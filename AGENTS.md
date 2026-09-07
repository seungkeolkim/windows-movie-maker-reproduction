# Repository Guidelines

## Project Structure & Module Organization

This repository has an executable Python/Qt environment scaffold; video editing features remain in the design phase. `README.md` defines the product scope, roadmap, setup, and quality goals. `docs/README.md` defines the documentation map and reading order, while `docs/decisions/` contains numbered Architecture Decision Records (ADRs), such as `adr-0001-python-uv-native-launcher.md`.

Python application code lives in `src/movie_maker/`, Windows and Linux environment helpers in `scripts/environment/`, and automated tests in `tests/`. Keep future script families in purpose-specific subdirectories instead of mixing them in `scripts/`. Add feature packages such as `ui/`, `timeline/`, `media/`, and `project/` as implementation progresses; reserve `launcher/` for the native Windows launcher. Keep new documentation close to its subject and add architectural decisions as sequentially numbered ADRs.

## Persistence & Database Constraints

Project files remain versioned JSON documents unless an approved ADR changes that boundary. Do not add both
SQLite and DuckDB, or otherwise introduce multiple file-based embedded database engines, for separate app
features. If local indexed storage becomes necessary, select one embedded engine for the application and
keep its schema, migration, backup, and recovery lifecycle together. Prefer SQLite for operational app data;
consider DuckDB only as a measured alternative that replaces or consolidates the embedded store. A distinct
server database such as PostgreSQL requires a separate multi-user or centralized-service need and a new ADR.

## Build, Test, and Development Commands

- `.\scripts\environment\setup.ps1 -FFmpegDirectory <bin-path>` — creates the locked Windows runtime environment.
- `.\scripts\environment\setup.ps1 -Dev -FFmpegDirectory <bin-path>` — also installs Windows development dependencies.
- `bash ./scripts/environment/setup.sh --ffmpeg-dir <bin-path>` — creates the equivalent Linux runtime environment; add `--dev` for development dependencies.
- Use the corresponding `scripts/environment/run.ps1` or `scripts/environment/run.sh` to start the application without changing the environment.
- `uv --managed-python run --locked --no-sync -- pytest` — runs tests after development setup.
- `uv --managed-python run --locked --no-sync -- ruff check .` — runs lint checks.
- `uv --managed-python run --locked --no-sync -- mypy` — checks source types.
- `git diff --check` — checks documentation and patches for whitespace errors.

When adding development or test commands, define them in `pyproject.toml` and document the exact invocation here and in `README.md`.

## Coding Style & Naming Conventions

Use four-space indentation and standard Python conventions: `snake_case` for modules, functions, and variables; `PascalCase` for classes; and `UPPER_SNAKE_CASE` for constants. Keep UI, timeline, media, and persistence concerns in their respective packages. Prefer small, typed interfaces between components. Ruff is configured for Python 3.13 with a 100-character line length; mypy checks `src/` in strict mode.

Write Markdown with descriptive headings, short paragraphs, fenced code blocks with language tags, and relative links. Preserve the repository's UTF-8 Korean documentation.

## Testing Guidelines

Tests use pytest and pytest-qt with the PySide6 API selected explicitly. New implementation work should add focused tests under `tests/`, mirroring the source package structure and using names such as `test_timeline_split.py`. No coverage threshold is set yet. Prioritize project-file safety, frame-accurate edits, FFmpeg argument construction, and launcher error handling.

## Commit & Pull Request Guidelines

History is brief, but the latest commit uses a Conventional Commit-style subject (`docs: define project roadmap and runtime architecture`). Prefer `type: concise imperative summary`, for example `feat: add media probe service` or `test: cover missing source recovery`.

Pull requests should explain the change, verification performed, and related issue or roadmap item. Include screenshots or recordings for UI changes, sample media details for compatibility changes, and a new ADR when revising an accepted architectural decision.
