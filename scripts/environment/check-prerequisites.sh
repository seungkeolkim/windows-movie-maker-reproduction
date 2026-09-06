#!/usr/bin/env bash

set -Eeuo pipefail

readonly MINIMUM_UV_VERSION="0.12.1"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
REPOSITORY_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd -P)"
FFMPEG_DIRECTORY=""

usage() {
    cat <<'EOF'
Usage: check-prerequisites.sh [--ffmpeg-dir <directory>]

Checks Linux, uv, ffmpeg, ffprobe, required encoders, and required filters.
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
        -h|--help)
            usage
            exit 0
            ;;
        *)
            fail "Unknown argument: $1"
            ;;
    esac
done

[[ "$(uname -s)" == "Linux" ]] || fail "This Bash setup supports Linux only. Use the .ps1 scripts on Windows."
case "$(uname -m)" in
    x86_64|aarch64) ;;
    *) fail "A 64-bit x86_64 or aarch64 Linux system is required." ;;
esac

command -v uv >/dev/null 2>&1 || fail "uv was not found. Install it from https://docs.astral.sh/uv/getting-started/installation/."
uv_version_text="$(uv --version)"
uv_version="$(awk '{print $2}' <<<"$uv_version_text")"
[[ "$uv_version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || fail "Unable to determine the uv version: $uv_version_text"
oldest_version="$(printf '%s\n' "$MINIMUM_UV_VERSION" "$uv_version" | sort -V | head -n 1)"
[[ "$oldest_version" == "$MINIMUM_UV_VERSION" ]] || fail "uv $MINIMUM_UV_VERSION or newer is required; found $uv_version."

if [[ -n "$FFMPEG_DIRECTORY" && ! -d "$FFMPEG_DIRECTORY" ]]; then
    fail "The specified FFmpeg directory does not exist: $FFMPEG_DIRECTORY"
fi

resolve_executable() {
    local name="$1"
    local candidate
    local -a directories=()

    [[ -z "$FFMPEG_DIRECTORY" ]] || directories+=("$FFMPEG_DIRECTORY")
    [[ -z "${MOVIE_MAKER_FFMPEG_DIR:-}" ]] || directories+=("$MOVIE_MAKER_FFMPEG_DIR")
    directories+=("$REPOSITORY_ROOT/tools/ffmpeg/bin")

    for directory in "${directories[@]}"; do
        candidate="$directory/$name"
        if [[ -x "$candidate" ]]; then
            readlink -f -- "$candidate"
            return 0
        fi
    done

    command -v "$name" 2>/dev/null || return 1
}

ffmpeg_path="$(resolve_executable ffmpeg || true)"
ffprobe_path="$(resolve_executable ffprobe || true)"
if [[ -z "$ffmpeg_path" || -z "$ffprobe_path" ]]; then
    fail "FFmpeg and ffprobe were not both found. Install your distribution's FFmpeg package, then add its bin directory to PATH, set MOVIE_MAKER_FFMPEG_DIR, pass --ffmpeg-dir, or use tools/ffmpeg/bin."
fi

ffmpeg_parent="$(cd -- "$(dirname -- "$ffmpeg_path")" && pwd -P)"
ffprobe_parent="$(cd -- "$(dirname -- "$ffprobe_path")" && pwd -P)"
[[ "$ffmpeg_parent" == "$ffprobe_parent" ]] || fail "ffmpeg and ffprobe were found in different directories. Use executables from the same FFmpeg build."

ffmpeg_version_output="$("$ffmpeg_path" -hide_banner -version 2>&1)"
ffmpeg_version="${ffmpeg_version_output%%$'\n'*}"
ffprobe_version_output="$("$ffprobe_path" -hide_banner -version 2>&1)"
ffprobe_version="${ffprobe_version_output%%$'\n'*}"
encoders="$("$ffmpeg_path" -hide_banner -encoders 2>&1)"
grep -Eq '[[:space:]](libx264|libopenh264)[[:space:]]' <<<"$encoders" || fail "A software H.264 encoder is required. Install an FFmpeg build containing libx264 or libopenh264."
grep -Eq '[[:space:]]aac[[:space:]]' <<<"$encoders" || fail "An FFmpeg build containing the AAC encoder is required."

filters="$("$ffmpeg_path" -hide_banner -filters 2>&1)"
required_filters=(
    trim atrim setpts asetpts concat scale crop pad fps aresample volume afade amix
    xfade acrossfade drawtext
)
missing_filters=()
for filter in "${required_filters[@]}"; do
    grep -Eq "[[:space:]]${filter}[[:space:]]" <<<"$filters" || missing_filters+=("$filter")
done
((${#missing_filters[@]} == 0)) || fail "Required FFmpeg filters are missing: ${missing_filters[*]}"

printf 'Prerequisite check passed.\n'
printf '  uv:      %s\n' "$uv_version"
printf '  ffmpeg:  %s\n' "$ffmpeg_version"
printf '  ffprobe: %s\n' "$ffprobe_version"
printf '  path:    %s\n' "$ffmpeg_parent"
