"""Deterministic, immutable commands for the fixed project timeline."""

from __future__ import annotations

from dataclasses import dataclass, replace
from fractions import Fraction

from movie_maker.project.commands import CommandApplication, CommandRejected
from movie_maker.project.model import (
    NORMAL_PLAYBACK_RATE,
    ZERO_TIME,
    AudioLevel,
    Canvas,
    Clip,
    MediaKind,
    MediaReference,
    MediaStream,
    MediaStreamKind,
    PlaybackRate,
    Project,
    ProjectValidationError,
    TimelineTrack,
    TrackKind,
)
from movie_maker.project.time import ProjectTime

MIN_SPLIT_DURATION = ProjectTime.from_milliseconds(250)
MIN_TIMED_CLIP_DURATION = ProjectTime.from_milliseconds(500)
MIN_PHOTO_DURATION = ProjectTime.from_seconds(1)
DEFAULT_PHOTO_DURATION = ProjectTime.from_seconds(5)
MAX_PHOTO_DURATION = ProjectTime.from_seconds(30)


def source_span_duration(
    source_in: ProjectTime,
    source_out: ProjectTime,
    playback_rate: PlaybackRate,
) -> ProjectTime:
    """Return the deterministic project duration for one source range."""

    if not isinstance(source_in, ProjectTime) or not isinstance(source_out, ProjectTime):
        raise TypeError("Source boundaries must be ProjectTime values.")
    if not isinstance(playback_rate, PlaybackRate):
        raise TypeError("Playback rate must be a PlaybackRate value.")
    if source_in.nanoseconds < 0 or source_out <= source_in:
        raise ValueError("Source range must have a non-negative start and positive length.")
    source_seconds = (source_out - source_in).to_fractional_seconds()
    return ProjectTime.from_seconds(source_seconds / playback_rate.fraction)


def _nearest_non_negative_integer(value: Fraction) -> int:
    if value < 0:
        raise ValueError("Boundary index lookup requires a non-negative value.")
    quotient, remainder = divmod(value.numerator, value.denominator)
    return quotient + (1 if remainder * 2 >= value.denominator else 0)


def _primary_stream(media: MediaReference) -> MediaStream | None:
    if media.primary_stream_index is None:
        return None
    return next(
        (stream for stream in media.streams if stream.index == media.primary_stream_index),
        None,
    )


def _snap_to_units(time: ProjectTime, units_per_second: Fraction) -> ProjectTime:
    index = _nearest_non_negative_integer(
        time.to_fractional_seconds() * units_per_second
    )
    return ProjectTime.from_seconds(Fraction(index, 1) / units_per_second)


def snap_source_time(media: MediaReference, requested: ProjectTime) -> ProjectTime:
    """Snap a media-relative time to the best available deterministic source boundary."""

    if not isinstance(media, MediaReference):
        raise TypeError("Source boundary snapping requires a MediaReference value.")
    if not isinstance(requested, ProjectTime):
        raise TypeError("Requested boundary must be a ProjectTime value.")
    if requested.nanoseconds < 0:
        raise ValueError("Source boundary cannot be negative.")
    if media.duration is not None and requested > media.duration:
        raise ValueError("Source boundary cannot exceed the media duration.")
    if requested == ZERO_TIME or requested == media.duration:
        return requested

    primary = _primary_stream(media)
    snapped = requested
    if (
        primary is not None
        and primary.kind is MediaStreamKind.VIDEO
        and primary.average_frame_rate is not None
    ):
        snapped = _snap_to_units(
            requested,
            primary.average_frame_rate.frames_per_second,
        )
    elif (
        primary is not None
        and primary.kind is MediaStreamKind.AUDIO
        and primary.sample_rate is not None
    ):
        snapped = _snap_to_units(requested, Fraction(primary.sample_rate, 1))
    elif primary is not None:
        snapped = _snap_to_units(
            requested,
            Fraction(1, 1) / primary.time_base.seconds_per_tick,
        )

    if snapped < ZERO_TIME:
        return ZERO_TIME
    if media.duration is not None and snapped > media.duration:
        return media.duration
    return snapped


def _reflow_visual(clips: tuple[Clip, ...]) -> tuple[Clip, ...]:
    cursor = ZERO_TIME
    result: list[Clip] = []
    for clip in clips:
        reflowed = replace(clip, timeline_start=cursor)
        result.append(reflowed)
        cursor = reflowed.timeline_end
    return tuple(result)


def _replace_track(
    project: Project,
    kind: TrackKind,
    clips: tuple[Clip, ...],
    *,
    canvas: Canvas | None = None,
) -> Project:
    if kind is TrackKind.VISUAL:
        clips = _reflow_visual(clips)
    try:
        replacement = TimelineTrack(kind, clips)
        tracks = tuple(
            replacement if track.kind is kind else track for track in project.tracks
        )
        return replace(project, tracks=tracks, canvas=canvas or project.canvas)
    except ProjectValidationError as error:
        raise CommandRejected(str(error)) from error


def _locate_clip(project: Project, clip_id: str) -> tuple[TimelineTrack, int, Clip]:
    for track in project.tracks:
        for index, clip in enumerate(track.clips):
            if clip.clip_id == clip_id:
                return track, index, clip
    raise CommandRejected("타임라인에서 클립을 찾을 수 없습니다.")


def _media_for_clip(project: Project, clip: Clip) -> MediaReference:
    if clip.asset_id is None:
        raise CommandRejected("미디어를 참조하지 않는 클립은 이 명령으로 편집할 수 없습니다.")
    try:
        return project.media_reference(clip.asset_id)
    except KeyError as error:
        raise CommandRejected("클립이 참조하는 미디어를 찾을 수 없습니다.") from error


def _ensure_unique_clip_id(project: Project, clip_id: str) -> None:
    try:
        project.clip(clip_id)
    except KeyError:
        return
    raise CommandRejected("이미 사용 중인 클립 ID입니다.")


@dataclass(frozen=True, slots=True)
class _ReplaceTimelineRange:
    track: TrackKind
    index: int
    expected: tuple[Clip, ...]
    replacement: tuple[Clip, ...]
    history_label: str
    expected_canvas: Canvas | None = None
    replacement_canvas: Canvas | None = None

    @property
    def label(self) -> str:
        return self.history_label

    def apply(self, project: Project) -> CommandApplication:
        if type(self.index) is not int or self.index < 0:
            raise CommandRejected("클립 위치가 유효하지 않습니다.")
        track = project.track(self.track)
        end = self.index + len(self.expected)
        if end > len(track.clips) or track.clips[self.index : end] != self.expected:
            raise CommandRejected("편집 대상 클립이 명령 준비 이후 변경되었습니다.")
        if self.expected_canvas is not None and project.canvas != self.expected_canvas:
            raise CommandRejected("프로젝트 화면 기준이 명령 준비 이후 변경되었습니다.")

        clips = (*track.clips[: self.index], *self.replacement, *track.clips[end:])
        canvas = (
            self.replacement_canvas
            if self.expected_canvas is not None and self.replacement_canvas is not None
            else project.canvas
        )
        next_project = _replace_track(project, self.track, clips, canvas=canvas)
        inserted = next_project.track(self.track).clips[
            self.index : self.index + len(self.replacement)
        ]
        inverse = _ReplaceTimelineRange(
            track=self.track,
            index=self.index,
            expected=inserted,
            replacement=self.expected,
            history_label=self.history_label,
            expected_canvas=canvas if self.expected_canvas is not None else None,
            replacement_canvas=(
                self.expected_canvas if self.expected_canvas is not None else None
            ),
        )
        return CommandApplication(next_project, inverse)


@dataclass(frozen=True, slots=True)
class _ReplaceOneClip:
    expected: Clip
    replacement: Clip
    history_label: str

    @property
    def label(self) -> str:
        return self.history_label

    def apply(self, project: Project) -> CommandApplication:
        track, index, current = _locate_clip(project, self.expected.clip_id)
        if current != self.expected:
            raise CommandRejected("편집 대상 클립이 명령 준비 이후 변경되었습니다.")
        if self.replacement.clip_id != current.clip_id:
            raise CommandRejected("속성 변경은 클립 ID를 바꿀 수 없습니다.")
        if self.replacement.track is not current.track:
            raise CommandRejected("속성 변경은 클립 트랙을 바꿀 수 없습니다.")

        clips = [*track.clips]
        clips[index] = self.replacement
        if track.kind is not TrackKind.VISUAL:
            clips.sort(key=lambda clip: clip.timeline_start)
        next_project = _replace_track(project, track.kind, tuple(clips))
        next_clip = next_project.clip(current.clip_id)
        return CommandApplication(
            next_project,
            _ReplaceOneClip(next_clip, current, self.history_label),
        )


@dataclass(frozen=True, slots=True)
class AddMediaClip:
    """Create and append a correctly routed clip for one project media reference."""

    asset_id: str
    clip_id: str
    narration: bool = False
    timeline_start: ProjectTime | None = None
    label_text: str | None = None
    photo_duration: ProjectTime = DEFAULT_PHOTO_DURATION

    @property
    def label(self) -> str:
        return "타임라인에 클립 추가"

    def apply(self, project: Project) -> CommandApplication:
        if type(self.narration) is not bool:
            raise CommandRejected("내레이션 선택 값이 유효하지 않습니다.")
        _ensure_unique_clip_id(project, self.clip_id)
        try:
            media = project.media_reference(self.asset_id)
        except KeyError as error:
            raise CommandRejected("보관함에서 추가할 미디어를 찾을 수 없습니다.") from error

        if media.kind in {MediaKind.VIDEO, MediaKind.PHOTO}:
            if self.narration:
                raise CommandRejected("영상과 사진은 내레이션 트랙에 추가할 수 없습니다.")
            if self.timeline_start is not None:
                raise CommandRejected("시각 클립은 순차 트랙의 끝에만 추가할 수 있습니다.")
            track_kind = TrackKind.VISUAL
        else:
            track_kind = TrackKind.NARRATION if self.narration else TrackKind.MUSIC

        if media.kind is MediaKind.PHOTO:
            if not MIN_PHOTO_DURATION <= self.photo_duration <= MAX_PHOTO_DURATION:
                raise CommandRejected("사진 표시 시간은 1초에서 30초 사이여야 합니다.")
            source_out = None
            duration = self.photo_duration
        else:
            if media.duration is None:
                raise CommandRejected("원본 길이가 없는 미디어는 추가할 수 없습니다.")
            source_out = media.duration
            duration = source_span_duration(ZERO_TIME, source_out, NORMAL_PLAYBACK_RATE)

        track = project.track(track_kind)
        if track_kind is TrackKind.VISUAL:
            start = project.duration
            index = len(track.clips)
        else:
            start = self.timeline_start or max(
                (clip.timeline_end for clip in track.clips),
                default=ZERO_TIME,
            )
            if start.nanoseconds < 0:
                raise CommandRejected("보조 트랙 클립 시작은 0보다 작을 수 없습니다.")
            index = next(
                (
                    current
                    for current, existing in enumerate(track.clips)
                    if existing.timeline_start > start
                ),
                len(track.clips),
            )

        clip = Clip(
            clip_id=self.clip_id,
            track=track_kind,
            asset_id=media.asset_id,
            label=self.label_text or media.name.rsplit(".", maxsplit=1)[0],
            timeline_start=start,
            duration=duration,
            source_out=source_out,
        )
        next_canvas = project.canvas
        if (
            track_kind is TrackKind.VISUAL
            and not track.clips
            and project.canvas.width is None
            and media.width is not None
            and media.height is not None
        ):
            next_canvas = Canvas(media.width, media.height, media.asset_id)

        command = _ReplaceTimelineRange(
            track=track_kind,
            index=index,
            expected=(),
            replacement=(clip,),
            history_label=self.label,
            expected_canvas=(project.canvas if next_canvas != project.canvas else None),
            replacement_canvas=(next_canvas if next_canvas != project.canvas else None),
        )
        return command.apply(project)


@dataclass(frozen=True, slots=True)
class MoveVisualClip:
    """Move one visual clip to a new order index and deterministically ripple starts."""

    clip_id: str
    target_index: int
    expected: Clip | None = None
    history_label: str = "클립 이동"

    @property
    def label(self) -> str:
        return self.history_label

    def apply(self, project: Project) -> CommandApplication:
        track, current_index, clip = _locate_clip(project, self.clip_id)
        if track.kind is not TrackKind.VISUAL:
            raise CommandRejected("영상 또는 사진 클립만 앞뒤로 이동할 수 있습니다.")
        if self.expected is not None and clip != self.expected:
            raise CommandRejected("이동할 클립이 명령 준비 이후 변경되었습니다.")
        if type(self.target_index) is not int or not 0 <= self.target_index < len(track.clips):
            raise CommandRejected("클립을 이동할 위치가 범위를 벗어났습니다.")
        if self.target_index == current_index:
            raise CommandRejected("클립이 이미 요청한 위치에 있습니다.")

        reordered = list(track.clips)
        reordered.pop(current_index)
        reordered.insert(self.target_index, clip)
        next_project = _replace_track(project, TrackKind.VISUAL, tuple(reordered))
        inverse = MoveVisualClip(
            self.clip_id,
            current_index,
            expected=next_project.clip(self.clip_id),
            history_label=self.history_label,
        )
        return CommandApplication(next_project, inverse)


@dataclass(frozen=True, slots=True)
class DeleteTimelineClip:
    """Delete one clip without deleting its source-media reference."""

    clip_id: str

    @property
    def label(self) -> str:
        return "클립 삭제"

    def apply(self, project: Project) -> CommandApplication:
        track, index, clip = _locate_clip(project, self.clip_id)
        command = _ReplaceTimelineRange(
            track=track.kind,
            index=index,
            expected=(clip,),
            replacement=(),
            history_label=self.label,
        )
        return command.apply(project)


def _split_source_position(
    media: MediaReference,
    clip: Clip,
    timeline_position: ProjectTime,
) -> ProjectTime:
    if timeline_position <= clip.timeline_start or timeline_position >= clip.timeline_end:
        raise CommandRejected("재생 위치를 클립 양 끝이 아닌 내부로 옮기세요.")
    timeline_offset = timeline_position - clip.timeline_start
    source_offset = ProjectTime.from_seconds(
        timeline_offset.to_fractional_seconds() * clip.playback_rate.fraction
    )
    try:
        return snap_source_time(media, clip.source_in + source_offset)
    except (TypeError, ValueError) as error:
        raise CommandRejected(str(error)) from error


@dataclass(frozen=True, slots=True)
class SplitClip:
    """Split one timed clip at a snapped media boundary."""

    clip_id: str
    timeline_position: ProjectTime
    trailing_clip_id: str

    @property
    def label(self) -> str:
        return "클립 분할"

    def apply(self, project: Project) -> CommandApplication:
        track, index, clip = _locate_clip(project, self.clip_id)
        media = _media_for_clip(project, clip)
        if media.kind is MediaKind.PHOTO or track.kind is TrackKind.TEXT:
            raise CommandRejected("사진과 텍스트는 분할할 수 없습니다.")
        if clip.source_out is None:
            raise CommandRejected("원본 끝이 없는 클립은 분할할 수 없습니다.")
        _ensure_unique_clip_id(project, self.trailing_clip_id)
        split_source = _split_source_position(media, clip, self.timeline_position)
        if not clip.source_in < split_source < clip.source_out:
            raise CommandRejected("스냅한 분할점이 클립의 유효한 내부 경계가 아닙니다.")

        front_duration = source_span_duration(
            clip.source_in,
            split_source,
            clip.playback_rate,
        )
        back_duration = source_span_duration(
            split_source,
            clip.source_out,
            clip.playback_rate,
        )
        if front_duration < MIN_SPLIT_DURATION or back_duration < MIN_SPLIT_DURATION:
            raise CommandRejected("분할 뒤 양쪽 클립은 각각 0.25초 이상이어야 합니다.")

        front = replace(clip, duration=front_duration, source_out=split_source)
        back = replace(
            clip,
            clip_id=self.trailing_clip_id,
            label=f"{clip.label} (뒤)",
            timeline_start=front.timeline_end,
            duration=back_duration,
            source_in=split_source,
        )
        command = _ReplaceTimelineRange(
            track=track.kind,
            index=index,
            expected=(clip,),
            replacement=(front, back),
            history_label=self.label,
        )
        return command.apply(project)


@dataclass(frozen=True, slots=True)
class UpdateClipTiming:
    """Atomically apply all persistent timing values from one property submission."""

    clip_id: str
    source_in: ProjectTime | None = None
    source_out: ProjectTime | None = None
    photo_duration: ProjectTime | None = None
    playback_rate: PlaybackRate | None = None
    timeline_start: ProjectTime | None = None
    audio_level: AudioLevel | None = None
    audio_muted: bool | None = None
    history_label: str = "클립 속성 적용"

    @property
    def label(self) -> str:
        return self.history_label

    def apply(self, project: Project) -> CommandApplication:
        track, _, clip = _locate_clip(project, self.clip_id)
        media = _media_for_clip(project, clip)

        if media.kind is MediaKind.PHOTO:
            if self.source_in is not None or self.source_out is not None:
                raise CommandRejected("사진에는 원본 시작과 끝을 지정할 수 없습니다.")
            if self.playback_rate is not None:
                raise CommandRejected("사진에는 재생 속도를 지정할 수 없습니다.")
            if self.audio_level is not None or self.audio_muted is not None:
                raise CommandRejected("사진에는 오디오 속성을 지정할 수 없습니다.")
            if self.photo_duration is None:
                raise CommandRejected("변경할 사진 표시 시간을 입력하세요.")
            if not MIN_PHOTO_DURATION <= self.photo_duration <= MAX_PHOTO_DURATION:
                raise CommandRejected("사진 표시 시간은 1초에서 30초 사이여야 합니다.")
            replacement = replace(
                clip,
                duration=self.photo_duration,
                source_in=ZERO_TIME,
                source_out=None,
                playback_rate=NORMAL_PLAYBACK_RATE,
            )
        else:
            if self.photo_duration is not None:
                raise CommandRejected("영상과 오디오에는 사진 표시 시간을 지정할 수 없습니다.")
            if self.playback_rate is not None and media.kind is not MediaKind.VIDEO:
                raise CommandRejected("재생 속도는 영상 클립에서만 변경할 수 있습니다.")
            if self.playback_rate is not None and not isinstance(
                self.playback_rate, PlaybackRate
            ):
                raise CommandRejected("재생 속도가 유효한 유리수가 아닙니다.")
            if clip.source_out is None:
                raise CommandRejected("원본 끝이 없는 클립은 트리밍할 수 없습니다.")

            try:
                source_in = (
                    snap_source_time(media, self.source_in)
                    if self.source_in is not None
                    else clip.source_in
                )
                source_out = (
                    snap_source_time(media, self.source_out)
                    if self.source_out is not None
                    else clip.source_out
                )
            except (TypeError, ValueError) as error:
                raise CommandRejected(str(error)) from error
            if source_out <= source_in:
                raise CommandRejected("원본 끝은 원본 시작보다 뒤여야 합니다.")
            rate = self.playback_rate or clip.playback_rate
            duration = source_span_duration(source_in, source_out, rate)
            if duration < MIN_TIMED_CLIP_DURATION:
                raise CommandRejected("트리밍과 속도 변경 뒤 클립은 0.5초 이상이어야 합니다.")
            replacement = replace(
                clip,
                source_in=source_in,
                source_out=source_out,
                duration=duration,
                playback_rate=rate,
            )

            if self.audio_level is not None or self.audio_muted is not None:
                if not (
                    media.kind is MediaKind.VIDEO
                    or track.kind is TrackKind.MUSIC
                ):
                    raise CommandRejected(
                        "오디오 속성은 영상 원본음과 음악 클립에서만 변경할 수 있습니다."
                    )
                if self.audio_level is not None and not isinstance(
                    self.audio_level, AudioLevel
                ):
                    raise CommandRejected("음량 값이 유효하지 않습니다.")
                if self.audio_muted is not None and type(self.audio_muted) is not bool:
                    raise CommandRejected("음소거 값이 유효하지 않습니다.")
                replacement = replace(
                    replacement,
                    audio_level=(
                        self.audio_level
                        if self.audio_level is not None
                        else clip.audio_level
                    ),
                    audio_muted=(
                        self.audio_muted
                        if self.audio_muted is not None
                        else clip.audio_muted
                    ),
                )

        if self.timeline_start is not None:
            if self.timeline_start.nanoseconds < 0:
                raise CommandRejected("클립 시작 위치는 0보다 작을 수 없습니다.")
            if track.kind is TrackKind.VISUAL and self.timeline_start != clip.timeline_start:
                raise CommandRejected("시각 클립 시작은 리플 순서에서 자동으로 계산됩니다.")
            replacement = replace(replacement, timeline_start=self.timeline_start)

        if replacement == clip:
            raise CommandRejected("입력한 값이 현재 클립 속성과 같습니다.")
        command = _ReplaceOneClip(clip, replacement, self.history_label)
        return command.apply(project)


@dataclass(frozen=True, slots=True)
class UpdateClipAudio:
    """Atomically change persisted level and mute without altering clip timing."""

    clip_id: str
    audio_level: AudioLevel | None = None
    audio_muted: bool | None = None

    @property
    def label(self) -> str:
        return "오디오 속성 적용"

    def apply(self, project: Project) -> CommandApplication:
        if self.audio_level is None and self.audio_muted is None:
            raise CommandRejected("변경할 오디오 속성을 입력하세요.")
        return UpdateClipTiming(
            self.clip_id,
            audio_level=self.audio_level,
            audio_muted=self.audio_muted,
            history_label=self.label,
        ).apply(project)


@dataclass(frozen=True, slots=True)
class TrimClipStart:
    clip_id: str
    source_in: ProjectTime

    @property
    def label(self) -> str:
        return "클립 시작 트리밍"

    def apply(self, project: Project) -> CommandApplication:
        return UpdateClipTiming(
            self.clip_id,
            source_in=self.source_in,
            history_label=self.label,
        ).apply(project)


@dataclass(frozen=True, slots=True)
class TrimClipEnd:
    clip_id: str
    source_out: ProjectTime

    @property
    def label(self) -> str:
        return "클립 끝 트리밍"

    def apply(self, project: Project) -> CommandApplication:
        return UpdateClipTiming(
            self.clip_id,
            source_out=self.source_out,
            history_label=self.label,
        ).apply(project)


@dataclass(frozen=True, slots=True)
class SetPhotoDuration:
    clip_id: str
    duration: ProjectTime

    @property
    def label(self) -> str:
        return "사진 표시 시간 변경"

    def apply(self, project: Project) -> CommandApplication:
        return UpdateClipTiming(
            self.clip_id,
            photo_duration=self.duration,
            history_label=self.label,
        ).apply(project)


@dataclass(frozen=True, slots=True)
class SetPlaybackRate:
    clip_id: str
    playback_rate: PlaybackRate

    @property
    def label(self) -> str:
        return "영상 재생 속도 변경"

    def apply(self, project: Project) -> CommandApplication:
        return UpdateClipTiming(
            self.clip_id,
            playback_rate=self.playback_rate,
            history_label=self.label,
        ).apply(project)
