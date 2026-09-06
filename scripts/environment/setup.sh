#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
REPOSITORY_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd -P)"
DEV=0
FFMPEG_DIRECTORY=""

usage() {
    cat <<'EOF'
Usage: setup.sh [--dev] [--ffmpeg-dir <directory>]

Creates a locked .venv with uv-managed Python. Use --dev to include test,
lint, and type-checking dependencies.
EOF
}

fail() {
    printf 'Error: %s\n' "$*" >&2
    exit 1
}

while (($# > 0)); do
    case "$1" in
        --dev)
            DEV=1
            shift
            ;;
        --ffmpeg-dir)
            (($# >= 2)) || fail "--ffmpeg-dir requires a directory."
            FFMPEG_DIRECTORY="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            fail "Unknown argument: $1"
            ;;
    esac
done

check_arguments=()
[[ -z "$FFMPEG_DIRECTORY" ]] || check_arguments+=(--ffmpeg-dir "$FFMPEG_DIRECTORY")
"$SCRIPT_DIR/check-prerequisites.sh" "${check_arguments[@]}"
if [[ -n "$FFMPEG_DIRECTORY" ]]; then
    FFMPEG_DIRECTORY="$(cd -- "$FFMPEG_DIRECTORY" && pwd -P)"
fi

cd -- "$REPOSITORY_ROOT"
python_version="$(tr -d '[:space:]' < .python-version)"
[[ -n "$python_version" ]] || fail ".python-version is empty."

printf 'Preparing uv-managed CPython %s...\n' "$python_version"
uv --managed-python python install "$python_version"

if ((DEV)); then
    printf 'Syncing .venv with development dependencies...\n'
    uv --managed-python sync --locked --group dev
else
    printf 'Syncing .venv with runtime dependencies...\n'
    uv --managed-python sync --locked --no-dev
fi

uv_python_directory="$(uv python dir)"
base_interpreter_directory="$(uv --managed-python run --locked --no-sync -- python -c 'import sys; print(sys.base_prefix)')"
case "$base_interpreter_directory" in
    "$uv_python_directory"|"$uv_python_directory"/*) ;;
    *) fail ".venv does not use a uv-managed Python. Rename or remove .venv, then run setup again." ;;
esac

if [[ -n "$FFMPEG_DIRECTORY" ]]; then
    export MOVIE_MAKER_FFMPEG_DIR="$FFMPEG_DIRECTORY"
fi

printf 'Checking the GUI runtime...\n'
uv --managed-python run --locked --no-sync -- movie-maker --check
printf 'Environment setup completed.\n'
printf 'Run the app with: bash ./scripts/environment/run.sh\n'
