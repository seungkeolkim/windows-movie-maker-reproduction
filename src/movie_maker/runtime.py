"""Lifecycle owner for W-10 app data, autosave, and media cache workers."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from movie_maker.media import MediaArtifactGenerator, MediaCache, PriorityMediaQueue
from movie_maker.project import (
    ApplicationPaths,
    AutosaveCoordinator,
    AutosaveStore,
    RecentProjectsStore,
    RecoveryCandidate,
    SessionJournal,
)

DEFAULT_CACHE_LIMIT_BYTES = 5 * 1024 * 1024 * 1024


@dataclass(slots=True)
class W10Runtime:
    paths: ApplicationPaths
    session_journal: SessionJournal
    autosave_store: AutosaveStore
    autosave: AutosaveCoordinator
    recent_projects: RecentProjectsStore
    cache: MediaCache
    media_queue: PriorityMediaQueue
    artifacts: MediaArtifactGenerator
    import_executor: ThreadPoolExecutor
    recovery_candidates: tuple[RecoveryCandidate, ...]
    cache_limit_bytes: int = DEFAULT_CACHE_LIMIT_BYTES
    closed: bool = False

    @classmethod
    def create(cls, paths: ApplicationPaths | None = None) -> W10Runtime:
        paths = paths or ApplicationPaths.default()
        session_journal = SessionJournal(paths.session_marker_path)
        previous = session_journal.begin()
        if session_journal.current is None:
            raise RuntimeError("A current application session was not created.")
        autosave_store = AutosaveStore(
            paths.autosave_root,
            paths.recovery_quarantine_root,
        )
        cache = MediaCache(paths.media_cache_root)
        cache.cleanup_partials()
        cache.prune(DEFAULT_CACHE_LIMIT_BYTES)
        return cls(
            paths,
            session_journal,
            autosave_store,
            AutosaveCoordinator(autosave_store, session_journal.current.session_id),
            RecentProjectsStore(paths.recent_projects_path),
            cache,
            PriorityMediaQueue(cache, workers=2),
            MediaArtifactGenerator(cache),
            ThreadPoolExecutor(max_workers=1, thread_name_prefix="media-analysis"),
            autosave_store.discover(previous),
        )

    def prune_cache(self) -> None:
        self.cache.prune(self.cache_limit_bytes)

    def close(self, *, clean_exit: bool) -> None:
        if self.closed:
            return
        self.autosave.shutdown()
        self.media_queue.shutdown()
        self.import_executor.shutdown(wait=False, cancel_futures=True)
        if clean_exit:
            self.session_journal.mark_clean()
        self.closed = True
