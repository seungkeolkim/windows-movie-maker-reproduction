import base64
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QFileDialog, QLabel, QPushButton

from movie_maker.media import (
    MediaAnalysis,
    MediaAnalysisErrorCode,
    MediaAnalysisFailure,
    MediaAnalysisSuccess,
    MediaLibrary,
    ThumbnailErrorCode,
    ThumbnailFailure,
    ThumbnailSuccess,
)
from movie_maker.project import (
    CommandExecutor,
    FrameRate,
    MediaKind,
    MediaStream,
    MediaStreamKind,
    MediaTimeBase,
    Project,
    ProjectTime,
)
from movie_maker.ui.main_window import MainWindow
from movie_maker.ui.mock_controller import MockController

PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YA"
    "AAAASUVORK5CYII="
)


def _analysis(source_path: str, kind: MediaKind) -> MediaAnalysis:
    stream_kind = (
        MediaStreamKind.AUDIO if kind is MediaKind.AUDIO else MediaStreamKind.VIDEO
    )
    stream = MediaStream(
        index=0,
        kind=stream_kind,
        codec_name="aac" if kind is MediaKind.AUDIO else "h264",
        time_base=MediaTimeBase(1, 1_000),
        start_pts=0,
        duration_ts=5_000,
        average_frame_rate=FrameRate(30, 1) if stream_kind is MediaStreamKind.VIDEO else None,
        sample_rate=48_000 if stream_kind is MediaStreamKind.AUDIO else None,
    )
    path = Path(source_path)
    return MediaAnalysis(
        source_path=str(path.resolve(strict=False)),
        name=path.name,
        kind=kind,
        duration=None if kind is MediaKind.PHOTO else ProjectTime.from_seconds(5),
        width=1920 if kind is not MediaKind.AUDIO else None,
        height=1080 if kind is not MediaKind.AUDIO else None,
        primary_stream_index=0,
        streams=(stream,),
    )


class StubAnalyzer:
    def __init__(self, kinds_by_name, failure_names=()) -> None:
        self.kinds_by_name = kinds_by_name
        self.failure_names = set(failure_names)
        self.calls: list[str] = []

    def analyze(self, source_path):
        path = str(Path(source_path).resolve(strict=False))
        self.calls.append(path)
        name = Path(path).name
        if name in self.failure_names:
            return MediaAnalysisFailure(
                path,
                MediaAnalysisErrorCode.PROCESS_FAILED,
                "파일 헤더를 읽을 수 없습니다.",
            )
        return MediaAnalysisSuccess(_analysis(path, self.kinds_by_name[name]))


class StubThumbnailer:
    def __init__(self, failure_names=()) -> None:
        self.failure_names = set(failure_names)

    def create(self, analysis):
        if analysis.name in self.failure_names:
            return ThumbnailFailure(
                analysis.source_path,
                ThumbnailErrorCode.PROCESS_FAILED,
                "대표 프레임을 읽을 수 없습니다.",
            )
        return ThumbnailSuccess(analysis.source_path, PNG_BYTES)


def _controller(kinds_by_name, *, failure_names=(), thumbnail_failure_names=()):
    identifiers = iter(f"real-media-{index}" for index in range(1, 20))
    library = MediaLibrary(
        CommandExecutor(Project.empty(project_id="project-ui")),
        StubAnalyzer(kinds_by_name, failure_names),
        StubThumbnailer(thumbnail_failure_names),
        asset_id_factory=lambda: next(identifiers),
    )
    return MockController(library)


def test_controller_reports_partial_success_and_populates_core_and_views(tmp_path) -> None:
    video = tmp_path / "제주 영상.mp4"
    photo = tmp_path / "가족 사진.png"
    audio = tmp_path / "배경 음악.wav"
    broken = tmp_path / "손상 파일.mov"
    sources = (video, photo, audio, broken)
    for source in sources:
        source.write_bytes(f"original:{source.name}".encode())
    before = {source: (source.read_bytes(), source.stat().st_mtime_ns) for source in sources}
    controller = _controller(
        {
            video.name: MediaKind.VIDEO,
            photo.name: MediaKind.PHOTO,
            audio.name: MediaKind.AUDIO,
        },
        failure_names={broken.name},
        thumbnail_failure_names={photo.name},
    )

    report = controller.import_media_files(tuple(str(source) for source in sources))

    assert len(report.imported) == 3
    assert len(report.failures) == 1
    assert len(controller.state.assets) == 3
    assert {media.kind for media in controller.media_project.media} == set(MediaKind)
    assert controller.media_history_count == 3
    assert controller.state.selected_asset_id == "real-media-1"
    assert controller.state.is_dirty
    assert controller.state.import_warning is not None
    assert broken.name in controller.state.import_warning
    assert "파일 헤더" in controller.state.import_warning
    assert photo.name in controller.state.import_warning
    assert controller.state.assets["real-media-1"].thumbnail_png == PNG_BYTES
    assert controller.state.assets["real-media-2"].thumbnail_error is not None
    assert all((source.read_bytes(), source.stat().st_mtime_ns) == before[source] for source in sources)


def test_duplicate_and_cancelled_imports_leave_existing_project_unchanged(tmp_path) -> None:
    source = tmp_path / "duplicate video.mp4"
    source.write_bytes(b"original")
    controller = _controller({source.name: MediaKind.VIDEO})
    controller.import_media_files((str(source),))

    duplicate = controller.import_media_files((str(source),))
    assert len(duplicate.duplicates) == 1
    assert controller.media_history_count == 1
    assert controller.state.import_warning is not None
    assert "중복" in controller.state.import_warning
    before_assets = dict(controller.state.assets)
    before_project = controller.media_project
    before_warning = controller.state.import_warning
    before_dirty = controller.state.is_dirty

    cancelled = controller.import_media_files(())

    assert cancelled.cancelled
    assert controller.state.assets == before_assets
    assert controller.media_project == before_project
    assert controller.media_history_count == 1
    assert controller.state.import_warning == before_warning
    assert controller.state.is_dirty is before_dirty
    assert "취소" in controller.state.status_message


def test_unused_real_item_removal_keeps_source_file_and_removes_core_reference(tmp_path) -> None:
    source = tmp_path / "unused video.mp4"
    source.write_bytes(b"source bytes")
    before = (source.read_bytes(), source.stat().st_mtime_ns)
    controller = _controller({source.name: MediaKind.VIDEO})
    controller.import_media_files((str(source),))

    assert controller.remove_selected_asset()

    assert not controller.state.assets
    assert not controller.media_project.media
    assert controller.media_history_count == 2
    assert (source.read_bytes(), source.stat().st_mtime_ns) == before
    assert "원본 파일은 유지" in controller.state.status_message


def test_used_real_item_removal_is_refused_with_impact_count(tmp_path) -> None:
    source = tmp_path / "used video.mp4"
    source.write_bytes(b"source bytes")
    controller = _controller({source.name: MediaKind.VIDEO})
    controller.import_media_files((str(source),))
    asset_id = controller.state.selected_asset_id
    assert asset_id is not None
    assert controller.add_selected_to_timeline()
    controller.select_asset(asset_id)

    assert not controller.remove_selected_asset()

    assert asset_id in controller.state.assets
    assert controller.media_project.media_reference(asset_id).name == source.name
    assert controller.asset_usage_count(asset_id) == 1
    assert "관련 클립 1개" in controller.state.status_message
    assert source.read_bytes() == b"source bytes"


def test_main_window_import_action_uses_selected_files_and_shows_failures(qtbot, tmp_path) -> None:
    video = tmp_path / "selected video.mp4"
    broken = tmp_path / "selected broken.mov"
    video.write_bytes(b"video")
    broken.write_bytes(b"broken")
    controller = _controller(
        {video.name: MediaKind.VIDEO},
        failure_names={broken.name},
    )
    window = MainWindow(
        controller,
        media_file_selector=lambda: (str(video), str(broken)),
    )
    qtbot.addWidget(window)
    window.show()
    start_button = window.findChild(QPushButton, "E-START-IMPORT")
    assert start_button is not None

    qtbot.mouseClick(start_button, Qt.MouseButton.LeftButton)

    assert window.library_list.count() == 1
    assert video.name in window.library_list.item(0).text()
    assert not window.library_list.item(0).icon().isNull()
    assert window.import_warning.isVisible()
    assert broken.name in window.import_warning.text()
    assert window.preview_stack.currentWidget().objectName() == "S-PREVIEW"


def test_file_selection_cancel_is_visible_without_creating_project_state(qtbot) -> None:
    window = MainWindow(media_file_selector=lambda: ())
    qtbot.addWidget(window)
    window.show()
    start_button = window.findChild(QPushButton, "E-START-IMPORT")
    assert start_button is not None

    qtbot.mouseClick(start_button, Qt.MouseButton.LeftButton)

    assert not window.controller.state.assets
    assert not window.controller.state.is_dirty
    assert window.controller.media_history_count == 0
    assert "취소" in window.controller.state.status_message
    assert window.preview_stack.currentWidget().objectName() == "S-START"


def test_default_selector_uses_native_multi_file_dialog(monkeypatch, qtbot) -> None:
    captured: dict[str, object] = {}

    def select_files(parent, caption, directory, file_filter):
        captured.update(
            parent=parent,
            caption=caption,
            directory=directory,
            file_filter=file_filter,
        )
        return (["C:/Media/one.mp4", "C:/Media/two.wav"], "지원 미디어")

    monkeypatch.setattr(QFileDialog, "getOpenFileNames", select_files)
    window = MainWindow()
    qtbot.addWidget(window)

    selected = window._choose_media_files()

    assert selected == ["C:/Media/one.mp4", "C:/Media/two.wav"]
    assert captured["parent"] is window
    assert captured["caption"] == "미디어 가져오기"
    assert "*.mp4" in str(captured["file_filter"])
    assert "*.png" in str(captured["file_filter"])
    assert "*.wav" in str(captured["file_filter"])


def test_used_item_removal_dialog_explains_refusal_and_keeps_media(qtbot, tmp_path) -> None:
    source = tmp_path / "dialog used.mp4"
    source.write_bytes(b"video")
    controller = _controller({source.name: MediaKind.VIDEO})
    controller.import_media_files((str(source),))
    asset_id = controller.state.selected_asset_id
    assert asset_id is not None
    assert controller.add_selected_to_timeline()
    controller.select_asset(asset_id)
    window = MainWindow(controller, media_file_selector=lambda: ())
    qtbot.addWidget(window)
    window.show()

    window._request_remove_asset()

    dialogs = [dialog for dialog in window.findChildren(QDialog) if dialog.isVisible()]
    assert dialogs
    heading = dialogs[-1].findChild(QLabel, "dialogHeading")
    assert heading is not None
    assert "제거할 수 없습니다" in heading.text()
    assert asset_id in controller.state.assets
    assert controller.asset_usage_count(asset_id) == 1
