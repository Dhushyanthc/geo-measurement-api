"""Settings read from environment variables, with defaults for local runs."""

import os
from dataclasses import dataclass
from pathlib import Path

_MIB = 1024 * 1024


@dataclass(frozen=True)
class Settings:
    database_url: str = "sqlite:///./app.db"
    storage_dir: Path = Path("./storage")
    max_upload_mb: int = 25
    max_uncompressed_mb: int = 100
    max_zip_entries: int = 100

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * _MIB

    @property
    def max_uncompressed_bytes(self) -> int:
        return self.max_uncompressed_mb * _MIB

    @classmethod
    def from_env(cls) -> "Settings":
        """Build settings from the environment; unset variables keep their defaults."""
        defaults = cls()
        return cls(
            database_url=os.environ.get("DATABASE_URL", defaults.database_url),
            storage_dir=Path(os.environ.get("STORAGE_DIR", defaults.storage_dir)),
            max_upload_mb=int(os.environ.get("MAX_UPLOAD_MB", defaults.max_upload_mb)),
            max_uncompressed_mb=int(
                os.environ.get("MAX_UNCOMPRESSED_MB", defaults.max_uncompressed_mb)
            ),
            max_zip_entries=int(os.environ.get("MAX_ZIP_ENTRIES", defaults.max_zip_entries)),
        )
