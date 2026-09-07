"""PySide6 interactive mock-up for the approved product flows."""

from __future__ import annotations

import sys
from collections.abc import Callable, Sequence
from importlib.metadata import version

from PySide6.QtCore import QSignalBlocker, QSize, Qt, QTimer, qVersion
from PySide6.QtGui import (
    QAction,
    QCloseEvent,
    QColor,
    QDragEnterEvent,
    QDropEvent,
    QIcon,
    QKeySequence,
    QPainter,
    QPixmap,
    QResizeEvent,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDockWidget,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QTextEdit,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from movie_maker.media import AUDIO_EXTENSIONS, PHOTO_EXTENSIONS, VIDEO_EXTENSIONS
from movie_maker.ui.dialogs import (
    DecisionDialog,
    ExportSettingsDialog,
    MissingMediaDialog,
    NarrationDialog,
    OnboardingDialog,
    RecoveryDialog,
    RuntimeDialog,
)
from movie_maker.ui.mock_controller import MockController, format_time
from movie_maker.ui.mock_model import (
    AssetStatus,
    ExportState,
    MediaKind,
    MockAsset,
    MockClip,
    TrackKind,
)

MediaFileSelector = Callable[[], Sequence[str]]
ProjectOpenSelector = Callable[[], str | None]
ProjectSaveSelector = Callable[[str | None], str | None]


def _file_patterns(extensions: frozenset[str]) -> str:
    return " ".join(f"*{extension}" for extension in sorted(extensions))


MEDIA_FILE_FILTER = ";;".join(
    (
        f"지원 미디어 ({_file_patterns(VIDEO_EXTENSIONS | PHOTO_EXTENSIONS | AUDIO_EXTENSIONS)})",
        f"영상 ({_file_patterns(VIDEO_EXTENSIONS)})",
        f"사진 ({_file_patterns(PHOTO_EXTENSIONS)})",
        f"오디오 ({_file_patterns(AUDIO_EXTENSIONS)})",
        "모든 파일 (*)",
    )
)


class MainWindow(QMainWindow):
    """S-EDITOR interactive mock-up using only deterministic in-memory state."""

    def __init__(
        self,
        controller: MockController | None = None,
        *,
        media_file_selector: MediaFileSelector | None = None,
        project_open_selector: ProjectOpenSelector | None = None,
        project_save_selector: ProjectSaveSelector | None = None,
    ) -> None:
        super().__init__()
        self.setObjectName("S-EDITOR")
        self.setAcceptDrops(True)
        self.setMinimumSize(1024, 640)
        self.resize(1440, 900)
        self.controller = controller or MockController()
        self._media_file_selector = media_file_selector or self._choose_media_files
        self._project_open_selector = project_open_selector or self._choose_project_to_open
        self._project_save_selector = project_save_selector or self._choose_project_to_save
        self._allow_close = False
        self._showing_transition = False
        self._responsive_hidden_inspector = False
        self._dialogs: list[QWidget] = []
        self._actions: dict[str, QAction] = {}
        self._timeline_lists: dict[TrackKind, QListWidget] = {}

        self._preview_timer = QTimer(self)
        self._preview_timer.setInterval(100)
        self._preview_timer.timeout.connect(self.controller.advance_playback)
        self._export_timer = QTimer(self)
        self._export_timer.setInterval(200)
        self._export_timer.timeout.connect(self.controller.advance_export)

        self._build_central_workspace()
        self._build_library_dock()
        self._build_inspector_dock()
        self._create_actions()
        self._build_menus()
        self._build_toolbar()
        self._build_status_bar()
        self._apply_style()

        self.controller.state_changed.connect(self.refresh)
        self.controller.status_changed.connect(self._show_status)
        self.controller.export_changed.connect(self._refresh_export_panel)
        self.refresh()

    # ------------------------------------------------------------------
    # Widget construction

    def _build_central_workspace(self) -> None:
        root = QWidget()
        root.setObjectName("E-EDITOR-WORKSPACE")
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(10, 10, 10, 8)
        root_layout.setSpacing(8)

        self.workspace_splitter = QSplitter(Qt.Orientation.Vertical)
        self.workspace_splitter.setObjectName("E-EDITOR-SPLITTER")
        self.workspace_splitter.setChildrenCollapsible(False)
        self.preview_stack = QStackedWidget()
        self.preview_stack.addWidget(self._make_start_page())
        self.preview_stack.addWidget(self._make_preview_page())
        self.workspace_splitter.addWidget(self.preview_stack)
        self.workspace_splitter.addWidget(self._make_timeline_panel())
        self.workspace_splitter.setSizes([320, 320])
        root_layout.addWidget(self.workspace_splitter, 1)
        root_layout.addWidget(self._make_export_progress_panel())
        self.setCentralWidget(root)

    def _make_start_page(self) -> QWidget:
        page = QFrame()
        page.setObjectName("S-START")
        page.setProperty("role", "start")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(48, 32, 48, 32)
        layout.addStretch()
        eyebrow = QLabel("INTERACTIVE PRODUCT MOCK-UP")
        eyebrow.setObjectName("eyebrow")
        eyebrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(eyebrow)
        heading = QLabel("첫 영상을 만들어 볼까요?")
        heading.setObjectName("startHeading")
        heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(heading)
        description = QLabel(
            "로컬 영상, 사진과 오디오를 가져와 미디어 보관함을 만들 수 있습니다.\n"
            "가져오기, 프로젝트 저장과 타임라인 편집은 실제 기능이며 재생·출력은 목업입니다."
        )
        description.setObjectName("secondaryText")
        description.setAlignment(Qt.AlignmentFlag.AlignCenter)
        description.setWordWrap(True)
        layout.addWidget(description)
        button_row = QHBoxLayout()
        button_row.addStretch()
        import_button = QPushButton("＋  미디어 가져오기")
        import_button.setObjectName("E-START-IMPORT")
        import_button.setProperty("primary", True)
        import_button.setMinimumHeight(42)
        import_button.clicked.connect(self._import_media)
        button_row.addWidget(import_button)
        open_button = QPushButton("프로젝트 열기")
        open_button.setObjectName("E-START-OPEN")
        open_button.setMinimumHeight(42)
        open_button.clicked.connect(self._request_open_project)
        button_row.addWidget(open_button)
        button_row.addStretch()
        layout.addLayout(button_row)
        secondary_row = QHBoxLayout()
        secondary_row.addStretch()
        new_button = QPushButton("빈 새 프로젝트")
        new_button.setObjectName("E-START-NEW")
        new_button.clicked.connect(self._request_new_project)
        secondary_row.addWidget(new_button)
        example_button = QPushButton("예제 프로젝트 · 1.0")
        example_button.setObjectName("E-START-EXAMPLE")
        example_button.clicked.connect(self.controller.load_sample_project)
        secondary_row.addWidget(example_button)
        secondary_row.addStretch()
        layout.addLayout(secondary_row)
        recent = QPushButton("최근 프로젝트 · 제주 여행 목업 · 1.0")
        recent.setObjectName("E-START-RECENT")
        recent.setToolTip("목업 고정 최근 프로젝트를 엽니다")
        recent.clicked.connect(self.controller.load_sample_project)
        layout.addWidget(recent, alignment=Qt.AlignmentFlag.AlignHCenter)
        layout.addStretch()
        return page

    def _make_preview_page(self) -> QWidget:
        page = QFrame()
        page.setObjectName("S-PREVIEW")
        page.setProperty("role", "panel")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(12, 12, 12, 10)
        header = QHBoxLayout()
        title = QLabel("미리 보기")
        title.setObjectName("panelHeading")
        header.addWidget(title)
        header.addStretch()
        self.preview_badge = QLabel("목업 프레임")
        self.preview_badge.setObjectName("mockBadge")
        header.addWidget(self.preview_badge)
        layout.addLayout(header)

        self.preview_canvas = QLabel()
        self.preview_canvas.setObjectName("E-PREVIEW-CANVAS")
        self.preview_canvas.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_canvas.setWordWrap(True)
        self.preview_canvas.setMinimumSize(420, 150)
        self.preview_canvas.setAccessibleName("프로젝트 미리 보기 캔버스")
        layout.addWidget(self.preview_canvas, 1)

        controls = QHBoxLayout()
        self.step_back_button = QPushButton("◀│")
        self.step_back_button.setObjectName("E-PREVIEW-STEP-BACK")
        self.step_back_button.setToolTip("이전 목업 프레임 · 1.0")
        self.step_back_button.clicked.connect(lambda: self.controller.step_frame(-1))
        controls.addWidget(self.step_back_button)
        self.play_button = QPushButton("▶  재생")
        self.play_button.setObjectName("E-PREVIEW-PLAY")
        self.play_button.setMinimumWidth(92)
        self.play_button.clicked.connect(self.controller.toggle_playback)
        controls.addWidget(self.play_button)
        self.step_forward_button = QPushButton("│▶")
        self.step_forward_button.setObjectName("E-PREVIEW-STEP-FORWARD")
        self.step_forward_button.setToolTip("다음 목업 프레임 · 1.0")
        self.step_forward_button.clicked.connect(lambda: self.controller.step_frame(1))
        controls.addWidget(self.step_forward_button)
        self.preview_time = QLabel("00:00.000 / 00:00.000")
        self.preview_time.setObjectName("E-PREVIEW-TIME")
        self.preview_time.setMinimumWidth(150)
        controls.addWidget(self.preview_time)
        self.preview_seek = QSlider(Qt.Orientation.Horizontal)
        self.preview_seek.setObjectName("E-PREVIEW-SEEK")
        self.preview_seek.setAccessibleName("프로젝트 재생 위치")
        self.preview_seek.sliderMoved.connect(self.controller.seek)
        controls.addWidget(self.preview_seek, 1)
        self.preview_mute_button = QPushButton("🔊  미리 듣기")
        self.preview_mute_button.setObjectName("E-PREVIEW-MUTE")
        self.preview_mute_button.clicked.connect(self.controller.toggle_preview_mute)
        controls.addWidget(self.preview_mute_button)
        layout.addLayout(controls)
        return page

    def _make_timeline_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("S-TIMELINE")
        panel.setProperty("role", "panel")
        panel.setMinimumHeight(280)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(5)
        header = QHBoxLayout()
        title = QLabel("타임라인")
        title.setObjectName("panelHeading")
        header.addWidget(title)
        self.timeline_mode_combo = QComboBox()
        self.timeline_mode_combo.setObjectName("E-TIMELINE-MODE")
        self.timeline_mode_combo.addItems(["타임라인", "스토리보드 · 1.0"])
        self.timeline_mode_combo.currentTextChanged.connect(self._change_timeline_mode)
        header.addWidget(self.timeline_mode_combo)
        self.timeline_time = QLabel("00:00.000 / 00:00.000")
        self.timeline_time.setMinimumWidth(150)
        header.addWidget(self.timeline_time)
        header.addStretch()
        zoom_label = QLabel("확대 · 1.0")
        zoom_label.setProperty("role", "caption")
        header.addWidget(zoom_label)
        zoom_out = QPushButton("−")
        zoom_out.setToolTip("타임라인 축소 · 1.0")
        zoom_out.clicked.connect(
            lambda: self.controller.set_timeline_zoom(self.controller.state.timeline_zoom - 25)
        )
        header.addWidget(zoom_out)
        self.timeline_zoom = QSlider(Qt.Orientation.Horizontal)
        self.timeline_zoom.setObjectName("E-TIMELINE-ZOOM")
        self.timeline_zoom.setRange(50, 200)
        self.timeline_zoom.setSingleStep(25)
        self.timeline_zoom.setFixedWidth(90)
        self.timeline_zoom.sliderMoved.connect(self.controller.set_timeline_zoom)
        header.addWidget(self.timeline_zoom)
        zoom_in = QPushButton("＋")
        zoom_in.setToolTip("타임라인 확대 · 1.0")
        zoom_in.clicked.connect(
            lambda: self.controller.set_timeline_zoom(self.controller.state.timeline_zoom + 25)
        )
        header.addWidget(zoom_in)
        layout.addLayout(header)

        command_row = QHBoxLayout()
        self.timeline_playhead_label = QLabel("▼ 재생 헤드")
        self.timeline_playhead_label.setObjectName("E-TIMELINE-PLAYHEAD")
        self.timeline_playhead_label.setProperty("role", "caption")
        command_row.addWidget(self.timeline_playhead_label)
        self.timeline_transition_label = QLabel("◇ 전환 · 1.0")
        self.timeline_transition_label.setObjectName("E-TIMELINE-TRANSITION")
        self.timeline_transition_label.setProperty("role", "caption")
        command_row.addWidget(self.timeline_transition_label)
        command_row.addStretch()
        move_back = QPushButton("← 앞")
        move_back.setObjectName("I-TIMELINE-MOVE-BACK")
        move_back.clicked.connect(lambda: self.controller.move_selected_visual(-1))
        command_row.addWidget(move_back)
        move_forward = QPushButton("뒤 →")
        move_forward.setObjectName("I-TIMELINE-MOVE-FORWARD")
        move_forward.clicked.connect(lambda: self.controller.move_selected_visual(1))
        command_row.addWidget(move_forward)
        split_button = QPushButton("✂ 분할")
        split_button.setObjectName("I-TIMELINE-SPLIT")
        split_button.clicked.connect(self.controller.split_selected_clip)
        command_row.addWidget(split_button)
        delete_button = QPushButton("삭제")
        delete_button.setObjectName("I-TIMELINE-DELETE")
        delete_button.clicked.connect(self.controller.delete_selected_clip)
        command_row.addWidget(delete_button)
        layout.addLayout(command_row)

        self.timeline_ruler = QSlider(Qt.Orientation.Horizontal)
        self.timeline_ruler.setObjectName("E-TIMELINE-RULER")
        self.timeline_ruler.setAccessibleName("타임라인 눈금과 재생 헤드")
        self.timeline_ruler.sliderMoved.connect(self.controller.seek)
        layout.addWidget(self.timeline_ruler)

        for track in TrackKind:
            row = QHBoxLayout()
            label = QLabel(track.value)
            label.setFixedWidth(74)
            label.setObjectName(f"trackLabel-{track.name.lower()}")
            row.addWidget(label)
            track_list = QListWidget()
            track_list.setObjectName(self._track_object_name(track))
            track_list.setFlow(QListView.Flow.LeftToRight)
            track_list.setWrapping(False)
            track_list.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
            track_list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            track_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
            track_list.setFixedHeight(44)
            track_list.itemSelectionChanged.connect(
                lambda selected_track=track: self._select_timeline_items(selected_track)
            )
            self._timeline_lists[track] = track_list
            row.addWidget(track_list, 1)
            layout.addLayout(row)
        return panel

    def _make_export_progress_panel(self) -> QWidget:
        panel = QFrame()
        self.export_panel = panel
        panel.setObjectName("S-TASK-PROGRESS")
        panel.setProperty("role", "task")
        panel.setVisible(False)
        layout = QHBoxLayout(panel)
        self.export_stage = QLabel("동영상 저장 준비 중")
        self.export_stage.setObjectName("E-TASK-RESULT")
        self.export_stage.setMinimumWidth(260)
        self.export_stage.setWordWrap(True)
        layout.addWidget(self.export_stage)
        self.export_progress = QProgressBar()
        self.export_progress.setObjectName("E-TASK-PROGRESS")
        self.export_progress.setRange(0, 100)
        layout.addWidget(self.export_progress, 1)
        self.export_cancel_button = QPushButton("출력 취소")
        self.export_cancel_button.setObjectName("E-EXPORT-CANCEL")
        self.export_cancel_button.clicked.connect(self._request_cancel_export)
        layout.addWidget(self.export_cancel_button)
        self.export_result_button = QPushButton("결과 확인")
        self.export_result_button.clicked.connect(self._handle_export_result)
        layout.addWidget(self.export_result_button)
        return panel

    def _build_library_dock(self) -> None:
        self.library_dock = QDockWidget("미디어", self)
        self.library_dock.setObjectName("S-LIBRARY")
        self.library_dock.setMinimumWidth(220)
        self.library_dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(8, 8, 8, 8)
        header = QHBoxLayout()
        title = QLabel("미디어 보관함")
        title.setObjectName("panelHeading")
        header.addWidget(title)
        header.addStretch()
        import_button = QPushButton("＋ 가져오기")
        import_button.setObjectName("E-LIBRARY-IMPORT")
        import_button.setToolTip("로컬 영상, 사진과 오디오 파일 선택 (Ctrl+I)")
        import_button.clicked.connect(self._import_media)
        header.addWidget(import_button)
        layout.addLayout(header)
        self.library_filter = QComboBox()
        self.library_filter.setObjectName("E-LIBRARY-FILTER")
        self.library_filter.addItems(["전체", "영상", "사진", "오디오", "문제 있음"])
        self.library_filter.currentTextChanged.connect(self._refresh_library)
        layout.addWidget(self.library_filter)
        self.import_warning = QLabel()
        self.import_warning.setObjectName("E-LIBRARY-WARNING")
        self.import_warning.setWordWrap(True)
        self.import_warning.setProperty("role", "warning")
        self.import_warning.setVisible(False)
        layout.addWidget(self.import_warning)
        self.library_list = QListWidget()
        self.library_list.setObjectName("E-LIBRARY-GRID")
        self.library_list.setViewMode(QListView.ViewMode.IconMode)
        self.library_list.setResizeMode(QListView.ResizeMode.Adjust)
        self.library_list.setMovement(QListView.Movement.Static)
        self.library_list.setIconSize(QSize(150, 70))
        self.library_list.setGridSize(QSize(190, 112))
        self.library_list.setWordWrap(True)
        self.library_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.library_list.currentItemChanged.connect(self._select_library_item)
        self.library_list.itemDoubleClicked.connect(lambda _item: self.controller.add_selected_to_timeline())
        layout.addWidget(self.library_list, 1)
        self.library_empty = QLabel("미디어 가져오기로 영상, 사진과 오디오를 추가하세요.")
        self.library_empty.setObjectName("E-LIBRARY-EMPTY")
        self.library_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.library_empty.setWordWrap(True)
        self.library_empty.setProperty("role", "empty")
        layout.addWidget(self.library_empty)
        button_row = QHBoxLayout()
        self.library_add_button = QPushButton("타임라인에 추가")
        self.library_add_button.setObjectName("E-LIBRARY-ADD")
        self.library_add_button.clicked.connect(self.controller.add_selected_to_timeline)
        button_row.addWidget(self.library_add_button)
        self.library_remove_button = QPushButton("제거")
        self.library_remove_button.setObjectName("E-LIBRARY-REMOVE")
        self.library_remove_button.clicked.connect(self._request_remove_asset)
        button_row.addWidget(self.library_remove_button)
        layout.addLayout(button_row)
        self.library_relink_button = QPushButton("원본 다시 연결 · 1.0")
        self.library_relink_button.setObjectName("E-LIBRARY-RELINK")
        self.library_relink_button.clicked.connect(self.controller.relink_selected_asset)
        layout.addWidget(self.library_relink_button)
        self.library_count = QLabel("0개 항목 · 백그라운드 작업 없음")
        self.library_count.setObjectName("E-LIBRARY-JOBS")
        self.library_count.setProperty("role", "caption")
        layout.addWidget(self.library_count)
        self.library_dock.setWidget(container)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.library_dock)

    def _build_inspector_dock(self) -> None:
        self.inspector_dock = QDockWidget("속성", self)
        self.inspector_dock.setObjectName("S-INSPECTOR")
        self.inspector_dock.setMinimumWidth(260)
        self.inspector_dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(10, 10, 10, 10)
        self.inspector_header = QLabel("프로젝트 속성")
        self.inspector_header.setObjectName("E-INSPECTOR-HEADER")
        self.inspector_header.setProperty("role", "inspectorHeading")
        self.inspector_header.setWordWrap(True)
        layout.addWidget(self.inspector_header)
        self.inspector_reset = QPushButton("기본값 복원 · 1.0")
        self.inspector_reset.setObjectName("E-INSPECTOR-RESET")
        self.inspector_reset.clicked.connect(self.controller.reset_selected_properties)
        layout.addWidget(self.inspector_reset)
        self.inspector_stack = QStackedWidget()
        self.inspector_stack.setObjectName("E-INSPECTOR-STACK")
        self.project_page = self._make_project_inspector()
        self.media_page = self._make_media_inspector()
        self.clip_page = self._make_clip_inspector()
        self.audio_page = self._make_audio_inspector()
        self.text_page = self._make_text_inspector()
        self.transition_page = self._make_transition_inspector()
        self.inspector_stack.addWidget(self.project_page)
        self.inspector_stack.addWidget(self.media_page)
        self.inspector_stack.addWidget(self.clip_page)
        self.inspector_stack.addWidget(self.audio_page)
        self.inspector_stack.addWidget(self.text_page)
        self.inspector_stack.addWidget(self.transition_page)
        layout.addWidget(self.inspector_stack)
        layout.addStretch()
        scroll.setWidget(container)
        self.inspector_dock.setWidget(scroll)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.inspector_dock)
        self.resizeDocks(
            [self.library_dock, self.inspector_dock],
            [260, 300],
            Qt.Orientation.Horizontal,
        )

    def _make_project_inspector(self) -> QWidget:
        page = QWidget()
        page.setObjectName("E-INSPECTOR-PROJECT")
        layout = QVBoxLayout(page)
        form = QFormLayout()
        self.project_name_edit = QLineEdit()
        self.project_name_edit.setObjectName("E-PROJECT-NAME")
        self.project_name_edit.editingFinished.connect(self._apply_project_name)
        form.addRow("프로젝트 이름", self.project_name_edit)
        self.canvas_combo = QComboBox()
        self.canvas_combo.setObjectName("E-PROJECT-ASPECT")
        self.canvas_combo.addItems(["원본 유지", "16:9", "4:3 · 1.0"])
        self.canvas_combo.currentTextChanged.connect(self._apply_canvas_mode)
        form.addRow("프로젝트 화면", self.canvas_combo)
        layout.addLayout(form)
        self.project_summary = QLabel()
        self.project_summary.setObjectName("E-PROJECT-SUMMARY")
        self.project_summary.setWordWrap(True)
        self.project_summary.setProperty("role", "summary")
        layout.addWidget(self.project_summary)
        hint = QLabel(
            "원본 유지는 선택 시점의 첫 영상(없으면 첫 사진)을 기준으로 고정됩니다. "
            "다른 비율은 잘리지 않도록 맞춤 처리합니다."
        )
        hint.setWordWrap(True)
        hint.setObjectName("secondaryText")
        layout.addWidget(hint)
        layout.addStretch()
        return page

    def _make_media_inspector(self) -> QWidget:
        page = QWidget()
        page.setObjectName("E-INSPECTOR-MEDIA")
        layout = QVBoxLayout(page)
        self.media_info = QLabel()
        self.media_info.setWordWrap(True)
        self.media_info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.media_info.setProperty("role", "summary")
        layout.addWidget(self.media_info)
        self.media_status = QLabel()
        self.media_status.setWordWrap(True)
        layout.addWidget(self.media_status)
        self.media_proxy_status = QLabel("프록시: 사용 안 함")
        self.media_proxy_status.setObjectName("E-MEDIA-PROXY-STATUS")
        self.media_proxy_status.setWordWrap(True)
        layout.addWidget(self.media_proxy_status)
        self.media_proxy_button = QPushButton("프록시 사용 · 1.0")
        self.media_proxy_button.setObjectName("E-MEDIA-PROXY")
        self.media_proxy_button.clicked.connect(self.controller.toggle_selected_proxy)
        layout.addWidget(self.media_proxy_button)
        relink = QPushButton("새 원본 찾기 · 1.0")
        relink.clicked.connect(self.controller.relink_selected_asset)
        layout.addWidget(relink)
        layout.addStretch()
        return page

    def _make_clip_inspector(self) -> QWidget:
        page = QWidget()
        page.setObjectName("E-INSPECTOR-CLIP")
        layout = QVBoxLayout(page)
        self.clip_kind_label = QLabel()
        self.clip_kind_label.setProperty("role", "caption")
        layout.addWidget(self.clip_kind_label)
        form = QFormLayout()
        self.clip_in = QDoubleSpinBox()
        self.clip_in.setObjectName("E-CLIP-IN")
        self.clip_in.setRange(0.0, 600.0)
        self.clip_in.setSingleStep(0.5)
        self.clip_in.setSuffix("초")
        form.addRow("원본 시작", self.clip_in)
        self.clip_out = QDoubleSpinBox()
        self.clip_out.setObjectName("E-CLIP-OUT")
        self.clip_out.setRange(0.0, 600.0)
        self.clip_out.setSingleStep(0.5)
        self.clip_out.setSuffix("초")
        form.addRow("원본 끝", self.clip_out)
        self.clip_duration = QDoubleSpinBox()
        self.clip_duration.setObjectName("E-CLIP-DURATION")
        self.clip_duration.setRange(0.5, 600.0)
        self.clip_duration.setSingleStep(0.5)
        self.clip_duration.setSuffix("초")
        form.addRow("결과 길이", self.clip_duration)
        self.clip_speed = QComboBox()
        self.clip_speed.setObjectName("E-CLIP-SPEED")
        self.clip_speed.addItems(["0.5×", "1×", "1.5×", "2×"])
        form.addRow("재생 속도", self.clip_speed)
        self.clip_volume = QSpinBox()
        self.clip_volume.setObjectName("E-CLIP-SOURCE-VOLUME")
        self.clip_volume.setRange(0, 100)
        self.clip_volume.setSuffix("%")
        form.addRow("음량", self.clip_volume)
        self.clip_mute = QCheckBox("음소거")
        self.clip_mute.setObjectName("E-CLIP-SOURCE-MUTE")
        form.addRow("소리", self.clip_mute)
        self.clip_fit = QComboBox()
        self.clip_fit.setObjectName("E-CLIP-FIT")
        self.clip_fit.addItems(["맞춤", "채움"])
        form.addRow("화면 배치 · 1.0", self.clip_fit)
        self.clip_effect = QComboBox()
        self.clip_effect.setObjectName("E-CLIP-EFFECT")
        self.clip_effect.addItems(["없음", "따뜻하게", "흑백", "밝게"])
        form.addRow("효과 · 1.0", self.clip_effect)
        layout.addLayout(form)
        trim_row = QHBoxLayout()
        trim_start = QPushButton("시작 +0.5초")
        trim_start.setObjectName("E-TIMELINE-TRIM-START")
        trim_start.clicked.connect(lambda: self._nudge_trim("start"))
        trim_row.addWidget(trim_start)
        trim_end = QPushButton("끝 −0.5초")
        trim_end.setObjectName("E-TIMELINE-TRIM-END")
        trim_end.clicked.connect(lambda: self._nudge_trim("end"))
        trim_row.addWidget(trim_end)
        layout.addLayout(trim_row)
        rotation_frame = QFrame()
        rotation_frame.setObjectName("E-CLIP-ROTATE")
        rotation_row = QHBoxLayout()
        rotation_row.setContentsMargins(0, 0, 0, 0)
        rotate_left = QPushButton("↶ 왼쪽 90°")
        rotate_left.setObjectName("E-CLIP-ROTATE-LEFT")
        rotate_left.clicked.connect(lambda: self.controller.rotate_selected_clip(-90))
        rotation_row.addWidget(rotate_left)
        rotate_right = QPushButton("↷ 오른쪽 90°")
        rotate_right.setObjectName("E-CLIP-ROTATE-RIGHT")
        rotate_right.clicked.connect(lambda: self.controller.rotate_selected_clip(90))
        rotation_row.addWidget(rotate_right)
        rotation_frame.setLayout(rotation_row)
        layout.addWidget(rotation_frame)
        apply_button = QPushButton("속성 적용")
        apply_button.setObjectName("E-INSPECTOR-APPLY")
        apply_button.setProperty("primary", True)
        apply_button.clicked.connect(self._apply_clip_properties)
        layout.addWidget(apply_button)
        note = QLabel("실제 프레임·오디오 처리는 하지 않으며 값과 화면 피드백만 바뀝니다.")
        note.setObjectName("secondaryText")
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch()
        return page

    def _make_audio_inspector(self) -> QWidget:
        page = QWidget()
        page.setObjectName("E-INSPECTOR-AUDIO")
        layout = QVBoxLayout(page)
        self.audio_kind_label = QLabel()
        self.audio_kind_label.setProperty("role", "caption")
        layout.addWidget(self.audio_kind_label)
        form = QFormLayout()
        self.audio_start = QDoubleSpinBox()
        self.audio_start.setObjectName("E-AUDIO-START")
        self.audio_start.setRange(0.0, 3_600.0)
        self.audio_start.setSingleStep(0.5)
        self.audio_start.setSuffix("초")
        form.addRow("프로젝트 시작", self.audio_start)
        self.audio_in = QDoubleSpinBox()
        self.audio_in.setObjectName("E-AUDIO-RANGE")
        self.audio_in.setRange(0.0, 3_600.0)
        self.audio_in.setSingleStep(0.5)
        self.audio_in.setSuffix("초")
        form.addRow("원본 시작", self.audio_in)
        self.audio_out = QDoubleSpinBox()
        self.audio_out.setRange(0.0, 3_600.0)
        self.audio_out.setSingleStep(0.5)
        self.audio_out.setSuffix("초")
        form.addRow("원본 끝", self.audio_out)
        self.audio_volume = QSpinBox()
        self.audio_volume.setObjectName("E-AUDIO-VOLUME")
        self.audio_volume.setRange(0, 100)
        self.audio_volume.setSuffix("%")
        form.addRow("음량", self.audio_volume)
        self.audio_mute = QCheckBox("음소거")
        self.audio_mute.setObjectName("E-AUDIO-MUTE")
        form.addRow("소리", self.audio_mute)
        self.audio_fade_in = QComboBox()
        self.audio_fade_in.setObjectName("E-AUDIO-FADE-IN")
        self.audio_fade_in.addItems(["없음", "0.5초", "1초", "2초"])
        form.addRow("페이드 인 · 1.0", self.audio_fade_in)
        self.audio_fade_out = QComboBox()
        self.audio_fade_out.setObjectName("E-AUDIO-FADE-OUT")
        self.audio_fade_out.addItems(["없음", "0.5초", "1초", "2초"])
        form.addRow("페이드 아웃 · 1.0", self.audio_fade_out)
        self.audio_ducking = QComboBox()
        self.audio_ducking.setObjectName("E-AUDIO-DUCKING")
        self.audio_ducking.addItems(["꺼짐", "약하게", "보통", "강하게"])
        form.addRow("내레이션 더킹 · 1.0", self.audio_ducking)
        layout.addLayout(form)
        apply_button = QPushButton("오디오 속성 적용")
        apply_button.setProperty("primary", True)
        apply_button.clicked.connect(self._apply_audio_properties)
        layout.addWidget(apply_button)
        waveform = QLabel("▂▅▃▇▆▂▃▅▇▃▂▆▅▂ · 목업 파형 · 1.0")
        waveform.setObjectName("E-AUDIO-WAVEFORM")
        waveform.setProperty("role", "summary")
        waveform.setWordWrap(True)
        layout.addWidget(waveform)
        layout.addStretch()
        return page

    def _make_text_inspector(self) -> QWidget:
        page = QWidget()
        page.setObjectName("E-INSPECTOR-TEXT")
        layout = QVBoxLayout(page)
        form = QFormLayout()
        self.text_kind = QComboBox()
        self.text_kind.setObjectName("E-TEXT-KIND")
        self.text_kind.addItems(["제목", "캡션", "크레딧"])
        form.addRow("종류", self.text_kind)
        self.text_content = QTextEdit()
        self.text_content.setObjectName("E-TEXT-CONTENT")
        self.text_content.setPlaceholderText("텍스트를 입력하세요")
        self.text_content.setMaximumHeight(100)
        form.addRow("내용", self.text_content)
        self.text_font = QComboBox()
        self.text_font.setObjectName("E-TEXT-FONT")
        self.text_font.addItems(["맑은 고딕", "굴림", "바탕"])
        form.addRow("글꼴", self.text_font)
        font_attributes = QFrame()
        font_attributes_layout = QHBoxLayout(font_attributes)
        font_attributes_layout.setContentsMargins(0, 0, 0, 0)
        self.text_size = QSpinBox()
        self.text_size.setRange(12, 96)
        self.text_size.setSuffix("pt")
        font_attributes_layout.addWidget(self.text_size)
        self.text_bold = QCheckBox("굵게")
        font_attributes_layout.addWidget(self.text_bold)
        form.addRow("크기·두께", font_attributes)
        self.text_color = QComboBox()
        self.text_color.setObjectName("E-TEXT-COLOR")
        self.text_color.addItems(["흰색", "검정", "노랑", "하늘색"])
        form.addRow("색상", self.text_color)
        self.text_alignment = QComboBox()
        self.text_alignment.addItems(["왼쪽", "가운데", "오른쪽"])
        form.addRow("정렬", self.text_alignment)
        self.text_position = QComboBox()
        self.text_position.setObjectName("E-TEXT-POSITION")
        self.text_position.addItems(["위", "가운데", "아래"])
        form.addRow("위치", self.text_position)
        self.text_animation = QComboBox()
        self.text_animation.setObjectName("E-TEXT-ANIMATION")
        self.text_animation.addItems(["없음", "페이드", "위로 흐르기"])
        form.addRow("애니메이션", self.text_animation)
        self.text_start = QDoubleSpinBox()
        self.text_start.setObjectName("E-TEXT-TIME")
        self.text_start.setRange(0.0, 3_600.0)
        self.text_start.setSingleStep(0.5)
        self.text_start.setSuffix("초")
        form.addRow("시작", self.text_start)
        self.text_duration = QDoubleSpinBox()
        self.text_duration.setRange(0.5, 60.0)
        self.text_duration.setSingleStep(0.5)
        self.text_duration.setSuffix("초")
        form.addRow("표시 길이", self.text_duration)
        layout.addLayout(form)
        apply_button = QPushButton("텍스트 적용")
        apply_button.setProperty("primary", True)
        apply_button.clicked.connect(self._apply_text_properties)
        layout.addWidget(apply_button)
        layout.addStretch()
        return page

    def _make_transition_inspector(self) -> QWidget:
        page = QWidget()
        page.setObjectName("E-INSPECTOR-TRANSITION")
        layout = QVBoxLayout(page)
        note = QLabel("선택한 시각 클립과 바로 다음 클립 사이의 전환")
        note.setWordWrap(True)
        note.setProperty("role", "caption")
        layout.addWidget(note)
        form = QFormLayout()
        self.transition_type = QComboBox()
        self.transition_type.setObjectName("E-TRANSITION-TYPE")
        self.transition_type.addItems(["없음", "페이드", "디졸브", "닦아내기"])
        form.addRow("전환", self.transition_type)
        self.transition_duration = QDoubleSpinBox()
        self.transition_duration.setObjectName("E-TRANSITION-DURATION")
        self.transition_duration.setRange(0.1, 5.0)
        self.transition_duration.setSingleStep(0.25)
        self.transition_duration.setValue(0.75)
        self.transition_duration.setSuffix("초")
        form.addRow("길이", self.transition_duration)
        layout.addLayout(form)
        apply_button = QPushButton("전환 적용")
        apply_button.setProperty("primary", True)
        apply_button.clicked.connect(self._apply_transition_properties)
        layout.addWidget(apply_button)
        back_button = QPushButton("클립 속성으로")
        back_button.clicked.connect(self._show_clip_inspector)
        layout.addWidget(back_button)
        layout.addStretch()
        return page

    def _create_actions(self) -> None:
        self._actions = {
            "new": self._action("새 프로젝트", self._request_new_project, "Ctrl+N"),
            "open": self._action("프로젝트 열기…", self._request_open_project, "Ctrl+O"),
            "save": self._action("저장", self._request_save, "Ctrl+S"),
            "save_as": self._action(
                "다른 이름으로 저장…",
                lambda: self._request_save(save_as=True),
                "Ctrl+Shift+S",
            ),
            "import": self._action(
                "미디어 가져오기…",
                self._import_media,
                "Ctrl+I",
            ),
            "remove_asset": self._action("보관함에서 제거", self._request_remove_asset),
            "export": self._action("동영상 저장…", self._open_export_settings, "Ctrl+E"),
            "exit": self._action("종료", self.close),
            "undo": self._action("실행 취소", self.controller.undo, "Ctrl+Z"),
            "redo": self._action("다시 실행", self.controller.redo, "Ctrl+Y"),
            "delete": self._action("선택 항목 삭제", self._delete_contextual, "Delete"),
            "split": self._action("재생 위치에서 분할", self.controller.split_selected_clip, "Ctrl+B"),
            "duplicate": self._action("클립 복제 · 1.0", self.controller.duplicate_selected_clip, "Ctrl+D"),
            "move_back": self._action(
                "클립 앞으로 이동",
                lambda: self.controller.move_selected_visual(-1),
            ),
            "move_forward": self._action(
                "클립 뒤로 이동",
                lambda: self.controller.move_selected_visual(1),
            ),
            "add_music": self._action(
                "선택 오디오를 음악으로 추가",
                self.controller.add_selected_to_timeline,
            ),
            "add_narration": self._action(
                "선택 오디오를 내레이션으로 추가 · 1.0",
                lambda: self.controller.add_selected_to_timeline(narration=True),
            ),
            "record_narration": self._action(
                "내레이션 녹음 · 1.0",
                self._open_narration,
            ),
            "add_title": self._action("제목 추가 · 1.0", lambda: self.controller.add_text("제목")),
            "add_caption": self._action(
                "캡션 추가 · 1.0",
                lambda: self.controller.add_text("캡션"),
            ),
            "add_credits": self._action(
                "크레딧 추가 · 1.0",
                lambda: self.controller.add_text("크레딧"),
            ),
            "transition": self._action("전환 속성 · 1.0", self._add_transition_and_show),
            "fit": self._action("화면에 맞춤 · 1.0", lambda: self.controller.set_clip_fit("맞춤")),
            "fill": self._action("화면 채움 · 1.0", lambda: self.controller.set_clip_fit("채움")),
            "rotate_left": self._action(
                "왼쪽으로 회전 · 1.0",
                lambda: self.controller.rotate_selected_clip(-90),
            ),
            "rotate_right": self._action(
                "오른쪽으로 회전 · 1.0",
                lambda: self.controller.rotate_selected_clip(90),
            ),
            "onboarding": self._action("시작 안내 · 1.0", self._open_onboarding),
            "runtime": self._action("실행 환경 및 진단", self._open_runtime),
            "sample": self._action("샘플 편집 프로젝트 불러오기 · 목업", self.controller.load_sample_project),
            "empty": self._action("빈 프로젝트로 초기화 · 목업", self._request_new_project),
            "missing": self._action("누락 미디어 추가 · 목업", self._inject_and_show_missing),
            "import_failure": self._action(
                "가져오기 부분 실패 표시 · 목업",
                self.controller.inject_import_failure,
            ),
            "fail_write": self._action(
                "다음 출력: 위치에 쓸 수 없음 · 목업",
                lambda: self.controller.reserve_export_failure("출력 위치에 쓸 수 없습니다"),
            ),
            "fail_ffmpeg": self._action(
                "다음 출력: FFmpeg 없음 · 목업",
                lambda: self.controller.reserve_export_failure("FFmpeg를 찾을 수 없습니다"),
            ),
            "fail_space": self._action(
                "다음 출력: 디스크 공간 부족 · 목업",
                lambda: self.controller.reserve_export_failure("디스크 공간이 부족합니다"),
            ),
            "recovery": self._action("자동 저장 복구 열기 · 목업", self._open_recovery),
            "version_error": self._action(
                "새 버전 프로젝트 오류 · 목업",
                self._open_project_version_message,
            ),
        }
        self._actions["library_toggle"] = self.library_dock.toggleViewAction()
        self._actions["library_toggle"].setText("미디어 보관함")
        self._actions["library_toggle"].setObjectName("E-WINDOW-LIBRARY-TOGGLE")
        self._actions["inspector_toggle"] = self.inspector_dock.toggleViewAction()
        self._actions["inspector_toggle"].setText("속성")
        self._actions["inspector_toggle"].setObjectName("E-WINDOW-INSPECTOR-TOGGLE")

    def _build_menus(self) -> None:
        menu_bar = self.menuBar()
        menu_bar.setObjectName("E-WINDOW-MENU")
        file_menu = menu_bar.addMenu("파일")
        self._add_actions(file_menu, "new", "open")
        recent_menu = file_menu.addMenu("최근 프로젝트 · 1.0")
        recent_menu.addAction(self._actions["sample"])
        file_menu.addSeparator()
        self._add_actions(file_menu, "save", "save_as", "import")
        file_menu.addSeparator()
        self._add_actions(file_menu, "export", "exit")

        edit_menu = menu_bar.addMenu("편집")
        self._add_actions(edit_menu, "undo", "redo")
        edit_menu.addSeparator()
        self._add_actions(edit_menu, "delete")

        clip_menu = menu_bar.addMenu("클립")
        self._add_actions(
            clip_menu,
            "split",
            "duplicate",
            "move_back",
            "move_forward",
            "rotate_left",
            "rotate_right",
        )

        audio_menu = menu_bar.addMenu("오디오")
        self._add_actions(audio_menu, "add_music", "add_narration", "record_narration")

        text_menu = menu_bar.addMenu("텍스트")
        self._add_actions(text_menu, "add_title", "add_caption", "add_credits")

        effects_menu = menu_bar.addMenu("효과")
        self._add_actions(effects_menu, "transition", "fit", "fill")

        view_menu = menu_bar.addMenu("보기")
        self._add_actions(view_menu, "library_toggle", "inspector_toggle")
        view_menu.addSeparator()
        timeline_menu = view_menu.addMenu("스토리보드·타임라인 · 1.0")
        timeline_menu.addAction(
            self._action("타임라인", lambda: self.controller.set_timeline_mode("타임라인"))
        )
        timeline_menu.addAction(
            self._action(
                "스토리보드",
                lambda: self.controller.set_timeline_mode("스토리보드 · 1.0"),
            )
        )

        help_menu = menu_bar.addMenu("도움말")
        self._add_actions(help_menu, "onboarding", "runtime")

        mock_menu = menu_bar.addMenu("목업 상태")
        mock_menu.setObjectName("M-MOCK-STATE-MENU")
        self._add_actions(mock_menu, "sample", "empty", "missing", "import_failure")
        mock_menu.addSeparator()
        failure_menu = mock_menu.addMenu("다음 출력 실패 예약")
        self._add_actions(failure_menu, "fail_write", "fail_ffmpeg", "fail_space")
        mock_menu.addSeparator()
        self._add_actions(mock_menu, "recovery", "version_error", "onboarding", "runtime")

    def _build_toolbar(self) -> None:
        toolbar = QToolBar("주요 명령")
        toolbar.setObjectName("E-WINDOW-TOOLBAR")
        toolbar.setMovable(False)
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, toolbar)
        for name in ("new", "open", "save"):
            toolbar.addAction(self._actions[name])
        toolbar.addSeparator()
        toolbar.addAction(self._actions["import"])
        toolbar.addAction(self._actions["remove_asset"])
        toolbar.addSeparator()
        toolbar.addAction(self._actions["undo"])
        toolbar.addAction(self._actions["redo"])
        toolbar.addSeparator()
        toolbar.addAction(self._actions["split"])
        toolbar.addAction(self._actions["delete"])
        toolbar.addSeparator()
        toolbar.addAction(self._actions["export"])

    def _build_status_bar(self) -> None:
        status = QStatusBar()
        status.setObjectName("E-WINDOW-STATUS")
        self.setStatusBar(status)
        self.status_summary = QLabel("길이 00:00.000 · 백그라운드 작업 없음 · 목업")
        status.addPermanentWidget(self.status_summary)

    # ------------------------------------------------------------------
    # Shared helpers

    def _action(
        self,
        text: str,
        callback: Callable[[], object],
        shortcut: str | None = None,
    ) -> QAction:
        action = QAction(text, self)
        action.setObjectName(f"action-{text.split(' ·', maxsplit=1)[0]}")
        if shortcut is not None:
            action.setShortcut(QKeySequence(shortcut))
            action.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        action.triggered.connect(lambda _checked=False: callback())
        return action

    def _add_actions(self, menu: QMenu, *names: str) -> None:
        for name in names:
            menu.addAction(self._actions[name])

    @staticmethod
    def _track_object_name(track: TrackKind) -> str:
        return {
            TrackKind.VISUAL: "E-TIMELINE-VIDEO-TRACK",
            TrackKind.MUSIC: "E-TIMELINE-MUSIC-TRACK",
            TrackKind.NARRATION: "E-TIMELINE-NARRATION-TRACK",
            TrackKind.TEXT: "E-TIMELINE-TEXT-TRACK",
        }[track]

    def _show_dialog(self, dialog: QWidget) -> None:
        self._dialogs.append(dialog)
        if hasattr(dialog, "finished"):
            dialog.finished.connect(lambda _result, item=dialog: self._forget_dialog(item))
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _forget_dialog(self, dialog: QWidget) -> None:
        if dialog in self._dialogs:
            self._dialogs.remove(dialog)

    def _show_status(self, message: str) -> None:
        self.statusBar().showMessage(message)

    @staticmethod
    def _media_icon(asset: MockAsset, size: QSize) -> QIcon:
        pixmap = QPixmap(size)
        pixmap.fill(QColor(asset.color))
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        thumbnail = QPixmap()
        loaded_thumbnail = asset.thumbnail_png is not None and thumbnail.loadFromData(
            asset.thumbnail_png
        )
        if loaded_thumbnail:
            scaled = thumbnail.scaled(
                size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            left = (size.width() - scaled.width()) // 2
            top = (size.height() - scaled.height()) // 2
            painter.drawPixmap(left, top, scaled)
        else:
            painter.setPen(QColor("#ffffff"))
            font = painter.font()
            font.setBold(True)
            font.setPointSize(12)
            painter.setFont(font)
            symbol = {
                MediaKind.VIDEO: "VIDEO",
                MediaKind.PHOTO: "PHOTO",
                MediaKind.AUDIO: "AUDIO",
            }[asset.kind]
            painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, symbol)
        if asset.status is not AssetStatus.READY or asset.thumbnail_error is not None:
            painter.setBrush(QColor("#c73d4d"))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(size.width() - 28, 6, 22, 22)
            painter.setPen(QColor("#ffffff"))
            painter.drawText(size.width() - 28, 6, 22, 22, Qt.AlignmentFlag.AlignCenter, "!")
        painter.end()
        return QIcon(pixmap)

    # ------------------------------------------------------------------
    # State-to-view rendering

    def refresh(self) -> None:
        state = self.controller.state
        title_suffix = " *" if state.is_dirty else ""
        self.setWindowTitle(
            f"{state.project_name}{title_suffix} — Movie Maker Reproduction · "
            "W-04 타임라인 편집 · 재생·출력 인터랙티브 목업"
        )
        self.preview_stack.setCurrentIndex(0 if not state.assets else 1)
        self._refresh_library()
        self._refresh_timeline()
        self._refresh_preview()
        self._refresh_inspector()
        self._refresh_actions()
        self._refresh_export_panel()
        proxy_count = sum(asset.proxy_enabled for asset in state.assets.values())
        background_summary = (
            f"목업 프록시 {proxy_count}개 준비됨"
            if proxy_count
            else "백그라운드 작업 없음"
        )
        self.status_summary.setText(
            f"길이 {format_time(state.total_duration_ms)} · "
            f"{background_summary} · 미디어 보관함 실제 분석 · 편집 목업"
        )
        if state.is_playing and not self._preview_timer.isActive():
            self._preview_timer.start()
        elif not state.is_playing and self._preview_timer.isActive():
            self._preview_timer.stop()

    def _refresh_library(self) -> None:
        state = self.controller.state
        selected_id = state.selected_asset_id
        selected_filter = self.library_filter.currentText()
        blocker = QSignalBlocker(self.library_list)
        self.library_list.clear()
        visible_count = 0
        for asset in state.assets.values():
            if selected_filter == "문제 있음":
                visible = (
                    asset.status is not AssetStatus.READY
                    or asset.thumbnail_error is not None
                )
            elif selected_filter == "전체":
                visible = True
            else:
                visible = asset.kind.value == selected_filter
            if not visible:
                continue
            visible_count += 1
            duration = "사진" if asset.duration_ms is None else format_time(asset.duration_ms)
            if asset.status is not AssetStatus.READY:
                status = f"\n⚠ {asset.status.value}"
            elif asset.thumbnail_error is not None:
                status = "\n⚠ 썸네일 없음"
            else:
                status = ""
            item = QListWidgetItem(
                self._media_icon(asset, QSize(150, 70)),
                f"{asset.name}\n{asset.kind.value} · {duration}{status}",
            )
            item.setData(Qt.ItemDataRole.UserRole, asset.asset_id)
            tooltip = (
                f"{asset.name}\n{asset.resolution_text}\n{asset.source_path}\n"
                f"상태: {asset.status.value}"
            )
            if asset.thumbnail_error is not None:
                tooltip += f"\n썸네일: {asset.thumbnail_error}"
            item.setToolTip(tooltip)
            self.library_list.addItem(item)
            if asset.asset_id == selected_id:
                self.library_list.setCurrentItem(item)
        del blocker
        total = len(state.assets)
        self.library_empty.setVisible(visible_count == 0)
        if total == 0:
            self.library_empty.setText("미디어 가져오기로 영상, 사진과 오디오를 추가하세요.")
        elif visible_count == 0:
            self.library_empty.setText(f"‘{selected_filter}’ 필터에 해당하는 항목이 없습니다.")
        self.import_warning.setVisible(state.import_warning is not None)
        self.import_warning.setText(state.import_warning or "")
        proxy_count = sum(asset.proxy_enabled for asset in state.assets.values())
        background_summary = (
            f"목업 프록시 {proxy_count}개 준비됨"
            if proxy_count
            else "백그라운드 작업 없음"
        )
        self.library_count.setText(f"{total}개 항목 · {background_summary}")

    def _refresh_timeline(self) -> None:
        state = self.controller.state
        for track, widget in self._timeline_lists.items():
            blocker = QSignalBlocker(widget)
            widget.clear()
            clips = self.controller.clips_for_track(track)
            if not clips:
                placeholder = QListWidgetItem("＋ 여기에 미디어 추가")
                placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
                placeholder.setSizeHint(QSize(190, 42))
                widget.addItem(placeholder)
            for clip in clips:
                asset = self.controller.asset_for_clip(clip)
                icon = self._clip_icon(clip, asset)
                details = f"{format_time(clip.duration_ms)}"
                if clip.volume != 100 or clip.muted:
                    details += f" · {'음소거' if clip.muted else f'{clip.volume}%'}"
                if track is TrackKind.VISUAL:
                    index = state.visual_clips.index(clip)
                    if index < len(state.visual_clips) - 1:
                        next_clip = state.visual_clips[index + 1]
                        if f"{clip.clip_id}|{next_clip.clip_id}" in state.transitions:
                            details += " · ◈ 페이드"
                label = f"{clip.label}\n{details}"
                if asset is not None and asset.status is not AssetStatus.READY:
                    label = f"⚠ {label}"
                item = QListWidgetItem(icon, label)
                item.setData(Qt.ItemDataRole.UserRole, clip.clip_id)
                item.setToolTip(
                    f"{track.value} · 시작 {format_time(clip.start_ms)} · 길이 {details}"
                )
                width = int(max(120, min(330, clip.duration_ms / 55 * state.timeline_zoom / 100)))
                item.setSizeHint(QSize(width, 36))
                widget.addItem(item)
                if clip.clip_id in state.selected_clip_ids:
                    item.setSelected(True)
                if clip.clip_id == state.selected_clip_id:
                    widget.setCurrentItem(item)
            del blocker
        total = state.total_duration_ms
        self.timeline_ruler.setRange(0, total)
        self.timeline_ruler.setValue(state.playhead_ms)
        self.timeline_ruler.setEnabled(total > 0)
        self.timeline_time.setText(f"{format_time(state.playhead_ms)} / {format_time(total)}")
        mode_blocker = QSignalBlocker(self.timeline_mode_combo)
        self.timeline_mode_combo.setCurrentText(state.timeline_mode)
        del mode_blocker
        zoom_blocker = QSignalBlocker(self.timeline_zoom)
        self.timeline_zoom.setValue(state.timeline_zoom)
        del zoom_blocker

    def _clip_icon(self, clip: MockClip, asset: MockAsset | None) -> QIcon:
        if asset is not None:
            return self._media_icon(asset, QSize(72, 36))
        pixmap = QPixmap(72, 36)
        pixmap.fill(QColor("#2c8a83"))
        painter = QPainter(pixmap)
        painter.setPen(QColor("#ffffff"))
        painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "TEXT")
        painter.end()
        return QIcon(pixmap)

    def _refresh_preview(self) -> None:
        state = self.controller.state
        total = state.total_duration_ms
        self.preview_seek.setRange(0, total)
        self.preview_seek.setValue(state.playhead_ms)
        self.preview_seek.setEnabled(total > 0)
        self.preview_time.setText(f"{format_time(state.playhead_ms)} / {format_time(total)}")
        self.play_button.setText("❚❚  일시 정지" if state.is_playing else "▶  재생")
        self.preview_mute_button.setText(
            "🔇  음소거됨" if state.preview_muted else "🔊  미리 듣기"
        )
        self.step_back_button.setEnabled(state.playhead_ms > 0)
        self.step_forward_button.setEnabled(state.playhead_ms < total)
        if not state.visual_clips:
            self.preview_canvas.setStyleSheet(
                "background:#111722; border:1px dashed #657083; border-radius:8px; color:#aab3c2;"
            )
            self.preview_canvas.setText(
                "<b>타임라인에 영상이나 사진을 추가하세요</b><br>"
                "왼쪽 보관함에서 항목을 더블클릭하거나 ‘타임라인에 추가’를 누릅니다."
            )
            return
        clip = self._visual_clip_at(state.playhead_ms)
        if clip is None:
            return
        asset = self.controller.asset_for_clip(clip)
        color = asset.color if asset is not None else "#273140"
        missing = asset is None or asset.status is not AssetStatus.READY
        if missing:
            color = "#4a4f58"
        self.preview_canvas.setStyleSheet(
            f"background:{color}; border:1px solid #78869a; border-radius:8px; color:white;"
        )
        canvas = (
            f"{state.canvas_width}×{state.canvas_height}"
            if state.canvas_width is not None and state.canvas_height is not None
            else "화면 미정"
        )
        frame_time = max(0, state.playhead_ms - clip.start_ms)
        main_text = "⚠ 원본 미디어 누락" if missing else clip.label
        fit_note = f"{clip.fit_mode} · 회전 {clip.rotation}° · 효과 {clip.effect}"
        overlay = self._active_text_overlay(state.playhead_ms)
        overlay_text = f"<br><br><span style='font-size:18px'>{overlay}</span>" if overlay else ""
        self.preview_canvas.setText(
            f"<span style='font-size:22px'><b>{main_text}</b></span><br>"
            f"목업 프레임 {format_time(frame_time)}<br>"
            f"프로젝트 화면 {canvas} · {state.canvas_mode}<br>{fit_note}{overlay_text}"
        )

    def _visual_clip_at(self, position_ms: int) -> MockClip | None:
        clips = self.controller.state.visual_clips
        for clip in clips:
            if clip.start_ms <= position_ms < clip.start_ms + clip.duration_ms:
                return clip
        return clips[-1] if clips and position_ms == self.controller.state.total_duration_ms else None

    def _active_text_overlay(self, position_ms: int) -> str:
        for clip in self.controller.state.text_clips:
            if clip.start_ms <= position_ms < clip.start_ms + clip.duration_ms:
                return clip.text_content.strip() or "텍스트를 입력하세요"
        return ""

    def _refresh_inspector(self) -> None:
        state = self.controller.state
        asset = self.controller.selected_asset
        clip = self.controller.selected_clip
        single_clip = len(state.selected_clip_ids) == 1
        self.inspector_reset.setVisible(clip is not None)
        if asset is not None:
            self._showing_transition = False
            self.inspector_header.setText(f"미디어 · {asset.name}")
            self.inspector_stack.setCurrentWidget(self.media_page)
            duration = "해당 없음" if asset.duration_ms is None else format_time(asset.duration_ms)
            self.media_info.setText(
                f"종류\n{asset.kind.value}\n\n길이\n{duration}\n\n해상도\n"
                f"{asset.resolution_text}\n\n원본 경로\n{asset.source_path}"
            )
            self.media_status.setText(f"상태: {asset.status.value}")
            self.media_status.setProperty(
                "role",
                "warning" if asset.status is not AssetStatus.READY else "success",
            )
            self.media_proxy_status.setText(f"프록시: {asset.proxy_status}")
            proxy_available = asset.kind is MediaKind.VIDEO and asset.status is AssetStatus.READY
            self.media_proxy_button.setEnabled(proxy_available)
            self.media_proxy_button.setText(
                "프록시 사용 해제 · 1.0" if asset.proxy_enabled else "프록시 사용 · 1.0"
            )
            self.media_proxy_button.setToolTip(
                "실제 파일 없이 준비 완료 상태만 만드는 목업"
                if proxy_available
                else "정상 상태의 영상 미디어를 선택하세요"
            )
            return
        if self._showing_transition and clip is not None and clip.track is TrackKind.VISUAL:
            index = state.visual_clips.index(clip)
            if single_clip and index < len(state.visual_clips) - 1:
                next_clip = state.visual_clips[index + 1]
                boundary = f"{clip.clip_id}|{next_clip.clip_id}"
                value = state.transitions.get(boundary, "없음")
                transition_type = value.split(" ·", maxsplit=1)[0]
                transition_duration_value = 0.75
                if " · " in value and value.endswith("초"):
                    try:
                        transition_duration_value = float(value.split(" · ", maxsplit=1)[1][:-1])
                    except ValueError:
                        transition_duration_value = 0.75
                blockers = [
                    QSignalBlocker(self.transition_type),
                    QSignalBlocker(self.transition_duration),
                ]
                self.transition_type.setCurrentText(transition_type)
                self.transition_duration.setValue(transition_duration_value)
                del blockers
                self.inspector_header.setText(f"전환 · {clip.label} → {next_clip.label}")
                self.inspector_stack.setCurrentWidget(self.transition_page)
                return
            self._showing_transition = False
        if clip is not None and clip.track is TrackKind.TEXT:
            prefix = (
                f"클립 {len(state.selected_clip_ids)}개 선택 · 공통 속성"
                if len(state.selected_clip_ids) > 1
                else f"{clip.text_kind} · {clip.label}"
            )
            self.inspector_header.setText(prefix)
            self.inspector_stack.setCurrentWidget(self.text_page)
            self.text_page.setEnabled(single_clip)
            blockers = [
                QSignalBlocker(self.text_kind),
                QSignalBlocker(self.text_content),
                QSignalBlocker(self.text_font),
                QSignalBlocker(self.text_size),
                QSignalBlocker(self.text_bold),
                QSignalBlocker(self.text_color),
                QSignalBlocker(self.text_alignment),
                QSignalBlocker(self.text_position),
                QSignalBlocker(self.text_animation),
                QSignalBlocker(self.text_start),
                QSignalBlocker(self.text_duration),
            ]
            self.text_kind.setCurrentText(clip.text_kind or "캡션")
            self.text_content.setPlainText(clip.text_content)
            self.text_font.setCurrentText(clip.text_font)
            self.text_size.setValue(clip.text_size)
            self.text_bold.setChecked(clip.text_bold)
            self.text_color.setCurrentText(clip.text_color)
            self.text_alignment.setCurrentText(clip.text_alignment)
            self.text_position.setCurrentText(clip.text_position)
            self.text_animation.setCurrentText(clip.text_animation)
            self.text_start.setValue(clip.start_ms / 1_000)
            self.text_duration.setValue(clip.duration_ms / 1_000)
            del blockers
            return
        if clip is not None and clip.track in {TrackKind.MUSIC, TrackKind.NARRATION}:
            prefix = (
                f"클립 {len(state.selected_clip_ids)}개 선택 · 공통 속성"
                if len(state.selected_clip_ids) > 1
                else f"{clip.track.value} 클립 · {clip.label}"
            )
            self.inspector_header.setText(prefix)
            self.inspector_stack.setCurrentWidget(self.audio_page)
            self.audio_page.setEnabled(single_clip)
            blockers = [
                QSignalBlocker(self.audio_start),
                QSignalBlocker(self.audio_in),
                QSignalBlocker(self.audio_out),
                QSignalBlocker(self.audio_volume),
                QSignalBlocker(self.audio_mute),
                QSignalBlocker(self.audio_fade_in),
                QSignalBlocker(self.audio_fade_out),
                QSignalBlocker(self.audio_ducking),
            ]
            self.audio_kind_label.setText(
                f"{clip.track.value} · 길이 {format_time(clip.duration_ms)}"
            )
            self.audio_start.setValue(clip.start_ms / 1_000)
            self.audio_in.setValue(clip.source_in_ms / 1_000)
            audio_out = clip.source_out_ms or clip.source_in_ms + clip.duration_ms
            self.audio_out.setValue(audio_out / 1_000)
            self.audio_volume.setValue(clip.volume)
            self.audio_mute.setChecked(clip.muted)
            self.audio_fade_in.setCurrentText(self._duration_choice(clip.fade_in_ms))
            self.audio_fade_out.setCurrentText(self._duration_choice(clip.fade_out_ms))
            self.audio_ducking.setCurrentText(clip.ducking)
            del blockers
            return
        if clip is not None:
            prefix = (
                f"클립 {len(state.selected_clip_ids)}개 선택 · 공통 속성"
                if len(state.selected_clip_ids) > 1
                else f"{clip.track.value} 클립 · {clip.label}"
            )
            self.inspector_header.setText(prefix)
            self.inspector_stack.setCurrentWidget(self.clip_page)
            self.clip_page.setEnabled(single_clip)
            asset_kind = self.controller.asset_for_clip(clip)
            kind_text = asset_kind.kind.value if asset_kind is not None else clip.track.value
            self.clip_kind_label.setText(
                f"{kind_text} · 시작 {format_time(clip.start_ms)} · "
                f"원본 시작 {format_time(clip.source_in_ms)}"
            )
            blockers = [
                QSignalBlocker(self.clip_in),
                QSignalBlocker(self.clip_out),
                QSignalBlocker(self.clip_duration),
                QSignalBlocker(self.clip_speed),
                QSignalBlocker(self.clip_volume),
                QSignalBlocker(self.clip_mute),
                QSignalBlocker(self.clip_fit),
                QSignalBlocker(self.clip_effect),
            ]
            self.clip_in.setValue(clip.source_in_ms / 1_000)
            source_out = clip.source_out_ms or clip.source_in_ms + clip.duration_ms
            self.clip_out.setValue(source_out / 1_000)
            self.clip_duration.setValue(clip.duration_ms / 1_000)
            self.clip_speed.setCurrentText(f"{clip.speed:g}×")
            self.clip_volume.setValue(clip.volume)
            self.clip_mute.setChecked(clip.muted)
            self.clip_fit.setCurrentText(clip.fit_mode)
            self.clip_effect.setCurrentText(clip.effect)
            is_visual = clip.track is TrackKind.VISUAL
            is_photo = asset_kind is not None and asset_kind.kind is MediaKind.PHOTO
            is_video = asset_kind is not None and asset_kind.kind is MediaKind.VIDEO
            self.clip_in.setEnabled(not is_photo)
            self.clip_out.setEnabled(not is_photo)
            self.clip_speed.setEnabled(is_video)
            self.clip_volume.setEnabled(is_video)
            self.clip_mute.setEnabled(is_video)
            self.clip_fit.setEnabled(is_visual)
            self.clip_effect.setEnabled(is_visual)
            del blockers
            return
        self._showing_transition = False
        self.inspector_header.setText("프로젝트 속성")
        self.inspector_stack.setCurrentWidget(self.project_page)
        name_blocker = QSignalBlocker(self.project_name_edit)
        self.project_name_edit.setText(state.project_name)
        del name_blocker
        canvas_blocker = QSignalBlocker(self.canvas_combo)
        display_canvas = "4:3 · 1.0" if state.canvas_mode == "4:3" else state.canvas_mode
        self.canvas_combo.setCurrentText(display_canvas)
        del canvas_blocker
        size = (
            f"{state.canvas_width}×{state.canvas_height}"
            if state.canvas_width is not None and state.canvas_height is not None
            else "첫 시각 미디어 추가 후 결정"
        )
        reference = "없음"
        if state.reference_asset_id:
            ref = state.assets.get(state.reference_asset_id)
            reference = ref.name if ref is not None else "제거됨 · 저장된 화면은 유지"
        audio_count = len(state.music_clips) + len(state.narration_clips)
        self.project_summary.setText(
            f"화면: {state.canvas_mode} · {size}\n"
            f"기준 미디어: {reference}\n"
            f"길이: {format_time(state.total_duration_ms)}\n"
            f"시각 클립 {len(state.visual_clips)}개 · 오디오 {audio_count}개 · "
            f"텍스트 {len(state.text_clips)}개"
        )

    def _refresh_actions(self) -> None:
        state = self.controller.state
        clip = self.controller.selected_clip
        asset = self.controller.selected_asset
        locked = state.export_state in {ExportState.RUNNING, ExportState.CANCELLING}
        self._set_action("save", not locked, "출력 중에는 프로젝트를 저장할 수 없습니다")
        self._set_action("save_as", not locked, "출력 중에는 프로젝트를 저장할 수 없습니다")
        self._set_action("import", not locked, "출력 중에는 미디어를 가져올 수 없습니다")
        self._set_action(
            "remove_asset",
            asset is not None and not locked,
            "보관함에서 제거할 미디어를 선택하세요",
        )
        self._set_action(
            "export",
            bool(state.visual_clips) and not locked,
            "동영상 저장에는 영상 또는 사진 클립이 필요합니다",
        )
        self._set_action("undo", self.controller.can_undo and not locked, "되돌릴 편집이 없습니다")
        self._set_action("redo", self.controller.can_redo and not locked, "다시 실행할 편집이 없습니다")
        self._actions["undo"].setText(
            f"실행 취소: {self.controller.undo_label}"
            if self.controller.undo_label
            else "실행 취소"
        )
        self._actions["redo"].setText(
            f"다시 실행: {self.controller.redo_label}"
            if self.controller.redo_label
            else "다시 실행"
        )
        single_clip = len(state.selected_clip_ids) == 1
        can_split = single_clip and clip is not None and self._can_split_clip(clip) and not locked
        self._set_action(
            "split",
            can_split,
            "클립 내부로 재생 헤드를 이동한 뒤 영상 또는 오디오 클립을 선택하세요",
        )
        self._set_action("delete", clip is not None or asset is not None, "삭제할 항목을 선택하세요")
        self._set_action(
            "duplicate",
            single_clip and clip is not None and not locked,
            "복제할 클립 하나를 선택하세요",
        )
        is_visual = (
            single_clip and clip is not None and clip.track is TrackKind.VISUAL and not locked
        )
        for name in ("move_back", "move_forward", "fit", "fill", "rotate_left", "rotate_right"):
            self._set_action(name, is_visual, "영상 또는 사진 클립을 선택하세요")
        can_transition = False
        if is_visual and clip is not None:
            can_transition = state.visual_clips.index(clip) < len(state.visual_clips) - 1
        self._set_action(
            "transition",
            can_transition,
            "다음 시각 클립이 있는 앞쪽 클립을 선택하세요",
        )
        is_audio_asset = asset is not None and asset.kind is MediaKind.AUDIO and not locked
        self._set_action("add_music", is_audio_asset, "보관함에서 오디오를 선택하세요")
        self._set_action("add_narration", is_audio_asset, "보관함에서 오디오를 선택하세요")
        for name in ("add_title", "add_caption", "add_credits"):
            self._set_action(name, bool(state.visual_clips) and not locked, "시각 클립이 필요합니다")
        ready_asset = asset is not None and asset.status is AssetStatus.READY and not locked
        self.library_add_button.setEnabled(ready_asset)
        self.library_add_button.setToolTip(
            "선택 미디어를 용도에 맞는 트랙에 추가합니다"
            if ready_asset
            else "정상 상태의 미디어를 먼저 선택하세요"
        )
        self.library_remove_button.setEnabled(asset is not None and not locked)
        self.library_relink_button.setVisible(
            asset is not None and asset.status is not AssetStatus.READY
        )
        self.inspector_reset.setEnabled(single_clip and not locked)
        self.play_button.setEnabled(bool(state.visual_clips) and not locked)

    def _set_action(self, name: str, enabled: bool, disabled_reason: str) -> None:
        action = self._actions[name]
        action.setEnabled(enabled)
        action.setToolTip(action.text() if enabled else disabled_reason)
        action.setStatusTip(action.toolTip())

    def _can_split_clip(self, clip: MockClip) -> bool:
        if clip.track is TrackKind.TEXT:
            return False
        asset = self.controller.asset_for_clip(clip)
        if asset is not None and asset.kind is MediaKind.PHOTO:
            return False
        relative = self.controller.state.playhead_ms - clip.start_ms
        return 250 < relative < clip.duration_ms - 250

    def _refresh_export_panel(self) -> None:
        state = self.controller.state
        export_state = state.export_state
        show = export_state not in {ExportState.CLOSED, ExportState.CONFIG}
        self.export_panel.setVisible(show)
        self.export_progress.setValue(state.export_progress)
        self.export_progress.setFormat(f"{state.export_progress}% · 목업")
        self.export_cancel_button.setVisible(export_state is ExportState.RUNNING)
        self.export_cancel_button.setEnabled(export_state is ExportState.RUNNING)
        self.export_result_button.setVisible(
            export_state in {ExportState.CANCELLED, ExportState.COMPLETE, ExportState.FAILED}
        )
        if export_state is ExportState.RUNNING:
            self.export_stage.setText("동영상 저장 중 · 장면 합성 · 목업")
            if not self._export_timer.isActive():
                self._export_timer.start()
        elif export_state is ExportState.CANCELLING:
            self.export_stage.setText("출력을 취소하고 임시 결과를 정리하는 중 · 목업")
        elif export_state is ExportState.CANCELLED:
            self.export_stage.setText("출력 취소됨 · 불완전 파일을 만들지 않았습니다")
            self.export_result_button.setText("닫기")
            self._export_timer.stop()
        elif export_state is ExportState.COMPLETE:
            self.export_stage.setText(f"완료 · {state.export_path} · 목업 파일")
            self.export_result_button.setText("파일 위치 열기 · 목업")
            self._export_timer.stop()
        elif export_state is ExportState.FAILED:
            self.export_stage.setText(
                f"출력 실패 · {state.export_error}\n설정을 확인하고 다시 시도하세요"
            )
            self.export_result_button.setText("설정으로 돌아가기")
            self._export_timer.stop()

    # ------------------------------------------------------------------
    # User intent adapters

    def _select_library_item(
        self,
        current: QListWidgetItem | None,
        _previous: QListWidgetItem | None,
    ) -> None:
        self._showing_transition = False
        asset_id = current.data(Qt.ItemDataRole.UserRole) if current is not None else None
        self.controller.select_asset(asset_id if isinstance(asset_id, str) else None)

    def _select_timeline_items(self, track: TrackKind) -> None:
        self._showing_transition = False
        widget = self._timeline_lists[track]
        selected_ids = [
            clip_id
            for item in widget.selectedItems()
            if isinstance((clip_id := item.data(Qt.ItemDataRole.UserRole)), str)
        ]
        current = widget.currentItem()
        active_value = current.data(Qt.ItemDataRole.UserRole) if current is not None else None
        active_id = active_value if isinstance(active_value, str) else None
        for other_track, widget in self._timeline_lists.items():
            if other_track is track:
                continue
            blocker = QSignalBlocker(widget)
            widget.clearSelection()
            widget.setCurrentRow(-1)
            del blocker
        self.controller.select_clips(selected_ids, active_id)

    def _change_timeline_mode(self, text: str) -> None:
        if text:
            self.controller.set_timeline_mode(text)

    def _apply_project_name(self) -> None:
        self.controller.rename_project(self.project_name_edit.text())

    def _apply_canvas_mode(self, display_mode: str) -> None:
        mode = "4:3" if display_mode.startswith("4:3") else display_mode
        if not self.controller.set_canvas_mode(mode):
            self.refresh()

    def _apply_clip_properties(self) -> None:
        speed_text = self.clip_speed.currentText().replace("×", "")
        speed = float(speed_text)
        self.controller.update_selected_clip(
            duration_ms=round(self.clip_duration.value() * 1_000),
            speed=speed,
            volume=self.clip_volume.value(),
            muted=self.clip_mute.isChecked(),
            fit_mode=self.clip_fit.currentText(),
            effect=self.clip_effect.currentText(),
            source_in_ms=round(self.clip_in.value() * 1_000),
            source_out_ms=round(self.clip_out.value() * 1_000),
        )

    def _nudge_trim(self, edge: str) -> None:
        if edge == "start":
            self.clip_in.setValue(self.clip_in.value() + 0.5)
        else:
            self.clip_out.setValue(max(self.clip_in.value() + 0.5, self.clip_out.value() - 0.5))
        self._apply_clip_properties()

    @staticmethod
    def _duration_choice(milliseconds: int) -> str:
        return {0: "없음", 500: "0.5초", 1_000: "1초", 2_000: "2초"}.get(
            milliseconds,
            "없음",
        )

    @staticmethod
    def _duration_choice_ms(choice: str) -> int:
        return {"없음": 0, "0.5초": 500, "1초": 1_000, "2초": 2_000}[choice]

    def _apply_audio_properties(self) -> None:
        self.controller.update_selected_audio(
            start_ms=round(self.audio_start.value() * 1_000),
            source_in_ms=round(self.audio_in.value() * 1_000),
            source_out_ms=round(self.audio_out.value() * 1_000),
            volume=self.audio_volume.value(),
            muted=self.audio_mute.isChecked(),
            fade_in_ms=self._duration_choice_ms(self.audio_fade_in.currentText()),
            fade_out_ms=self._duration_choice_ms(self.audio_fade_out.currentText()),
            ducking=self.audio_ducking.currentText(),
        )

    def _apply_text_properties(self) -> None:
        self.controller.update_selected_text(
            content=self.text_content.toPlainText(),
            position=self.text_position.currentText(),
            animation=self.text_animation.currentText(),
            duration_ms=round(self.text_duration.value() * 1_000),
            kind=self.text_kind.currentText(),
            start_ms=round(self.text_start.value() * 1_000),
            font=self.text_font.currentText(),
            size=self.text_size.value(),
            bold=self.text_bold.isChecked(),
            color=self.text_color.currentText(),
            alignment=self.text_alignment.currentText(),
        )

    def _add_transition_and_show(self) -> None:
        if self.controller.add_transition():
            self._showing_transition = True
            self._refresh_inspector()

    def _apply_transition_properties(self) -> None:
        if self.controller.update_transition_for_selected(
            self.transition_type.currentText(),
            round(self.transition_duration.value() * 1_000),
        ):
            self._showing_transition = True
            self._refresh_inspector()

    def _show_clip_inspector(self) -> None:
        self._showing_transition = False
        self._refresh_inspector()

    def _delete_contextual(self) -> None:
        focused = QApplication.focusWidget()
        if focused is self.library_list or (
            focused is not None and self.library_list.isAncestorOf(focused)
        ):
            self._request_remove_asset()
        elif self.controller.selected_clip is not None:
            self.controller.delete_selected_clip()
        elif self.controller.selected_asset is not None:
            self._request_remove_asset()

    def _guard_unsaved(self, continuation: Callable[[], None]) -> None:
        if not self.controller.state.is_dirty:
            continuation()
            return

        def save_then_continue() -> None:
            if self._request_save():
                continuation()

        def discard_then_continue() -> None:
            continuation()

        dialog = DecisionDialog(
            title="저장하지 않은 변경",
            heading=f"‘{self.controller.state.project_name}’의 변경을 저장할까요?",
            body="계속하면 현재 편집 상태가 바뀝니다. 원본 미디어는 삭제되지 않습니다.",
            actions=[
                ("저장하고 계속", save_then_continue, True),
                ("저장하지 않고 계속", discard_then_continue, False),
                ("취소", lambda: None, False),
            ],
            parent=self,
        )
        self._show_dialog(dialog)

    def _request_new_project(self) -> None:
        self._guard_unsaved(self.controller.new_project)

    def _request_open_project(self) -> None:
        self._guard_unsaved(self._open_selected_project)

    def _choose_project_to_open(self) -> str | None:
        path, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "프로젝트 열기",
            "",
            "Movie Maker 프로젝트 (*.mmrproj);;JSON 파일 (*.json);;모든 파일 (*)",
        )
        return path or None

    def _choose_project_to_save(self, current_path: str | None) -> str | None:
        path, _selected_filter = QFileDialog.getSaveFileName(
            self,
            "프로젝트 저장",
            current_path or "제목 없음.mmrproj",
            "Movie Maker 프로젝트 (*.mmrproj);;JSON 파일 (*.json)",
        )
        return path or None

    def _request_save(self, *, save_as: bool = False) -> bool:
        target = self.controller.state.project_path
        if save_as or target is None:
            target = self._project_save_selector(self.controller.state.project_path)
            if target is None:
                self.controller.report_status("프로젝트 저장을 취소했습니다")
                return False
        if self.controller.save_project(target):
            return True
        self._show_project_error("프로젝트를 저장하지 못했습니다")
        return False

    def _open_selected_project(self) -> None:
        path = self._project_open_selector()
        if path is None:
            self.controller.report_status("프로젝트 열기를 취소했습니다")
            return
        if not self.controller.open_project(path):
            self._show_project_error("프로젝트를 열지 못했습니다")

    def _show_project_error(self, heading: str) -> None:
        detail = self.controller.last_persistence_error or "파일을 확인하고 다시 시도하세요."
        dialog = DecisionDialog(
            title="프로젝트 파일 오류",
            heading=heading,
            body=f"{detail}\n현재 프로젝트와 기존 정상 파일은 변경되지 않았습니다.",
            actions=[("확인", lambda: None, True)],
            parent=self,
        )
        self._show_dialog(dialog)

    def _choose_media_files(self) -> Sequence[str]:
        files, _selected_filter = QFileDialog.getOpenFileNames(
            self,
            "미디어 가져오기",
            "",
            MEDIA_FILE_FILTER,
        )
        return files

    def _import_media(self) -> None:
        self.controller.import_media_files(tuple(self._media_file_selector()))
        self.library_filter.setCurrentText("전체")

    def _request_remove_asset(self) -> None:
        asset = self.controller.selected_asset
        if asset is None:
            self.controller.remove_selected_asset()
            return
        usage_count = self.controller.asset_usage_count(asset.asset_id)
        if usage_count == 0:
            self.controller.remove_selected_asset()
            return
        self.controller.remove_selected_asset()
        dialog = DecisionDialog(
            title="사용 중인 미디어 제거",
            heading=f"‘{asset.name}’은 아직 제거할 수 없습니다",
            body=(
                f"타임라인의 관련 클립 {usage_count}개가 이 파일을 사용하고 있습니다. "
                "관련 클립을 먼저 제거한 뒤 다시 시도하세요. 컴퓨터의 원본 파일은 "
                "변경되지 않았습니다."
            ),
            actions=[
                ("확인", lambda: None, True),
            ],
            parent=self,
        )
        self._show_dialog(dialog)

    def _open_export_settings(self) -> None:
        if not self.controller.open_export_configuration():
            return
        dialog = ExportSettingsDialog(self.controller.state, self)
        dialog.export_requested.connect(self._begin_export)
        self._show_dialog(dialog)

    def _begin_export(self, path: str, preset: str, framerate: str, quality: str) -> None:
        self.controller.configure_export(
            path=path,
            preset=preset,
            framerate=framerate,
            quality=quality,
        )
        self.controller.start_export()

    def _request_cancel_export(self) -> None:
        dialog = DecisionDialog(
            title="동영상 저장 취소",
            heading="진행 중인 출력을 취소할까요?",
            body="목업에서는 다음 진행 주기에 작업을 멈추고 불완전 파일이 없다고 표시합니다.",
            actions=[
                ("출력 취소", self.controller.cancel_export, False),
                ("계속 출력", lambda: None, True),
            ],
            parent=self,
        )
        self._show_dialog(dialog)

    def _handle_export_result(self) -> None:
        if self.controller.state.export_state is ExportState.FAILED:
            self.controller.close_export_result()
            self._open_export_settings()
            return
        if self.controller.state.export_state is ExportState.COMPLETE:
            self.controller.report_status(
                "목업 결과이므로 파일 위치를 열지 않았습니다"
            )
        self.controller.close_export_result()

    def _inject_and_show_missing(self) -> None:
        self.controller.inject_missing_media()
        dialog = MissingMediaDialog(self.controller.state, self)
        dialog.relink_requested.connect(self.controller.relink_selected_asset)
        self._show_dialog(dialog)

    def _open_recovery(self) -> None:
        dialog = RecoveryDialog(self)
        dialog.choice_made.connect(self._handle_recovery_choice)
        self._show_dialog(dialog)

    def _handle_recovery_choice(self, choice: str) -> None:
        self.controller.apply_recovery_choice(choice)

    def _open_project_version_message(self) -> None:
        dialog = DecisionDialog(
            title="프로젝트 버전 확인 · 목업",
            heading="이 프로젝트는 더 새로운 버전에서 만들어졌습니다",
            body=(
                "현재 버전에서는 안전하게 열 수 없습니다. 원본 프로젝트는 변경하지 않았습니다. "
                "지원되는 앱 버전으로 다시 시도하세요."
            ),
            actions=[("닫기", lambda: None, True)],
            body_object_name="E-PROJECT-VERSION-MESSAGE",
            parent=self,
        )
        self._show_dialog(dialog)

    def _open_narration(self) -> None:
        dialog = NarrationDialog(self)
        dialog.recording_added.connect(self.controller.add_recorded_narration)
        self._show_dialog(dialog)

    def _open_onboarding(self) -> None:
        dialog = OnboardingDialog(self)
        dialog.example_requested.connect(self.controller.load_sample_project)
        self._show_dialog(dialog)

    def _open_runtime(self) -> None:
        report = [
            f"Python: {sys.version.split()[0]}",
            f"Qt: {qVersion()}",
            f"PySide6: {version('PySide6')}",
            "Environment: uv managed · locked · no implicit sync",
        ]
        self._show_dialog(RuntimeDialog(report, self))

    # ------------------------------------------------------------------
    # Native window events and presentation

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        if event.mimeData().hasUrls():
            self.controller.report_status(
                "드래그 앤 드롭 가져오기는 1.0 범위입니다 · 가져오기 버튼을 사용하세요"
            )
        event.ignore()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._allow_close or not self.controller.state.is_dirty:
            event.accept()
            return
        event.ignore()

        def save_and_close() -> None:
            if self._request_save():
                self._allow_close = True
                self.close()

        def discard_and_close() -> None:
            self._allow_close = True
            self.close()

        dialog = DecisionDialog(
            title="프로젝트 닫기",
            heading=f"‘{self.controller.state.project_name}’의 변경을 저장할까요?",
            body="취소하면 편집기와 현재 상태가 그대로 유지됩니다.",
            actions=[
                ("저장하고 닫기", save_and_close, True),
                ("저장하지 않고 닫기", discard_and_close, False),
                ("취소", lambda: None, False),
            ],
            parent=self,
        )
        self._show_dialog(dialog)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if not hasattr(self, "inspector_dock"):
            return
        if event.size().width() < 1160 and self.inspector_dock.isVisible():
            self._responsive_hidden_inspector = True
            self.inspector_dock.hide()
        elif event.size().width() >= 1160 and self._responsive_hidden_inspector:
            self._responsive_hidden_inspector = False
            self.inspector_dock.show()

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow, QWidget {
                background: #f5f7fa;
                color: #172033;
                font-family: "Segoe UI", "Malgun Gothic";
                font-size: 10pt;
            }
            QMenuBar, QMenu, QToolBar, QStatusBar {
                background: #ffffff;
            }
            QMenuBar { border-bottom: 1px solid #d7dde7; }
            QToolBar {
                border: 0;
                border-bottom: 1px solid #d7dde7;
                spacing: 3px;
                padding: 5px;
            }
            QToolBar QToolButton { padding: 6px 8px; border-radius: 5px; }
            QToolBar QToolButton:hover { background: #e8eef8; }
            QDockWidget { font-weight: 600; color: #23304a; }
            QDockWidget::title {
                background: #eef2f7;
                border-bottom: 1px solid #d7dde7;
                padding: 8px;
            }
            QFrame[role="panel"], QFrame[role="task"] {
                background: #ffffff;
                border: 1px solid #d7dde7;
                border-radius: 8px;
            }
            QFrame[role="start"] {
                background: #ffffff;
                border: 1px solid #d7dde7;
                border-radius: 10px;
            }
            QLabel#startHeading { font-size: 24pt; font-weight: 700; color: #18233a; }
            QLabel#onboardingTitle { font-size: 20pt; font-weight: 700; }
            QLabel#eyebrow { color: #376fbd; font-weight: 700; letter-spacing: 1px; }
            QLabel#panelHeading, QLabel#dialogHeading {
                font-size: 13pt;
                font-weight: 700;
                color: #172033;
            }
            QLabel[role="inspectorHeading"] { font-size: 12pt; font-weight: 700; }
            QLabel#secondaryText, QLabel[role="caption"] { color: #657083; }
            QLabel#mockBadge {
                color: #4e3c83;
                background: #eee9ff;
                border: 1px solid #d4c8ff;
                border-radius: 8px;
                padding: 3px 7px;
            }
            QLabel[role="summary"], QLabel[role="info"], QLabel[role="success"],
            QLabel[role="warning"] {
                border-radius: 6px;
                padding: 9px;
            }
            QLabel[role="summary"], QLabel[role="info"] {
                background: #edf4ff;
                border: 1px solid #cbdcf6;
            }
            QLabel[role="success"] {
                background: #e8f6ef;
                border: 1px solid #b8e1ca;
                color: #17603a;
            }
            QLabel[role="warning"] {
                background: #fff4dc;
                border: 1px solid #f0d49a;
                color: #7a5011;
            }
            QPushButton {
                background: #ffffff;
                border: 1px solid #bfc8d5;
                border-radius: 5px;
                padding: 6px 10px;
            }
            QPushButton:hover { background: #edf4ff; border-color: #6f9bd4; }
            QPushButton:focus { border: 2px solid #2f6fbe; padding: 5px 9px; }
            QPushButton:disabled { color: #9299a5; background: #edf0f4; }
            QPushButton[primary="true"] {
                background: #2868b2;
                border-color: #2868b2;
                color: white;
                font-weight: 600;
            }
            QPushButton[primary="true"]:hover { background: #1f5798; }
            QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTextEdit, QListWidget {
                background: #ffffff;
                border: 1px solid #c5cedb;
                border-radius: 4px;
                padding: 4px;
                selection-background-color: #3777c6;
            }
            QListWidget::item { border-radius: 5px; padding: 3px; }
            QListWidget::item:selected {
                background: #dcecff;
                color: #10243f;
                border: 2px solid #2f6fbe;
            }
            QSlider::groove:horizontal { height: 4px; background: #cbd3df; }
            QSlider::handle:horizontal {
                width: 14px;
                margin: -6px 0;
                border-radius: 7px;
                background: #2f6fbe;
            }
            QProgressBar {
                border: 1px solid #bfc8d5;
                border-radius: 5px;
                background: white;
                text-align: center;
                min-height: 20px;
            }
            QProgressBar::chunk { background: #3b7fc4; border-radius: 4px; }
            """
        )
