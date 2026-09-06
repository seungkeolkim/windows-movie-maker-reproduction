from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QDialog, QFrame, QLabel, QListWidget, QPushButton, QWidget

from movie_maker.ui.dialogs import ExportSettingsDialog
from movie_maker.ui.main_window import MainWindow
from movie_maker.ui.mock_model import ExportState


def test_start_import_switches_to_editing_workspace(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    start_button = window.findChild(QPushButton, "E-START-IMPORT")

    assert start_button is not None
    assert window.preview_stack.currentWidget().objectName() == "S-START"

    qtbot.mouseClick(start_button, Qt.MouseButton.LeftButton)

    assert len(window.controller.state.assets) == 5
    assert window.preview_stack.currentWidget().objectName() == "S-PREVIEW"
    assert window.library_list.count() == 5


def test_library_filter_keeps_selection_state_but_changes_visible_items(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.controller.import_sample_media()
    window.show()

    window.library_filter.setCurrentText("오디오")

    assert window.controller.state.selected_asset_id == "media-beach"
    assert window.library_list.count() == 2
    assert all("오디오" in window.library_list.item(index).text() for index in range(2))

    window._import_sample_media()

    assert window.library_filter.currentText() == "전체"
    assert window.library_list.count() == 5


def test_timeline_selection_updates_preview_and_inspector(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.controller.load_sample_project()
    window.show()
    video_track = window.findChild(QListWidget, "E-TIMELINE-VIDEO-TRACK")

    assert video_track is not None
    video_track.setCurrentRow(1)

    assert window.controller.state.selected_clip_id == "clip-market"
    assert window.controller.state.playhead_ms == 8_000
    assert "야시장" in window.inspector_header.text()
    assert "야시장" in window.preview_canvas.text()


def test_required_screen_regions_fit_at_supported_window_sizes(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.controller.load_sample_project()
    window.show()

    for width, height in ((1280, 720), (1920, 1080)):
        window.resize(width, height)
        qtbot.wait(10)
        assert window.size().width() >= width
        assert window.size().height() >= height
        assert window.library_dock.isVisible()
        assert window.inspector_dock.isVisible()
        assert window.preview_canvas.width() >= 420
        timeline = window.findChild(QFrame, "S-TIMELINE")
        assert timeline is not None
        assert timeline.height() >= 280

    window.resize(1024, 640)
    qtbot.wait(10)
    assert window.library_dock.isVisible()
    assert not window.inspector_dock.isVisible()
    assert window.preview_canvas.width() >= 420


def test_detailed_mvp_and_one_zero_controls_exist_in_context(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.controller.load_sample_project()
    window.controller.select_clip("clip-market")
    window.show()

    required_names = {
        "E-CLIP-IN",
        "E-CLIP-OUT",
        "E-CLIP-DURATION",
        "E-CLIP-SPEED",
        "E-CLIP-SOURCE-VOLUME",
        "E-CLIP-SOURCE-MUTE",
        "E-CLIP-FIT",
        "E-CLIP-ROTATE",
        "E-CLIP-EFFECT",
        "E-MEDIA-PROXY",
        "E-MEDIA-PROXY-STATUS",
        "E-TIMELINE-TRIM-START",
        "E-TIMELINE-TRIM-END",
    }
    found = {
        child.objectName()
        for child in window.findChildren(QWidget)
        if child.objectName() in required_names
    }
    assert found == required_names

    window.controller.select_asset("media-market")
    proxy_button = window.findChild(QPushButton, "E-MEDIA-PROXY")
    assert proxy_button is not None
    assert proxy_button.isEnabled()
    qtbot.mouseClick(proxy_button, Qt.MouseButton.LeftButton)
    assert "준비됨" in window.media_proxy_status.text()
    assert "목업 프록시 1개" in window.library_count.text()


def test_ctrl_style_multi_selection_is_reflected_in_inspector(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.controller.load_sample_project()
    window.show()
    video_track = window.findChild(QListWidget, "E-TIMELINE-VIDEO-TRACK")

    assert video_track is not None
    video_track.setCurrentRow(1)
    video_track.item(0).setSelected(True)

    assert len(window.controller.state.selected_clip_ids) == 2
    assert "2개 선택" in window.inspector_header.text()


def test_export_dialog_explains_original_and_fixed_presets(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.controller.load_sample_project()
    dialog = ExportSettingsDialog(window.controller.state, window)
    qtbot.addWidget(dialog)
    dialog.show()
    preset = dialog.findChild(QComboBox, "E-EXPORT-PRESET")

    assert preset is not None
    assert "기준 미디어: 해변 산책.mp4" in dialog.original_summary.text()

    preset.setCurrentText("720p")

    assert "1280×720" in dialog.summary.text()
    assert "16:9" in dialog.summary.text()


def test_export_progress_panel_exposes_complete_and_failure_states(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.controller.load_sample_project()
    window.show()

    window.controller.start_export()
    for _ in range(20):
        window.controller.advance_export()

    assert window.controller.state.export_state is ExportState.COMPLETE
    assert window.export_panel.isVisible()
    assert window.export_progress.value() == 100
    assert "완료" in window.export_stage.text()

    window.controller.close_export_result()
    window.controller.reserve_export_failure("FFmpeg를 찾을 수 없습니다")
    window.controller.start_export()
    for _ in range(5):
        window.controller.advance_export()

    assert window.controller.state.export_state is ExportState.FAILED
    assert "FFmpeg" in window.export_stage.text()
    assert window.export_result_button.text() == "설정으로 돌아가기"


def test_mock_state_actions_open_named_supporting_screens(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()

    window._open_onboarding()
    window._open_recovery()
    window._open_narration()
    window._inject_and_show_missing()
    window._open_project_version_message()
    window._open_runtime()

    names = {
        dialog.objectName()
        for dialog in window.findChildren(QDialog)
        if dialog.isVisible()
    }
    assert {
        "S-MISSING-MEDIA",
        "S-NARRATION",
        "S-ONBOARDING",
        "S-RECOVERY",
        "S-RUNTIME",
    } <= names
    element_names = {widget.objectName() for widget in window.findChildren(QWidget)}
    assert {
        "E-MISSING-LIST",
        "E-MISSING-RELINK",
        "E-NARRATION-DEVICE",
        "E-NARRATION-RECORD",
        "E-ONBOARDING-STEPS",
        "E-PROJECT-VERSION-MESSAGE",
        "E-RECOVERY-COMPARISON",
    } <= element_names
    assert "원본 미디어 누락" in window.preview_canvas.text()


def test_empty_preview_explains_next_action(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.controller.import_sample_media()
    window.show()
    canvas = window.findChild(QLabel, "E-PREVIEW-CANVAS")

    assert canvas is not None
    assert "타임라인에 영상이나 사진을 추가" in canvas.text()
