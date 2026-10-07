import io
import struct
import zipfile
from pathlib import Path

import pyproj
import pytest

from app.core.ziputil import InvalidUpload, extract_shapefile
from tests.factories import from_utm, shapefile_parts, shapefile_zip, utm_square, zip_bytes

MAX_ENTRIES = 20
MAX_UNCOMPRESSED = 1024 * 1024

PARTS = shapefile_parts([from_utm(utm_square(1000))], pyproj.CRS.from_epsg(4326))


def extract(tmp_path: Path, content: bytes) -> Path:
    zip_path = tmp_path / "upload.zip"
    zip_path.write_bytes(content)
    return extract_shapefile(
        zip_path,
        tmp_path / "extracted",
        max_entries=MAX_ENTRIES,
        max_uncompressed=MAX_UNCOMPRESSED,
    )


def rejection(tmp_path: Path, content: bytes) -> str:
    with pytest.raises(InvalidUpload) as excinfo:
        extract(tmp_path, content)
    return str(excinfo.value)


def test_extracts_parts_to_fixed_flat_names(tmp_path: Path) -> None:
    shp = extract(tmp_path, shapefile_zip(PARTS, stem="My Plots"))

    assert shp == tmp_path / "extracted" / "data.shp"
    extracted = sorted(p.name for p in shp.parent.iterdir())
    assert extracted == ["data.dbf", "data.prj", "data.shp", "data.shx"]
    assert shp.read_bytes() == PARTS[".shp"]


def test_finds_shapefile_in_subfolder_with_any_case(tmp_path: Path) -> None:
    members = {f"survey/Plots{suffix.upper()}": data for suffix, data in PARTS.items()}

    shp = extract(tmp_path, zip_bytes(members))

    assert shp.read_bytes() == PARTS[".shp"]


def test_copies_optional_cpg(tmp_path: Path) -> None:
    shp = extract(tmp_path, shapefile_zip({**PARTS, ".cpg": b"UTF-8"}))

    assert (shp.parent / "data.cpg").read_bytes() == b"UTF-8"


def test_ignores_macosx_metadata_and_directories(tmp_path: Path) -> None:
    members = {
        "plots/": b"",
        "__MACOSX/._plots.shp": b"resource fork",
        **{f"plots{suffix}": data for suffix, data in PARTS.items()},
    }

    shp = extract(tmp_path, zip_bytes(members))

    assert shp.read_bytes() == PARTS[".shp"]


def test_missing_prj_has_its_own_message(tmp_path: Path) -> None:
    parts = {k: v for k, v in PARTS.items() if k != ".prj"}

    assert "no .prj" in rejection(tmp_path, shapefile_zip(parts))


@pytest.mark.parametrize("suffix", [".shx", ".dbf"])
def test_missing_required_part(tmp_path: Path, suffix: str) -> None:
    parts = {k: v for k, v in PARTS.items() if k != suffix}

    assert f"missing its {suffix}" in rejection(tmp_path, shapefile_zip(parts))


def test_parts_must_share_the_shp_folder(tmp_path: Path) -> None:
    members = {f"plots{suffix}": data for suffix, data in PARTS.items() if suffix != ".dbf"}
    members["other/plots.dbf"] = PARTS[".dbf"]

    assert "missing its .dbf" in rejection(tmp_path, zip_bytes(members))


def test_two_shapefiles_rejected(tmp_path: Path) -> None:
    members = {
        **{f"a{suffix}": data for suffix, data in PARTS.items()},
        **{f"b{suffix}": data for suffix, data in PARTS.items()},
    }

    assert "exactly one .shp" in rejection(tmp_path, zip_bytes(members))


def test_no_shapefile_rejected(tmp_path: Path) -> None:
    assert "no .shp" in rejection(tmp_path, zip_bytes({"readme.txt": b"hello"}))


@pytest.mark.parametrize("content", [b"not a zip at all", b""])
def test_non_zip_rejected(tmp_path: Path, content: bytes) -> None:
    assert "not a valid zip" in rejection(tmp_path, content)


def test_corrupt_member_rejected(tmp_path: Path) -> None:
    content = bytearray(shapefile_zip(PARTS))
    # Flip bytes inside the first member's compressed data (after its 30-byte
    # local header and file name) so the CRC check fails during extraction.
    data_start = 30 + len("plots.shp")
    for i in range(data_start, data_start + 16):
        content[i] ^= 0xFF

    assert "corrupt" in rejection(tmp_path, bytes(content))


@pytest.mark.parametrize("name", ["../evil.shp", "a/../../evil.shp", "/etc/evil.shp", "C:/x.shp"])
def test_unsafe_paths_rejected(tmp_path: Path, name: str) -> None:
    members = {name: PARTS[".shp"], **{f"plots{s}": d for s, d in PARTS.items() if s != ".shp"}}

    assert "unsafe path" in rejection(tmp_path, zip_bytes(members))
    assert not (tmp_path.parent / "evil.shp").exists()


def test_symlink_entry_rejected(tmp_path: Path) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for suffix, data in PARTS.items():
            archive.writestr(f"plots{suffix}", data)
        link = zipfile.ZipInfo("link.txt")
        link.external_attr = 0o120777 << 16  # S_IFLNK | rwx
        archive.writestr(link, "/etc/passwd")

    assert "symbolic link" in rejection(tmp_path, buffer.getvalue())


def test_too_many_entries_rejected(tmp_path: Path) -> None:
    members = {f"junk{i}.txt": b"" for i in range(MAX_ENTRIES + 1)}

    assert "more than" in rejection(tmp_path, zip_bytes(members))


def test_zip_bomb_rejected_by_declared_size(tmp_path: Path) -> None:
    # Zeros deflate roughly 1000:1, so the archive is tiny but declares > cap.
    members = {**{f"plots{s}": d for s, d in PARTS.items()}, "pad.bin": bytes(MAX_UNCOMPRESSED)}

    assert "too large" in rejection(tmp_path, zip_bytes(members))


def test_member_lying_about_its_size_rejected(tmp_path: Path) -> None:
    # Rewrite the declared uncompressed size of the .shp to 1 byte in both the
    # local header and the central directory; the real data is larger. zipfile
    # stops at the declared size, so the CRC check fails; the byte count while
    # copying is a second backstop that does not rely on that behaviour.
    content = bytearray(shapefile_zip(PARTS))
    struct.pack_into("<I", content, 22, 1)
    central = content.index(b"PK\x01\x02")
    struct.pack_into("<I", content, central + 24, 1)

    assert "corrupt" in rejection(tmp_path, bytes(content))
