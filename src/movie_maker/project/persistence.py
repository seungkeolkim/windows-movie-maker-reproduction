"""Versioned JSON project documents and atomic file replacement."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from movie_maker.project.model import (
    CURRENT_PROJECT_SCHEMA_VERSION,
    Canvas,
    Clip,
    MediaKind,
    MediaReference,
    MediaStream,
    MediaStreamKind,
    MediaTimeBase,
    PlaybackRate,
    Project,
    ProjectValidationError,
    TimelineTrack,
    TrackKind,
)
from movie_maker.project.time import FrameRate, ProjectTime

type JsonObject = dict[str, Any]
type ReplaceFile = Callable[[str, str], None]


class ProjectPersistenceError(RuntimeError):
    """Base error for project document I/O."""


class ProjectReadError(ProjectPersistenceError):
    """Raised when a project cannot be completely read and validated."""


class ProjectWriteError(ProjectPersistenceError):
    """Raised when a project cannot be atomically committed."""


class InvalidProjectDocument(ProjectReadError):
    """Raised when JSON structure or domain values are invalid."""


class UnsupportedProjectVersion(ProjectReadError):
    """Raised when a document uses a schema this application cannot read."""


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise InvalidProjectDocument(f"{label} must be a JSON object.")
    if any(not isinstance(key, str) for key in value):
        raise InvalidProjectDocument(f"{label} keys must be strings.")
    return value


def _sequence(value: object, label: str) -> Sequence[object]:
    if not isinstance(value, list):
        raise InvalidProjectDocument(f"{label} must be a JSON array.")
    return value


def _required(value: Mapping[str, object], key: str, label: str) -> object:
    if key not in value:
        raise InvalidProjectDocument(f"{label}.{key} is required.")
    return value[key]


def _string(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise InvalidProjectDocument(f"{label} must be a string.")
    return value


def _integer(value: object, label: str) -> int:
    if type(value) is not int:
        raise InvalidProjectDocument(f"{label} must be an integer.")
    return value


def _optional_integer(value: object, label: str) -> int | None:
    if value is None:
        return None
    return _integer(value, label)


def _ratio_document(numerator: int, denominator: int) -> JsonObject:
    return {"numerator": numerator, "denominator": denominator}


def _parse_ratio(value: object, label: str) -> tuple[int, int]:
    ratio = _mapping(value, label)
    return (
        _integer(_required(ratio, "numerator", label), f"{label}.numerator"),
        _integer(_required(ratio, "denominator", label), f"{label}.denominator"),
    )


def _stream_to_document(stream: MediaStream) -> JsonObject:
    result: JsonObject = {
        "index": stream.index,
        "kind": stream.kind.value,
        "codec_name": stream.codec_name,
        "time_base": _ratio_document(
            stream.time_base.numerator,
            stream.time_base.denominator,
        ),
        "start_pts": stream.start_pts,
        "duration_ts": stream.duration_ts,
        "average_frame_rate": None,
        "sample_rate": stream.sample_rate,
    }
    if stream.average_frame_rate is not None:
        result["average_frame_rate"] = _ratio_document(
            stream.average_frame_rate.numerator,
            stream.average_frame_rate.denominator,
        )
    return result


def _media_to_document(media: MediaReference) -> JsonObject:
    return {
        "asset_id": media.asset_id,
        "name": media.name,
        "source_path": media.source_path,
        "kind": media.kind.value,
        "duration_ns": media.duration.nanoseconds if media.duration is not None else None,
        "width": media.width,
        "height": media.height,
        "primary_stream_index": media.primary_stream_index,
        "streams": [_stream_to_document(stream) for stream in media.streams],
    }


def _clip_to_document(clip: Clip) -> JsonObject:
    return {
        "clip_id": clip.clip_id,
        "asset_id": clip.asset_id,
        "label": clip.label,
        "timeline_start_ns": clip.timeline_start.nanoseconds,
        "duration_ns": clip.duration.nanoseconds,
        "source_in_ns": clip.source_in.nanoseconds,
        "source_out_ns": (
            clip.source_out.nanoseconds if clip.source_out is not None else None
        ),
        "playback_rate": _ratio_document(
            clip.playback_rate.numerator,
            clip.playback_rate.denominator,
        ),
    }


def project_to_document(project: Project) -> JsonObject:
    """Return the complete persistent JSON value for a validated project."""

    return {
        "schema_version": project.schema_version,
        "project_id": project.project_id,
        "name": project.name,
        "canvas": {
            "width": project.canvas.width,
            "height": project.canvas.height,
            "reference_asset_id": project.canvas.reference_asset_id,
        },
        "media": [_media_to_document(media) for media in project.media],
        "timeline": {
            track.kind.value: [_clip_to_document(clip) for clip in track.clips]
            for track in project.tracks
        },
    }


def _parse_stream(value: object, label: str) -> MediaStream:
    stream = _mapping(value, label)
    time_base = _parse_ratio(_required(stream, "time_base", label), f"{label}.time_base")
    average_value = stream.get("average_frame_rate")
    average_frame_rate: FrameRate | None = None
    if average_value is not None:
        average = _parse_ratio(average_value, f"{label}.average_frame_rate")
        average_frame_rate = FrameRate(*average)
    return MediaStream(
        index=_integer(_required(stream, "index", label), f"{label}.index"),
        kind=MediaStreamKind(
            _string(_required(stream, "kind", label), f"{label}.kind")
        ),
        codec_name=_string(
            _required(stream, "codec_name", label), f"{label}.codec_name"
        ),
        time_base=MediaTimeBase(*time_base),
        start_pts=_optional_integer(stream.get("start_pts"), f"{label}.start_pts"),
        duration_ts=_optional_integer(stream.get("duration_ts"), f"{label}.duration_ts"),
        average_frame_rate=average_frame_rate,
        sample_rate=_optional_integer(stream.get("sample_rate"), f"{label}.sample_rate"),
    )


def _parse_media(value: object, index: int) -> MediaReference:
    label = f"media[{index}]"
    media = _mapping(value, label)
    duration_ns = _optional_integer(media.get("duration_ns"), f"{label}.duration_ns")
    streams_value = media.get("streams", [])
    streams = tuple(
        _parse_stream(stream, f"{label}.streams[{stream_index}]")
        for stream_index, stream in enumerate(_sequence(streams_value, f"{label}.streams"))
    )
    return MediaReference(
        asset_id=_string(_required(media, "asset_id", label), f"{label}.asset_id"),
        name=_string(_required(media, "name", label), f"{label}.name"),
        source_path=_string(
            _required(media, "source_path", label), f"{label}.source_path"
        ),
        kind=MediaKind(_string(_required(media, "kind", label), f"{label}.kind")),
        duration=ProjectTime(duration_ns) if duration_ns is not None else None,
        width=_optional_integer(media.get("width"), f"{label}.width"),
        height=_optional_integer(media.get("height"), f"{label}.height"),
        primary_stream_index=_optional_integer(
            media.get("primary_stream_index"), f"{label}.primary_stream_index"
        ),
        streams=streams,
    )


def _parse_clip(value: object, track: TrackKind, index: int) -> Clip:
    label = f"timeline.{track.value}[{index}]"
    clip = _mapping(value, label)
    playback = _parse_ratio(
        _required(clip, "playback_rate", label), f"{label}.playback_rate"
    )
    source_out_ns = _optional_integer(clip.get("source_out_ns"), f"{label}.source_out_ns")
    asset_value = clip.get("asset_id")
    if asset_value is not None:
        asset_value = _string(asset_value, f"{label}.asset_id")
    return Clip(
        clip_id=_string(_required(clip, "clip_id", label), f"{label}.clip_id"),
        track=track,
        asset_id=asset_value,
        label=_string(_required(clip, "label", label), f"{label}.label"),
        timeline_start=ProjectTime(
            _integer(
                _required(clip, "timeline_start_ns", label),
                f"{label}.timeline_start_ns",
            )
        ),
        duration=ProjectTime(
            _integer(_required(clip, "duration_ns", label), f"{label}.duration_ns")
        ),
        source_in=ProjectTime(
            _integer(_required(clip, "source_in_ns", label), f"{label}.source_in_ns")
        ),
        source_out=ProjectTime(source_out_ns) if source_out_ns is not None else None,
        playback_rate=PlaybackRate(*playback),
    )


def project_from_document(value: object) -> Project:
    """Parse and fully validate an in-memory JSON project value."""

    try:
        document = _mapping(value, "project")
        schema_version = _integer(
            _required(document, "schema_version", "project"), "project.schema_version"
        )
        if schema_version != CURRENT_PROJECT_SCHEMA_VERSION:
            raise UnsupportedProjectVersion(
                f"Unsupported project schema version: {schema_version}."
            )
        canvas_value = _mapping(_required(document, "canvas", "project"), "canvas")
        reference_asset_id = canvas_value.get("reference_asset_id")
        if reference_asset_id is not None:
            reference_asset_id = _string(reference_asset_id, "canvas.reference_asset_id")
        timeline = _mapping(_required(document, "timeline", "project"), "timeline")
        tracks = tuple(
            TimelineTrack(
                kind=kind,
                clips=tuple(
                    _parse_clip(clip, kind, index)
                    for index, clip in enumerate(
                        _sequence(
                            _required(timeline, kind.value, "timeline"),
                            f"timeline.{kind.value}",
                        )
                    )
                ),
            )
            for kind in TrackKind
        )
        return Project(
            schema_version=schema_version,
            project_id=_string(
                _required(document, "project_id", "project"), "project.project_id"
            ),
            name=_string(_required(document, "name", "project"), "project.name"),
            canvas=Canvas(
                width=_optional_integer(canvas_value.get("width"), "canvas.width"),
                height=_optional_integer(canvas_value.get("height"), "canvas.height"),
                reference_asset_id=reference_asset_id,
            ),
            media=tuple(
                _parse_media(media, index)
                for index, media in enumerate(
                    _sequence(_required(document, "media", "project"), "media")
                )
            ),
            tracks=tracks,
        )
    except UnsupportedProjectVersion:
        raise
    except InvalidProjectDocument:
        raise
    except (TypeError, ValueError, ProjectValidationError) as error:
        raise InvalidProjectDocument(str(error)) from error


class ProjectFileStore:
    """Load validated projects and atomically replace project files."""

    def __init__(self, *, replace_file: ReplaceFile = os.replace) -> None:
        self._replace_file = replace_file

    def load(self, path: str | os.PathLike[str]) -> Project:
        """Read, parse, and validate a complete project without side effects."""

        source = Path(path)
        try:
            text = source.read_text(encoding="utf-8")
            value = json.loads(text)
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ProjectReadError(f"프로젝트 파일을 읽을 수 없습니다: {source}") from error
        return project_from_document(value)

    def save(self, project: Project, path: str | os.PathLike[str]) -> None:
        """Write, flush, fsync, and atomically replace one project document."""

        target = Path(path)
        temporary_path: Path | None = None
        try:
            target.parent.mkdir(parents=False, exist_ok=True)
            descriptor, temporary_name = tempfile.mkstemp(
                dir=target.parent,
                prefix=f".{target.name}.",
                suffix=".tmp",
            )
            temporary_path = Path(temporary_name)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(
                    project_to_document(project),
                    stream,
                    ensure_ascii=False,
                    indent=2,
                )
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            self._replace_file(str(temporary_path), str(target))
            temporary_path = None
        except (OSError, TypeError, ValueError) as error:
            raise ProjectWriteError(f"프로젝트 파일을 저장할 수 없습니다: {target}") from error
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass
