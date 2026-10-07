"""Safe extraction of a zipped Shapefile.

The upload is untrusted, so nothing in the archive decides where bytes land on
disk: the selected members are copied to fixed flat names inside `dest`.
"""

import stat
import zipfile
import zlib
from pathlib import Path, PurePosixPath

REQUIRED_PARTS = (".shp", ".shx", ".dbf", ".prj")
OPTIONAL_PARTS = (".cpg",)
EXTRACTED_STEM = "data"

_COPY_CHUNK_BYTES = 1024 * 1024


class InvalidUpload(ValueError):
    """The uploaded file is rejected. The message is safe to show the client."""


def extract_shapefile(
    zip_path: Path, dest: Path, *, max_entries: int, max_uncompressed: int
) -> Path:
    """Returns path to the extracted .shp. Raises InvalidUpload on any problem."""
    if not zipfile.is_zipfile(zip_path):
        raise InvalidUpload("The file is not a valid zip archive.")
    try:
        with zipfile.ZipFile(zip_path) as archive:
            members = _check_archive(archive, max_entries, max_uncompressed)
            dest.mkdir(parents=True, exist_ok=True)
            budget = max_uncompressed
            for suffix, info in members.items():
                budget -= _copy_member(archive, info, dest / f"{EXTRACTED_STEM}{suffix}", budget)
    except (zipfile.BadZipFile, zlib.error, NotImplementedError, EOFError) as exc:
        # BadZipFile also covers CRC mismatches; zlib.error is damaged deflate data;
        # NotImplementedError is an unsupported compression method.
        raise InvalidUpload("The zip archive is corrupt or uses an unsupported format.") from exc
    return dest / f"{EXTRACTED_STEM}.shp"


def _check_archive(
    archive: zipfile.ZipFile, max_entries: int, max_uncompressed: int
) -> dict[str, zipfile.ZipInfo]:
    """Validate the archive and return its shapefile members keyed by suffix."""
    entries = archive.infolist()
    if len(entries) > max_entries:
        raise InvalidUpload(f"The zip archive has more than {max_entries} entries.")
    if sum(info.file_size for info in entries) > max_uncompressed:
        raise InvalidUpload("The zip archive is too large once uncompressed.")

    files: list[zipfile.ZipInfo] = []
    for info in entries:
        _reject_unsafe(info)
        path = PurePosixPath(info.filename)
        if info.is_dir() or path.parts[0] == "__MACOSX":
            continue
        files.append(info)

    shps = [info for info in files if info.filename.lower().endswith(".shp")]
    if not shps:
        raise InvalidUpload("The zip archive contains no .shp file.")
    if len(shps) > 1:
        raise InvalidUpload("The zip archive must contain exactly one .shp file.")

    # Sibling parts must share the .shp's directory and stem (any letter case).
    base = shps[0].filename[: -len(".shp")].lower()
    by_name = {info.filename.lower(): info for info in files}
    members: dict[str, zipfile.ZipInfo] = {}
    for suffix in REQUIRED_PARTS + OPTIONAL_PARTS:
        info = by_name.get(base + suffix)
        if info is not None:
            members[suffix] = info
        elif suffix == ".prj":
            raise InvalidUpload(
                "The shapefile has no .prj file, so its coordinate system is unknown."
            )
        elif suffix in REQUIRED_PARTS:
            raise InvalidUpload(f"The shapefile is missing its {suffix} file.")
    return members


def _reject_unsafe(info: zipfile.ZipInfo) -> None:
    name = info.filename.replace("\\", "/")
    path = PurePosixPath(name)
    if name.startswith("/") or (len(name) > 1 and name[1] == ":") or ".." in path.parts:
        raise InvalidUpload("The zip archive contains an unsafe path.")
    unix_mode = info.external_attr >> 16
    if stat.S_ISLNK(unix_mode):
        raise InvalidUpload("The zip archive contains a symbolic link.")


def _copy_member(archive: zipfile.ZipFile, info: zipfile.ZipInfo, target: Path, budget: int) -> int:
    """Copy one member to `target`, counting real bytes against `budget`.

    Header sizes can lie, so the cap is enforced on the decompressed stream.
    Returns the number of bytes written.
    """
    written = 0
    with archive.open(info) as src, target.open("wb") as out:
        while chunk := src.read(_COPY_CHUNK_BYTES):
            written += len(chunk)
            if written > budget:
                raise InvalidUpload("The zip archive is too large once uncompressed.")
            out.write(chunk)
    return written
