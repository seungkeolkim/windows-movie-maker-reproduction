"""Typed in-memory state used by the interactive design mock-up."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class MediaKind(str, Enum):
    """Media categories shown in the library."""

    VIDEO = "영상"
    PHOTO = "사진"
    AUDIO = "오디오"


class AssetStatus(str, Enum):
    """Availability of a referenced source file."""

    READY = "준비됨"
    MISSING = "누락"
    ERROR = "읽기 오류"


class TrackKind(str, Enum):
    """Fixed Movie Maker-style tracks."""

    VISUAL = "영상·사진"
    MUSIC = "음악"
    NARRATION = "내레이션"
    TEXT = "텍스트"


class ExportState(str, Enum):
    """States of the simulated export job."""

    CLOSED = "닫힘"
    CONFIG = "설정"
    RUNNING = "진행 중"
    CANCELLING = "취소 중"
    CANCELLED = "취소됨"
    COMPLETE = "완료"
    FAILED = "실패"


@dataclass(slots=True)
class MockAsset:
    """A library presentation item for real W-02 media or later-stage mock data."""

    asset_id: str
    name: str
    kind: MediaKind
    duration_ms: int | None
    width: int | None
    height: int | None
    color: str
    source_path: str
    status: AssetStatus = AssetStatus.READY
    proxy_enabled: bool = False
    proxy_status: str = "사용 안 함"
    thumbnail_png: bytes | None = None
    thumbnail_error: str | None = None
    is_real_media: bool = False

    @property
    def resolution_text(self) -> str:
        if self.width is None or self.height is None:
            return "해상도 없음"
        return f"{self.width}×{self.height}"


@dataclass(slots=True)
class MockClip:
    """A non-destructive reference to a library item or generated text."""

    clip_id: str
    track: TrackKind
    asset_id: str | None
    label: str
    start_ms: int
    duration_ms: int
    source_in_ms: int = 0
    source_out_ms: int | None = None
    speed: float = 1.0
    volume: int = 100
    muted: bool = False
    fit_mode: str = "맞춤"
    rotation: int = 0
    effect: str = "없음"
    fade_in_ms: int = 0
    fade_out_ms: int = 0
    ducking: str = "꺼짐"
    text_kind: str | None = None
    text_content: str = ""
    text_font: str = "맑은 고딕"
    text_size: int = 32
    text_bold: bool = True
    text_color: str = "흰색"
    text_alignment: str = "가운데"
    text_position: str = "아래"
    text_animation: str = "없음"


@dataclass(slots=True)
class MockProjectState:
    """Single source of truth for the interactive mock-up."""

    project_name: str = "제목 없음"
    project_path: str | None = None
    is_dirty: bool = False
    assets: dict[str, MockAsset] = field(default_factory=dict)
    visual_clips: list[MockClip] = field(default_factory=list)
    music_clips: list[MockClip] = field(default_factory=list)
    narration_clips: list[MockClip] = field(default_factory=list)
    text_clips: list[MockClip] = field(default_factory=list)
    transitions: dict[str, str] = field(default_factory=dict)
    canvas_mode: str = "원본 유지"
    canvas_width: int | None = None
    canvas_height: int | None = None
    reference_asset_id: str | None = None
    selected_asset_id: str | None = None
    selected_clip_id: str | None = None
    selected_clip_ids: list[str] = field(default_factory=list)
    playhead_ms: int = 0
    is_playing: bool = False
    preview_muted: bool = False
    timeline_mode: str = "타임라인"
    timeline_zoom: int = 100
    export_state: ExportState = ExportState.CLOSED
    export_progress: int = 0
    export_preset: str = "원본 유지"
    export_path: str = r"C:\Videos\제주 여행 목업.mp4"
    export_framerate: str = "원본"
    export_quality: str = "권장"
    pending_export_error: str | None = None
    export_error: str | None = None
    import_warning: str | None = None
    status_message: str = "준비됨 · 인터랙티브 목업"

    @property
    def all_clips(self) -> list[MockClip]:
        return [
            *self.visual_clips,
            *self.music_clips,
            *self.narration_clips,
            *self.text_clips,
        ]

    @property
    def total_duration_ms(self) -> int:
        if not self.visual_clips:
            return 0
        return max(clip.start_ms + clip.duration_ms for clip in self.visual_clips)


@dataclass(slots=True)
class MockHistoryEntry:
    """Whole-state snapshots are acceptable only inside this disposable mock-up."""

    label: str
    before: MockProjectState
    after: MockProjectState
