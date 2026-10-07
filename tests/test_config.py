from pathlib import Path

import pytest

from app.config import Settings


def test_defaults_without_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "DATABASE_URL",
        "STORAGE_DIR",
        "MAX_UPLOAD_MB",
        "MAX_UNCOMPRESSED_MB",
        "MAX_ZIP_ENTRIES",
    ):
        monkeypatch.delenv(name, raising=False)

    assert Settings.from_env() == Settings()
    assert Settings().max_upload_bytes == 25 * 1024 * 1024


def test_environment_overrides_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@db:5432/geo")
    monkeypatch.setenv("STORAGE_DIR", "/data/storage")
    monkeypatch.setenv("MAX_UPLOAD_MB", "5")
    monkeypatch.setenv("MAX_UNCOMPRESSED_MB", "50")
    monkeypatch.setenv("MAX_ZIP_ENTRIES", "10")

    settings = Settings.from_env()

    assert settings.database_url == "postgresql+psycopg://u:p@db:5432/geo"
    assert settings.storage_dir == Path("/data/storage")
    assert settings.max_upload_bytes == 5 * 1024 * 1024
    assert settings.max_uncompressed_bytes == 50 * 1024 * 1024
    assert settings.max_zip_entries == 10
