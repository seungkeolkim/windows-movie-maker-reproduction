"""Owned per-user storage locations for recoverable and regenerable app data."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_cache_path, user_data_path, user_log_path


@dataclass(frozen=True, slots=True)
class ApplicationPaths:
    """Separate durable recovery data from disposable caches and diagnostics."""

    data_root: Path
    cache_root: Path
    log_root: Path

    @classmethod
    def default(cls) -> ApplicationPaths:
        return cls(
            Path(user_data_path("MovieMakerReproduction", "OpenAI", roaming=True)),
            Path(user_cache_path("MovieMakerReproduction", "OpenAI")),
            Path(user_log_path("MovieMakerReproduction", "OpenAI")),
        )

    @property
    def autosave_root(self) -> Path:
        return self.data_root / "autosaves"

    @property
    def recovery_quarantine_root(self) -> Path:
        return self.data_root / "recovery-quarantine"

    @property
    def session_marker_path(self) -> Path:
        return self.data_root / "session.json"

    @property
    def recent_projects_path(self) -> Path:
        return self.data_root / "recent-projects.json"

    @property
    def media_cache_root(self) -> Path:
        return self.cache_root / "media"
