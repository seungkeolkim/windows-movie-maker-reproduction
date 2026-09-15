"""Content-addressed, root-confined cache storage for regenerable media artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from threading import Lock


class CacheKind(str, Enum):
    THUMBNAIL = "thumbnail"
    WAVEFORM = "waveform"
    PROXY = "proxy"


_EXTENSIONS = {
    CacheKind.THUMBNAIL: ".png",
    CacheKind.WAVEFORM: ".waveform.json",
    CacheKind.PROXY: ".proxy.mp4",
}


@dataclass(frozen=True, slots=True)
class SourceIdentity:
    canonical_path: str
    size: int
    modified_ns: int
    device: int | None
    inode: int | None

    @classmethod
    def capture(cls, path: str | os.PathLike[str]) -> SourceIdentity:
        source = _canonical_path(Path(path))
        if not source.exists():
            raise FileNotFoundError(source)
        stat = source.stat()
        if not source.is_file():
            raise FileNotFoundError(source)
        return cls(
            str(source),
            stat.st_size,
            stat.st_mtime_ns,
            getattr(stat, "st_dev", None),
            getattr(stat, "st_ino", None),
        )


@dataclass(frozen=True, slots=True)
class CacheKey:
    kind: CacheKind
    digest: str
    source: SourceIdentity
    settings: tuple[tuple[str, str], ...]

    @classmethod
    def create(
        cls,
        kind: CacheKind,
        source_path: str | os.PathLike[str],
        settings: Mapping[str, str | int | float | bool],
    ) -> CacheKey:
        identity = SourceIdentity.capture(source_path)
        normalized = tuple(sorted((key, str(value)) for key, value in settings.items()))
        payload = json.dumps(
            {
                "kind": kind.value,
                "path": identity.canonical_path,
                "size": identity.size,
                "modified_ns": identity.modified_ns,
                "device": identity.device,
                "inode": identity.inode,
                "settings": normalized,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        return cls(kind, hashlib.sha256(payload).hexdigest(), identity, normalized)

    def source_is_current(self) -> bool:
        try:
            return SourceIdentity.capture(self.source.canonical_path) == self.source
        except OSError:
            return False


@dataclass(frozen=True, slots=True)
class CachePruneResult:
    removed: tuple[Path, ...]
    bytes_removed: int
    bytes_remaining: int


class MediaCache:
    """Publish verified files atomically and delete only owned inactive entries."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._active: dict[Path, int] = {}
        self._lock = Lock()

    def path_for(self, key: CacheKey) -> Path:
        return self.root / key.kind.value / key.digest[:2] / f"{key.digest}{_EXTENSIONS[key.kind]}"

    def get(self, key: CacheKey) -> Path | None:
        path = self.path_for(key)
        if key.source_is_current() and path.is_file() and path.stat().st_size > 0:
            try:
                os.utime(path, None)
            except OSError:
                pass
            return path
        return None

    def temporary_path(self, key: CacheKey) -> Path:
        target = self.path_for(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(
            dir=target.parent,
            prefix=f".{key.digest}.",
            suffix=f".partial{_EXTENSIONS[key.kind]}",
        )
        os.close(descriptor)
        return Path(name)

    def publish_bytes(self, key: CacheKey, payload: bytes) -> Path:
        if not payload:
            raise ValueError("Cache payload cannot be empty.")
        temporary = self.temporary_path(key)
        try:
            with temporary.open("wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            return self.publish_temporary(key, temporary)
        finally:
            temporary.unlink(missing_ok=True)

    def publish_temporary(self, key: CacheKey, temporary: Path) -> Path:
        target = self.path_for(key)
        self._require_inside_root(temporary)
        self._require_inside_root(target)
        if not key.source_is_current():
            raise RuntimeError("The source changed while its cache artifact was generated.")
        if not temporary.is_file() or temporary.stat().st_size <= 0:
            raise ValueError("Cache artifact is empty or missing.")
        with temporary.open("r+b") as stream:
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        return target

    def remove(self, key: CacheKey) -> bool:
        path = _canonical_path(self.path_for(key))
        self._require_inside_root(path)
        with self._lock:
            if path in self._active:
                return False
        try:
            path.unlink(missing_ok=True)
        except OSError:
            return False
        return True

    @contextmanager
    def lease(self, path: Path) -> Iterator[Path]:
        resolved = self.protect(path)
        try:
            yield resolved
        finally:
            self.unprotect(resolved)

    def protect(self, path: Path) -> Path:
        """Pin an owned cache file while a preview consumer can read it."""

        resolved = _canonical_path(path)
        self._require_inside_root(resolved)
        with self._lock:
            self._active[resolved] = self._active.get(resolved, 0) + 1
        return resolved

    def unprotect(self, path: Path) -> None:
        resolved = _canonical_path(path)
        self._require_inside_root(resolved)
        with self._lock:
            count = self._active.get(resolved, 0)
            if count <= 1:
                self._active.pop(resolved, None)
            else:
                self._active[resolved] = count - 1

    def prune(self, maximum_bytes: int) -> CachePruneResult:
        if maximum_bytes < 0:
            raise ValueError("Cache size limit cannot be negative.")
        if not self.root.exists():
            return CachePruneResult((), 0, 0)
        root = _canonical_path(self.root)
        with self._lock:
            active = set(self._active)
        entries: list[tuple[int, int, Path]] = []
        total = 0
        for path in root.rglob("*"):
            if not path.is_file() or ".partial" in path.name:
                continue
            try:
                resolved = _canonical_path(path)
                self._require_inside_root(resolved)
                stat = resolved.stat()
            except (OSError, ValueError):
                continue
            total += stat.st_size
            if resolved not in active:
                entries.append((stat.st_mtime_ns, stat.st_size, resolved))
        removed: list[Path] = []
        removed_bytes = 0
        for _mtime, size, path in sorted(entries):
            if total - removed_bytes <= maximum_bytes:
                break
            try:
                path.unlink()
            except OSError:
                continue
            removed.append(path)
            removed_bytes += size
        return CachePruneResult(tuple(removed), removed_bytes, total - removed_bytes)

    def cleanup_partials(self) -> tuple[Path, ...]:
        if not self.root.exists():
            return ()
        root = _canonical_path(self.root)
        removed: list[Path] = []
        for path in root.rglob("*.partial*"):
            try:
                resolved = _canonical_path(path)
                self._require_inside_root(resolved)
                resolved.unlink()
                removed.append(resolved)
            except (OSError, ValueError):
                continue
        return tuple(removed)

    def _require_inside_root(self, path: Path) -> None:
        root = _canonical_path(self.root)
        candidate = _canonical_path(path)
        try:
            common = Path(os.path.commonpath((root, candidate)))
        except ValueError as error:
            raise ValueError("Cache operation escaped the application cache root.") from error
        if os.path.normcase(str(common)) != os.path.normcase(str(root)):
            raise ValueError("Cache operation escaped the application cache root.")


def _canonical_path(path: Path) -> Path:
    value = os.path.normpath(os.path.realpath(os.path.abspath(path)))
    if os.name == "nt":
        if value.startswith("\\\\?\\UNC\\"):
            value = "\\\\" + value[8:]
        elif value.startswith("\\\\?\\"):
            value = value[4:]
    return Path(value)
