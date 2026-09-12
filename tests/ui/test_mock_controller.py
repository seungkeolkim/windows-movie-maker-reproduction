from pathlib import Path

from movie_maker.ui.mock_controller import MockController
from movie_maker.ui.mock_model import AssetStatus, ExportState, TrackKind


def test_sample_import_is_idempotent_and_recorded_as_one_edit() -> None:
    controller = MockController()

    controller.import_sample_media()

    assert len(controller.state.assets) == 5
    assert controller.state.selected_asset_id == "media-beach"
    assert controller.state.is_dirty
    assert controller.history_count == 1
    assert controller.undo_label == "5개 미디어 가져오기"

    controller.import_sample_media()

    assert len(controller.state.assets) == 5
    assert controller.history_count == 1


def test_media_is_added_to_fixed_tracks_and_original_canvas_is_locked() -> None:
    controller = MockController()
    controller.import_sample_media()
    controller.select_asset("media-beach")

    assert controller.add_selected_to_timeline()
    assert controller.state.canvas_mode == "원본 유지"
    assert (controller.state.canvas_width, controller.state.canvas_height) == (1920, 1080)
    assert controller.state.reference_asset_id == "media-beach"

    controller.select_asset("media-music")
    assert controller.add_selected_to_timeline()
    assert len(controller.state.music_clips) == 1
    assert controller.state.music_clips[0].track is TrackKind.MUSIC

    controller.select_asset("media-narration")
    assert controller.add_selected_to_timeline()
    assert len(controller.state.narration_clips) == 1
    assert controller.state.narration_clips[0].track is TrackKind.NARRATION


def test_split_undo_redo_and_history_branch_preserve_timeline_length() -> None:
    controller = MockController()
    controller.load_sample_project()
    controller.select_clip("clip-beach")
    controller.seek(4_000)

    assert controller.split_selected_clip()
    assert len(controller.state.visual_clips) == 4
    assert [clip.duration_ms for clip in controller.state.visual_clips[:2]] == [4_000, 4_000]
    assert controller.state.total_duration_ms == 19_000
    assert controller.selected_clip is not None
    assert controller.selected_clip.label.endswith("(뒤)")

    assert controller.undo()
    assert len(controller.state.visual_clips) == 3
    assert controller.can_redo

    assert controller.redo()
    assert len(controller.state.visual_clips) == 4
    assert not controller.can_redo

    assert controller.undo()
    controller.select_clip("clip-market")
    assert controller.delete_selected_clip()
    assert not controller.can_redo
    assert controller.history_position == controller.history_count


def test_multi_selection_deletes_compatible_clips_as_one_command() -> None:
    controller = MockController()
    controller.load_sample_project()
    controller.select_clips(["clip-beach", "clip-market"], "clip-market")

    assert len(controller.selected_clips) == 2
    assert controller.delete_selected_clip()
    assert [clip.clip_id for clip in controller.state.visual_clips] == ["clip-photo"]
    assert controller.undo_label == "클립 2개 삭제"

    assert controller.undo()
    assert [clip.clip_id for clip in controller.state.visual_clips] == [
        "clip-beach",
        "clip-market",
        "clip-photo",
    ]


def test_speed_change_uses_source_span_and_property_apply_is_one_command() -> None:
    controller = MockController()
    controller.load_sample_project()
    controller.select_clip("clip-market")
    before = controller.history_count

    assert controller.update_selected_clip(
        duration_ms=6_000,
        speed=2.0,
        volume=65,
        muted=True,
        fit_mode="채움",
        effect="따뜻하게",
    )

    clip = controller.selected_clip
    assert clip is not None
    assert clip.duration_ms == 3_000
    assert clip.source_in_ms == 1_000
    assert clip.source_out_ms == 7_000
    assert clip.volume == 65
    assert clip.muted
    assert clip.fit_mode == "채움"
    assert clip.effect == "따뜻하게"
    assert controller.history_count == before + 1


def test_removing_used_reference_is_rejected_without_changing_timeline() -> None:
    controller = MockController()
    controller.load_sample_project()
    controller.select_asset("media-beach")

    assert controller.asset_usage_count("media-beach") == 1
    before_assets = dict(controller.state.assets)
    before_clips = list(controller.state.all_clips)

    assert not controller.remove_selected_asset()

    assert controller.state.assets == before_assets
    assert controller.state.all_clips == before_clips
    assert controller.state.reference_asset_id == "media-beach"
    assert (controller.state.canvas_width, controller.state.canvas_height) == (1920, 1080)
    assert controller.state.total_duration_ms == 19_000
    assert "관련 클립 1개" in controller.state.status_message


def test_missing_media_and_partial_failure_remain_visible_and_recoverable() -> None:
    controller = MockController()
    controller.inject_missing_media()

    missing = controller.state.assets["media-missing"]
    assert missing.status is AssetStatus.MISSING
    assert controller.asset_usage_count("media-missing") == 1
    assert controller.state.playhead_ms == controller.state.visual_clips[-1].start_ms
    assert controller.relink_selected_asset()
    assert missing.status is AssetStatus.READY

    controller.inject_import_failure()
    assert controller.state.import_warning is not None
    assert controller.state.assets["media-broken"].status is AssetStatus.ERROR


def test_proxy_toggle_records_a_mock_media_edit_without_creating_files() -> None:
    controller = MockController()
    controller.load_sample_project()
    controller.select_asset("media-market")

    assert controller.toggle_selected_proxy()
    assert controller.state.assets["media-market"].proxy_enabled
    assert controller.state.assets["media-market"].proxy_status == "준비됨 · 목업 대체본"
    assert controller.undo_label == "프록시 사용"

    assert controller.undo()
    assert not controller.state.assets["media-market"].proxy_enabled


def test_text_transition_and_narration_mock_commands_update_fixed_tracks() -> None:
    controller = MockController()
    controller.load_sample_project()
    controller.select_clip("clip-market")

    assert controller.add_transition()
    assert controller.state.transitions["clip-market|clip-photo"] == "페이드 · 0.75초"

    controller.seek(10_000)
    assert controller.add_text("캡션")
    assert controller.selected_clip is not None
    assert controller.selected_clip.track is TrackKind.TEXT
    assert controller.update_selected_text(
        content="제주 야시장",
        position="아래",
        animation="페이드",
        duration_ms=2_500,
    )
    assert controller.selected_clip.label == "캡션 · 제주 야시장"

    controller.add_recorded_narration()
    assert controller.state.narration_clips[-1].label == "목업 내레이션"


def test_export_presentation_state_tracks_real_worker_updates() -> None:
    controller = MockController()
    controller.load_sample_project()
    controller.configure_export(
        path=r"C:\Videos\result.mp4",
        preset="1080p",
        framerate="30 fps",
        quality="높음",
    )

    assert controller.start_export()
    controller.update_export_progress(45, elapsed_ms=2_000, eta_ms=3_000)
    controller.update_export_progress(99, elapsed_ms=4_000, eta_ms=100, verifying=True)
    controller.complete_export(r"C:\Videos\result.mp4", elapsed_ms=4_500)
    assert controller.state.export_state is ExportState.COMPLETE
    assert controller.state.export_progress == 100
    assert controller.state.export_result_path == r"C:\Videos\result.mp4"

    controller.close_export_result()
    assert controller.start_export()
    assert controller.cancel_export()
    controller.complete_export_cancellation()
    assert controller.state.export_state is ExportState.CANCELLED

    controller.close_export_result()
    assert controller.start_export()
    controller.fail_export("디스크 공간이 부족합니다", "ENOSPC")
    assert controller.state.export_state is ExportState.FAILED
    assert controller.state.export_error == "디스크 공간이 부족합니다"
    assert controller.state.export_error_detail == "ENOSPC"


def test_real_save_marks_current_result_clean_without_serialising_history(
    tmp_path: Path,
) -> None:
    controller = MockController()
    controller.import_sample_media()
    history_before = controller.history_count
    target = tmp_path / "saved project.mmrproj"

    assert controller.save_project(str(target))

    assert controller.state.project_path == str(target)
    assert controller.state.project_name == "제목 없음"
    assert not controller.state.is_dirty
    assert controller.history_count == history_before
    assert target.is_file()


def test_controller_owns_export_recovery_and_discard_screen_state() -> None:
    controller = MockController()

    assert not controller.open_export_configuration()
    assert controller.state.export_state is ExportState.CLOSED

    controller.load_sample_project()
    assert controller.open_export_configuration()
    assert controller.state.export_state is ExportState.CONFIG

    controller.apply_recovery_choice("자동 저장본")
    assert controller.state.is_dirty
    assert controller.state.status_message.startswith("자동 저장본을 열었습니다")

    controller.discard_unsaved_changes()
    assert not controller.state.is_dirty
