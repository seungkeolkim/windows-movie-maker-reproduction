#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
REPOSITORY_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd -P)"
RUNTIME_CONTRACT="$SCRIPT_DIR/runtime-contract.json"
FFMPEG_DIRECTORY=""
APP_ARGUMENTS=()

json_string_array() {
    local key="$1"
    awk -v key="\"$key\"" '
        index($0, key) { reading=1; next }
        reading && /]/ { exit }
        reading {
            value=$0
            gsub(/^[[:space:]]*"|",?[[:space:]]*$/, "", value)
            if (length(value)) print value
        }
    ' "$RUNTIME_CONTRACT"
}

mapfile -t RUN_ARGUMENTS < <(json_string_array run)

usage() {
    cat <<'EOF'
Usage: run.sh [--ffmpeg-dir <directory>] [-- <application arguments>]
EOF
}

fail() {
    printf 'Error: %s\n' "$*" >&2
    exit 1
}

while (($# > 0)); do
    case "$1" in
        --ffmpeg-dir)
            (($# >= 2)) || fail "--ffmpeg-dir requires a directory."
            FFMPEG_DIRECTORY="$2"
            shift 2
            ;;
        --)
            shift
            APP_ARGUMENTS=("$@")
            break
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            fail "Unknown script argument: $1. Put application arguments after --."
            ;;
    esac
done

[[ -d "$REPOSITORY_ROOT/.venv" ]] || fail ".venv does not exist. Run bash ./scripts/environment/setup.sh first."

check_arguments=()
[[ -z "$FFMPEG_DIRECTORY" ]] || check_arguments+=(--ffmpeg-dir "$FFMPEG_DIRECTORY")
"$SCRIPT_DIR/check-prerequisites.sh" "${check_arguments[@]}"

if [[ -n "$FFMPEG_DIRECTORY" ]]; then
    export MOVIE_MAKER_FFMPEG_DIR="$(cd -- "$FFMPEG_DIRECTORY" && pwd -P)"
fi

cd -- "$REPOSITORY_ROOT"
exec uv "${RUN_ARGUMENTS[@]}" "${APP_ARGUMENTS[@]}"
