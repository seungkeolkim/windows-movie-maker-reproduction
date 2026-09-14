"""Atomic W-09 project commands for creative properties."""

from __future__ import annotations

from dataclasses import dataclass, replace

from movie_maker.project.commands import (
    CommandApplication,
    CommandRejected,
    InsertClip,
)
from movie_maker.project.model import (
    ZERO_TIME,
    Brightness,
    Clip,
    FitMode,
    MediaKind,
    MediaReference,
    MixerSettings,
    Project,
    ProjectValidationError,
    TextAnimationPreset,
    TextKind,
    TextOverlay,
    TimelineTrack,
    TrackKind,
    Transition,
    TransitionPreset,
    UserRotation,
    VisualEffectPreset,
    transitions_for_tracks,
)
from movie_maker.project.time import ProjectTime

DEFAULT_TEXT_DURATION = ProjectTime.from_seconds(3)
DEFAULT_TRANSITION_DURATION = ProjectTime.from_milliseconds(750)
_TEXT_KIND_LABEL = {
    TextKind.TITLE: "제목",
    TextKind.CAPTION: "캡션",
    TextKind.CREDITS: "크레딧",
}


def _replace_clip(project: Project, expected: Clip, replacement: Clip) -> Project:
    track = project.track(expected.track)
    try:
        index = track.clips.index(expected)
    except ValueError as error:
        raise CommandRejected("The clip changed since the command was prepared.") from error
    clips = (*track.clips[:index], replacement, *track.clips[index + 1 :])
    tracks = tuple(
        TimelineTrack(track.kind, clips) if item.kind is track.kind else item
        for item in project.tracks
    )
    try:
        return replace(project, tracks=tracks)
    except ProjectValidationError as error:
        raise CommandRejected(str(error)) from error


@dataclass(frozen=True, slots=True)
class _ReplaceCreativeClip:
    expected: Clip
    replacement: Clip
    history_label: str

    @property
    def label(self) -> str:
        return self.history_label

    def apply(self, project: Project) -> CommandApplication:
        current = project.clip(self.expected.clip_id)
        if current != self.expected:
            raise CommandRejected("The clip changed since the command was prepared.")
        next_project = _replace_clip(project, self.expected, self.replacement)
        return CommandApplication(
            next_project,
            _ReplaceCreativeClip(self.replacement, self.expected, self.history_label),
        )


@dataclass(frozen=True, slots=True)
class UpdateVisualProperties:
    """Apply all visual properties from one inspector submission."""

    clip_id: str
    fit_mode: FitMode | None = None
    user_rotation: UserRotation | None = None
    brightness: Brightness | None = None
    effect_preset: VisualEffectPreset | None = None

    @property
    def label(self) -> str:
        return "시각 속성 적용"

    def apply(self, project: Project) -> CommandApplication:
        try:
            clip = project.clip(self.clip_id)
        except KeyError as error:
            raise CommandRejected("시각 클립을 찾을 수 없습니다.") from error
        if clip.track is not TrackKind.VISUAL:
            raise CommandRejected("시각 속성은 영상 또는 사진 클립에만 적용할 수 있습니다.")
        values = (self.fit_mode, self.user_rotation, self.brightness, self.effect_preset)
        if all(value is None for value in values):
            raise CommandRejected("변경할 시각 속성을 입력하세요.")
        try:
            replacement = replace(
                clip,
                fit_mode=self.fit_mode if self.fit_mode is not None else clip.fit_mode,
                user_rotation=(
                    self.user_rotation
                    if self.user_rotation is not None
                    else clip.user_rotation
                ),
                brightness=(
                    self.brightness if self.brightness is not None else clip.brightness
                ),
                effect_preset=(
                    self.effect_preset
                    if self.effect_preset is not None
                    else clip.effect_preset
                ),
            )
        except (TypeError, ProjectValidationError) as error:
            raise CommandRejected(str(error)) from error
        if replacement == clip:
            raise CommandRejected("입력한 값이 현재 시각 속성과 같습니다.")
        return _ReplaceCreativeClip(clip, replacement, self.label).apply(project)


def _default_text_start(
    project: Project,
    kind: TextKind,
    playhead: ProjectTime,
    duration: ProjectTime,
) -> ProjectTime:
    if kind is TextKind.TITLE:
        for clip in project.track(TrackKind.VISUAL).clips:
            if clip.timeline_start <= playhead < clip.timeline_end:
                return clip.timeline_start
        return ProjectTime.zero()
    if kind is TextKind.CREDITS:
        return max(ProjectTime.zero(), project.duration - duration)
    return min(playhead, max(ProjectTime.zero(), project.duration - duration))


def _default_text(kind: TextKind) -> TextOverlay:
    if kind is TextKind.TITLE:
        return TextOverlay(kind=kind, position=replace(TextOverlay().position, y=5_000))
    if kind is TextKind.CREDITS:
        return TextOverlay(
            kind=kind,
            position=replace(TextOverlay().position, y=9_000),
            animation=TextAnimationPreset.SCROLL_UP,
        )
    return TextOverlay(kind=kind)


@dataclass(frozen=True, slots=True)
class AddTextClip:
    """Create one typed text clip at the kind-specific default position."""

    clip_id: str
    kind: TextKind
    playhead: ProjectTime = ZERO_TIME
    duration: ProjectTime = DEFAULT_TEXT_DURATION

    @property
    def label(self) -> str:
        return {
            TextKind.TITLE: "제목 추가",
            TextKind.CAPTION: "캡션 추가",
            TextKind.CREDITS: "크레딧 추가",
        }.get(self.kind, "텍스트 추가")

    def apply(self, project: Project) -> CommandApplication:
        if not isinstance(self.kind, TextKind):
            raise CommandRejected("알 수 없는 텍스트 종류입니다.")
        if not isinstance(self.playhead, ProjectTime) or not isinstance(
            self.duration, ProjectTime
        ):
            raise CommandRejected("텍스트 시간 값이 유효하지 않습니다.")
        if not project.track(TrackKind.VISUAL).clips:
            raise CommandRejected("텍스트를 추가하려면 시각 클립이 필요합니다.")
        if self.duration.nanoseconds <= 0 or self.duration > project.duration:
            raise CommandRejected("텍스트 길이는 프로젝트 안의 양수여야 합니다.")
        start = _default_text_start(project, self.kind, self.playhead, self.duration)
        clip = Clip(
            clip_id=self.clip_id,
            track=TrackKind.TEXT,
            asset_id=None,
            label=f"{self.label.removesuffix(' 추가')} · 텍스트",
            timeline_start=start,
            duration=self.duration,
            text=_default_text(self.kind),
        )
        application = InsertClip(clip).apply(project)
        return CommandApplication(application.project, application.inverse)


@dataclass(frozen=True, slots=True)
class UpdateTextProperties:
    """Atomically replace content, style, layout, animation, and time."""

    clip_id: str
    text: TextOverlay
    timeline_start: ProjectTime
    duration: ProjectTime

    @property
    def label(self) -> str:
        return "텍스트 속성 적용"

    def apply(self, project: Project) -> CommandApplication:
        try:
            clip = project.clip(self.clip_id)
        except KeyError as error:
            raise CommandRejected("텍스트 클립을 찾을 수 없습니다.") from error
        if clip.track is not TrackKind.TEXT:
            raise CommandRejected("텍스트 속성은 텍스트 클립에만 적용할 수 있습니다.")
        if not isinstance(self.text, TextOverlay):
            raise CommandRejected("텍스트 속성 값이 유효하지 않습니다.")
        if self.timeline_start.nanoseconds < 0 or self.duration.nanoseconds <= 0:
            raise CommandRejected("텍스트 시작과 길이가 유효하지 않습니다.")
        if self.timeline_start + self.duration > project.duration:
            raise CommandRejected("텍스트 표시 구간은 프로젝트 길이를 넘을 수 없습니다.")
        replacement = replace(
            clip,
            label=(
                f"{_TEXT_KIND_LABEL[self.text.kind]} · "
                f"{self.text.content.strip() or '텍스트'}"
            ),
            timeline_start=self.timeline_start,
            duration=self.duration,
            text=self.text,
        )
        if replacement == clip:
            raise CommandRejected("입력한 값이 현재 텍스트 속성과 같습니다.")
        return _ReplaceCreativeClip(clip, replacement, self.label).apply(project)


@dataclass(frozen=True, slots=True)
class UpdateTransition:
    """Set or remove one adjacent-boundary transition."""

    left_clip_id: str
    right_clip_id: str
    preset: TransitionPreset | None
    duration: ProjectTime = DEFAULT_TRANSITION_DURATION

    @property
    def label(self) -> str:
        return "전환 속성 적용"

    def apply(self, project: Project) -> CommandApplication:
        boundary = (self.left_clip_id, self.right_clip_id)
        existing = next(
            (item for item in project.transitions if item.boundary == boundary),
            None,
        )
        if self.preset is None:
            if existing is None:
                raise CommandRejected("이 경계에는 제거할 전환이 없습니다.")
            replacement = tuple(item for item in project.transitions if item != existing)
        else:
            try:
                transition = Transition(*boundary, self.preset, self.duration)
                replacement = tuple(
                    item for item in project.transitions if item.boundary != boundary
                ) + (transition,)
                next_project = replace(project, transitions=replacement)
            except (TypeError, ProjectValidationError) as error:
                raise CommandRejected(str(error)) from error
            inverse = UpdateTransition(
                *boundary,
                existing.preset if existing is not None else None,
                existing.duration if existing is not None else self.duration,
            )
            return CommandApplication(next_project, inverse)
        try:
            next_project = replace(project, transitions=replacement)
        except ProjectValidationError as error:
            raise CommandRejected(str(error)) from error
        assert existing is not None
        return CommandApplication(
            next_project,
            UpdateTransition(*boundary, existing.preset, existing.duration),
        )


@dataclass(frozen=True, slots=True)
class UpdateMixerSettings:
    """Replace all project bus levels as one history entry."""

    settings: MixerSettings

    @property
    def label(self) -> str:
        return "전체 오디오 믹서 적용"

    def apply(self, project: Project) -> CommandApplication:
        if not isinstance(self.settings, MixerSettings):
            raise CommandRejected("전체 오디오 믹서 값이 유효하지 않습니다.")
        if self.settings == project.mixer:
            raise CommandRejected("입력한 값이 현재 전체 믹서와 같습니다.")
        try:
            next_project = replace(project, mixer=self.settings)
        except ProjectValidationError as error:
            raise CommandRejected(str(error)) from error
        return CommandApplication(next_project, UpdateMixerSettings(project.mixer))


@dataclass(frozen=True, slots=True)
class _RemoveNarrationMedia:
    reference: MediaReference
    clip: Clip

    @property
    def label(self) -> str:
        return "녹음 내레이션 제거"

    def apply(self, project: Project) -> CommandApplication:
        try:
            current = project.clip(self.clip.clip_id)
            current_reference = project.media_reference(self.reference.asset_id)
        except KeyError as error:
            raise CommandRejected("녹음 내레이션을 찾을 수 없습니다.") from error
        if current != self.clip or current_reference != self.reference:
            raise CommandRejected("녹음 내레이션이 명령 준비 뒤 변경됐습니다.")
        narration = project.track(TrackKind.NARRATION)
        clips = tuple(item for item in narration.clips if item.clip_id != self.clip.clip_id)
        tracks = tuple(
            TimelineTrack(TrackKind.NARRATION, clips)
            if item.kind is TrackKind.NARRATION
            else item
            for item in project.tracks
        )
        media = tuple(item for item in project.media if item.asset_id != self.reference.asset_id)
        try:
            next_project = replace(
                project,
                media=media,
                tracks=tracks,
                transitions=transitions_for_tracks(project.transitions, tracks),
            )
        except ProjectValidationError as error:
            raise CommandRejected(str(error)) from error
        return CommandApplication(
            next_project,
            AddRecordedNarration(self.reference, self.clip.clip_id, self.clip.timeline_start),
        )


@dataclass(frozen=True, slots=True)
class AddRecordedNarration:
    """Add one verified WAV reference and narration clip as one history entry."""

    reference: MediaReference
    clip_id: str
    timeline_start: ProjectTime

    @property
    def label(self) -> str:
        return "녹음 내레이션 추가"

    def apply(self, project: Project) -> CommandApplication:
        if self.reference.kind is not MediaKind.AUDIO or self.reference.duration is None:
            raise CommandRejected("녹음 내레이션은 길이가 확인된 오디오여야 합니다.")
        if any(item.asset_id == self.reference.asset_id for item in project.media):
            raise CommandRejected("같은 ID의 미디어 참조가 이미 있습니다.")
        if self.timeline_start.nanoseconds < 0:
            raise CommandRejected("내레이션 시작 위치는 0보다 작을 수 없습니다.")
        clip = Clip(
            clip_id=self.clip_id,
            track=TrackKind.NARRATION,
            asset_id=self.reference.asset_id,
            label=self.reference.name,
            timeline_start=self.timeline_start,
            duration=self.reference.duration,
            source_out=self.reference.duration,
        )
        narration = project.track(TrackKind.NARRATION)
        index = next(
            (
                current
                for current, item in enumerate(narration.clips)
                if item.timeline_start > clip.timeline_start
            ),
            len(narration.clips),
        )
        clips = (*narration.clips[:index], clip, *narration.clips[index:])
        tracks = tuple(
            TimelineTrack(TrackKind.NARRATION, clips)
            if item.kind is TrackKind.NARRATION
            else item
            for item in project.tracks
        )
        try:
            next_project = replace(
                project,
                media=(*project.media, self.reference),
                tracks=tracks,
            )
        except ProjectValidationError as error:
            raise CommandRejected(str(error)) from error
        return CommandApplication(next_project, _RemoveNarrationMedia(self.reference, clip))
