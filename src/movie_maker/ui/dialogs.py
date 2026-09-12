"""Dialogs used to exercise screen contracts in the interactive mock-up."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from movie_maker.ui.mock_model import MockProjectState

ExportPathSelector = Callable[[str], str | None]


class ExportSettingsDialog(QDialog):
    """S-EXPORT-SETTINGS for the real fixed-policy MP4 renderer."""

    export_requested = Signal(str, str, str, str)

    def __init__(
        self,
        state: MockProjectState,
        parent: QWidget | None = None,
        *,
        path_selector: ExportPathSelector | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("S-EXPORT-SETTINGS")
        self.setWindowTitle("동영상 저장")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setMinimumWidth(560)

        layout = QVBoxLayout(self)
        heading = QLabel("동영상 저장")
        heading.setObjectName("dialogHeading")
        layout.addWidget(heading)
        explanation = QLabel(
            "편집 결과를 H.264 영상과 AAC 오디오가 포함된 실제 MP4 파일로 저장합니다."
        )
        explanation.setWordWrap(True)
        explanation.setObjectName("secondaryText")
        layout.addWidget(explanation)

        form = QFormLayout()
        self.path_edit = QLineEdit(state.export_path)
        self.path_edit.setObjectName("E-EXPORT-PATH")
        path_row = QHBoxLayout()
        path_row.addWidget(self.path_edit)
        browse_button = QPushButton("위치 선택…")
        browse_button.setToolTip("MP4 파일을 저장할 위치를 선택합니다")
        browse_button.clicked.connect(self._choose_path)
        path_row.addWidget(browse_button)
        form.addRow("파일", path_row)

        self.preset_combo = QComboBox()
        self.preset_combo.setObjectName("E-EXPORT-PRESET")
        self.preset_combo.addItems(["원본 유지", "720p", "1080p"])
        self.preset_combo.setCurrentText(state.export_preset)
        self.preset_combo.currentTextChanged.connect(self._refresh_summary)
        form.addRow("출력 크기", self.preset_combo)

        self.framerate_combo = QComboBox()
        self.framerate_combo.setObjectName("E-EXPORT-FRAMERATE")
        self.framerate_combo.addItem("30 fps")
        self.framerate_combo.setEnabled(False)
        form.addRow("프레임률", self.framerate_combo)

        self.quality_combo = QComboBox()
        self.quality_combo.setObjectName("E-EXPORT-QUALITY")
        self.quality_combo.addItem("권장")
        self.quality_combo.setEnabled(False)
        form.addRow("품질", self.quality_combo)
        layout.addLayout(form)

        self.original_summary = QLabel()
        self.original_summary.setObjectName("E-EXPORT-ORIGINAL-SUMMARY")
        self.original_summary.setWordWrap(True)
        self.original_summary.setProperty("role", "info")
        layout.addWidget(self.original_summary)

        self.summary = QLabel()
        self.summary.setObjectName("E-EXPORT-SUMMARY")
        self.summary.setWordWrap(True)
        self.summary.setProperty("role", "summary")
        layout.addWidget(self.summary)

        buttons = QDialogButtonBox()
        self.start_button = buttons.addButton(
            "저장 시작",
            QDialogButtonBox.ButtonRole.AcceptRole,
        )
        self.start_button.setObjectName("E-EXPORT-START")
        cancel_button = buttons.addButton(
            "취소",
            QDialogButtonBox.ButtonRole.RejectRole,
        )
        cancel_button.setObjectName("E-EXPORT-CANCEL")
        buttons.accepted.connect(self._request_export)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._state = state
        self._path_selector = path_selector
        self._refresh_summary()

    def _choose_path(self) -> None:
        if self._path_selector is None:
            return
        selected = self._path_selector(self.path_edit.text())
        if selected is not None:
            self.path_edit.setText(selected)

    def _refresh_summary(self) -> None:
        preset = self.preset_combo.currentText()
        if preset == "720p":
            size = "1280×720 · 16:9"
            reference = "고정된 16:9 화면에 모든 미디어를 맞춥니다."
        elif preset == "1080p":
            size = "1920×1080 · 16:9"
            reference = "고정된 16:9 화면에 모든 미디어를 맞춥니다."
        else:
            width = self._state.canvas_width or 0
            height = self._state.canvas_height or 0
            size = f"{width}×{height} · 프로젝트 원본 화면"
            reference_name = "없음"
            if self._state.reference_asset_id:
                asset = self._state.assets.get(self._state.reference_asset_id)
                reference_name = asset.name if asset is not None else "제거됨"
            reference = (
                f"기준 미디어: {reference_name}. 다른 비율의 미디어는 전체가 보이도록 "
                "맞추고 남는 영역에 중립 배경을 사용합니다."
            )
        self.original_summary.setText(reference)
        seconds = self._state.total_duration_ms / 1_000
        self.summary.setText(
            f"MP4 · H.264/AAC\n{size} · {self.framerate_combo.currentText()} · "
            f"{seconds:.1f}초 · 예상 크기는 원본 내용에 따라 달라집니다"
        )

    def _request_export(self) -> None:
        path = self.path_edit.text().strip()
        if not path:
            self.path_edit.setFocus()
            self.path_edit.setPlaceholderText("출력 파일 위치가 필요합니다")
            return
        self.export_requested.emit(
            path,
            self.preset_combo.currentText(),
            self.framerate_combo.currentText(),
            self.quality_combo.currentText(),
        )
        self.accept()


class DecisionDialog(QDialog):
    """S-MESSAGE with explicit verbs rather than ambiguous yes/no buttons."""

    def __init__(
        self,
        *,
        title: str,
        heading: str,
        body: str,
        actions: list[tuple[str, Callable[[], object], bool]],
        body_object_name: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("S-MESSAGE")
        self.setWindowTitle(title)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)
        heading_label = QLabel(heading)
        heading_label.setObjectName("dialogHeading")
        layout.addWidget(heading_label)
        body_label = QLabel(body)
        body_label.setWordWrap(True)
        body_label.setObjectName(body_object_name or "secondaryText")
        layout.addWidget(body_label)
        button_row = QHBoxLayout()
        button_row.addStretch()
        for text, callback, primary in actions:
            button = QPushButton(text)
            if primary:
                button.setDefault(True)
                button.setProperty("primary", True)
            button.clicked.connect(self._wrapped(callback))
            button_row.addWidget(button)
        layout.addLayout(button_row)

    def _wrapped(self, callback: Callable[[], object]) -> Callable[[], None]:
        def invoke() -> None:
            self.accept()
            callback()

        return invoke


class MissingMediaDialog(QDialog):
    """S-MISSING-MEDIA showing impact and a mock relink route."""

    relink_requested = Signal()

    def __init__(self, state: MockProjectState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("S-MISSING-MEDIA")
        self.setWindowTitle("누락 미디어")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setMinimumSize(540, 320)
        layout = QVBoxLayout(self)
        heading = QLabel("원본 미디어를 찾을 수 없습니다")
        heading.setObjectName("dialogHeading")
        layout.addWidget(heading)
        body = QLabel(
            "프로젝트 구조와 편집점은 유지됩니다. MVP에서는 누락 상태로 열 수 있고, "
            "1.0에서는 새 원본을 다시 연결할 수 있습니다."
        )
        body.setWordWrap(True)
        body.setObjectName("secondaryText")
        layout.addWidget(body)
        self.items = QListWidget()
        self.items.setObjectName("E-MISSING-LIST")
        missing = [asset for asset in state.assets.values() if asset.status.value != "준비됨"]
        for asset in missing:
            affected = sum(clip.asset_id == asset.asset_id for clip in state.all_clips)
            self.items.addItem(
                f"⚠ {asset.name}\n마지막 위치: {asset.source_path}\n영향 받는 클립: {affected}개"
            )
        if not missing:
            self.items.addItem("현재 누락되거나 읽기 오류인 미디어가 없습니다.")
        layout.addWidget(self.items)
        buttons = QHBoxLayout()
        relink = QPushButton("선택 파일 다시 연결 · 1.0")
        relink.setObjectName("E-MISSING-RELINK")
        relink.setEnabled(bool(missing))
        relink.clicked.connect(self.relink_requested.emit)
        relink.clicked.connect(self.accept)
        buttons.addWidget(relink)
        buttons.addStretch()
        open_missing = QPushButton("누락 상태로 열기")
        open_missing.setObjectName("E-MISSING-OPEN")
        open_missing.setDefault(True)
        open_missing.clicked.connect(self.accept)
        buttons.addWidget(open_missing)
        cancel = QPushButton("프로젝트 열기 취소")
        cancel.setObjectName("E-MISSING-CANCEL")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        layout.addLayout(buttons)


class RecoveryDialog(QDialog):
    """S-RECOVERY mock comparing normal and automatic saves."""

    choice_made = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("S-RECOVERY")
        self.setWindowTitle("프로젝트 복구 · 목업")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        heading = QLabel("저장되지 않은 작업을 복구할 수 있습니다")
        heading.setObjectName("dialogHeading")
        layout.addWidget(heading)
        comparison = QFrame()
        comparison.setObjectName("E-RECOVERY-COMPARISON")
        comparison.setProperty("role", "summary")
        comparison_layout = QVBoxLayout(comparison)
        comparison_layout.addWidget(QLabel("자동 저장본 · 오늘 14:32 · 분할/삭제 후 3개 변경"))
        comparison_layout.addWidget(QLabel("정상 저장본 · 오늘 14:26 · 제주 여행 목업"))
        layout.addWidget(comparison)
        note = QLabel("복구본을 선택해도 정상 저장 파일을 즉시 덮어쓰지 않습니다.")
        note.setObjectName("secondaryText")
        layout.addWidget(note)
        row = QHBoxLayout()
        auto_button = QPushButton("자동 저장본 열기")
        auto_button.setObjectName("E-RECOVERY-AUTO")
        auto_button.setDefault(True)
        auto_button.clicked.connect(lambda: self._choose("자동 저장본"))
        row.addWidget(auto_button)
        normal_button = QPushButton("정상 저장본 열기")
        normal_button.setObjectName("E-RECOVERY-NORMAL")
        normal_button.clicked.connect(lambda: self._choose("정상 저장본"))
        row.addWidget(normal_button)
        later_button = QPushButton("나중에 결정")
        later_button.setObjectName("E-RECOVERY-LATER")
        later_button.clicked.connect(self.reject)
        row.addWidget(later_button)
        layout.addLayout(row)

    def _choose(self, choice: str) -> None:
        self.choice_made.emit(choice)
        self.accept()


class NarrationDialog(QDialog):
    """S-NARRATION with a fake input meter and recording timer."""

    recording_added = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("S-NARRATION")
        self.setWindowTitle("내레이션 녹음 · 목업")
        self.setMinimumWidth(500)
        self._elapsed_ms = 0
        self._is_recording = False
        self._timer = QTimer(self)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self._tick)

        layout = QVBoxLayout(self)
        heading = QLabel("내레이션 녹음")
        heading.setObjectName("dialogHeading")
        layout.addWidget(heading)
        form = QFormLayout()
        device = QComboBox()
        device.setObjectName("E-NARRATION-DEVICE")
        device.addItems(["기본 마이크 · 목업", "장치 없음 상태 · 목업"])
        form.addRow("입력 장치", device)
        self.level = QProgressBar()
        self.level.setObjectName("E-NARRATION-LEVEL")
        self.level.setRange(0, 100)
        self.level.setValue(28)
        self.level.setFormat("입력 수준 %p% · 목업")
        form.addRow("입력 수준", self.level)
        self.time_label = QLabel("00:00.000")
        self.time_label.setObjectName("E-NARRATION-TIME")
        form.addRow("녹음 시간", self.time_label)
        layout.addLayout(form)
        note = QLabel("실제 마이크나 파일을 사용하지 않습니다. 완료하면 5초 샘플이 추가됩니다.")
        note.setWordWrap(True)
        note.setObjectName("secondaryText")
        layout.addWidget(note)
        row = QHBoxLayout()
        self.record_button = QPushButton("녹음 시작")
        self.record_button.setObjectName("E-NARRATION-RECORD")
        self.record_button.clicked.connect(self._toggle_recording)
        row.addWidget(self.record_button)
        self.finish_button = QPushButton("완료하고 프로젝트에 추가")
        self.finish_button.setObjectName("E-NARRATION-FINISH")
        self.finish_button.setEnabled(False)
        self.finish_button.clicked.connect(self._finish)
        row.addWidget(self.finish_button)
        cancel = QPushButton("취소")
        cancel.setObjectName("E-NARRATION-CANCEL")
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        layout.addLayout(row)

    def _toggle_recording(self) -> None:
        self._is_recording = not self._is_recording
        if self._is_recording:
            self.record_button.setText("일시 정지")
            self._timer.start()
        else:
            self.record_button.setText("녹음 계속")
            self._timer.stop()

    def _tick(self) -> None:
        self._elapsed_ms += 250
        seconds, millis = divmod(self._elapsed_ms, 1_000)
        minutes, seconds = divmod(seconds, 60)
        self.time_label.setText(f"{minutes:02d}:{seconds:02d}.{millis:03d}")
        self.level.setValue(25 + (self._elapsed_ms // 250 * 17) % 58)
        self.finish_button.setEnabled(True)

    def _finish(self) -> None:
        self._timer.stop()
        self.recording_added.emit()
        self.accept()


class OnboardingDialog(QDialog):
    """S-ONBOARDING short, skippable introduction."""

    example_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("S-ONBOARDING")
        self.setWindowTitle("첫 영상 만들기 · 목업")
        self.setMinimumSize(600, 390)
        layout = QVBoxLayout(self)
        self.pages = QStackedWidget()
        self.pages.setObjectName("E-ONBOARDING-STEPS")
        steps = [
            ("1 · 미디어 가져오기", "영상, 사진과 음악을 왼쪽 보관함에 모읍니다."),
            ("2 · 배치하고 다듬기", "타임라인에 놓고 분할, 삭제와 트리밍으로 필요한 부분만 남깁니다."),
            ("3 · 저장하고 출력하기", "프로젝트를 저장한 뒤 원본 유지 또는 1080p MP4로 출력합니다."),
        ]
        for title, body in steps:
            page = QWidget()
            page_layout = QVBoxLayout(page)
            page_layout.addStretch()
            title_label = QLabel(title)
            title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            title_label.setObjectName("onboardingTitle")
            page_layout.addWidget(title_label)
            body_label = QLabel(body)
            body_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            body_label.setWordWrap(True)
            body_label.setObjectName("secondaryText")
            page_layout.addWidget(body_label)
            page_layout.addStretch()
            self.pages.addWidget(page)
        layout.addWidget(self.pages)
        row = QHBoxLayout()
        example = QPushButton("예제 프로젝트 열기")
        example.setObjectName("E-ONBOARDING-EXAMPLE")
        example.clicked.connect(self.example_requested.emit)
        example.clicked.connect(self.accept)
        row.addWidget(example)
        row.addStretch()
        skip = QPushButton("건너뛰기")
        skip.setObjectName("E-ONBOARDING-SKIP")
        skip.clicked.connect(self.reject)
        row.addWidget(skip)
        self.next_button = QPushButton("다음")
        self.next_button.setObjectName("E-ONBOARDING-NEXT")
        self.next_button.setDefault(True)
        self.next_button.clicked.connect(self._next)
        row.addWidget(self.next_button)
        layout.addLayout(row)

    def _next(self) -> None:
        next_index = self.pages.currentIndex() + 1
        if next_index >= self.pages.count():
            self.accept()
            return
        self.pages.setCurrentIndex(next_index)
        if next_index == self.pages.count() - 1:
            self.next_button.setText("시작하기")


class RuntimeDialog(QDialog):
    """S-RUNTIME overview; actual environment management remains out of scope."""

    def __init__(self, report: list[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("S-RUNTIME")
        self.setWindowTitle("실행 준비 및 진단 · 목업")
        self.setMinimumSize(620, 460)
        layout = QVBoxLayout(self)
        heading = QLabel("실행 환경 준비됨")
        heading.setObjectName("dialogHeading")
        layout.addWidget(heading)
        checklist = QLabel(
            "✓ uv 잠금 환경   ✓ Python   ✓ Qt/PySide6\n"
            "✓ FFmpeg/ffprobe · 설정 단계에서 검증됨   ✓ 애플리케이션"
        )
        checklist.setObjectName("E-RUNTIME-STATUS")
        checklist.setProperty("role", "success")
        checklist.setWordWrap(True)
        layout.addWidget(checklist)
        log = QTextEdit()
        log.setObjectName("E-RUNTIME-LOG")
        log.setReadOnly(True)
        log.setPlainText("\n".join(report) + "\nFFmpeg: 목업 경로 · 준비됨")
        layout.addWidget(log)
        options_form = QFormLayout()
        options = QLineEdit("--check 이후 일반 실행 · 목업")
        options.setObjectName("E-RUNTIME-OPTIONS")
        options_form.addRow("앱 옵션 · 1.0", options)
        layout.addLayout(options_form)
        row = QHBoxLayout()
        prepare = QPushButton("환경 복구 · 1.0")
        prepare.setObjectName("E-RUNTIME-PREPARE")
        prepare.setToolTip("목업에서는 상태 메시지만 확인합니다")
        prepare.clicked.connect(
            lambda: checklist.setText("✓ 환경 복구 확인 완료 · 실제 설치는 변경하지 않음 · 목업")
        )
        row.addWidget(prepare)
        launch = QPushButton("앱 실행 · 1.0")
        launch.setObjectName("E-RUNTIME-LAUNCH")
        launch.clicked.connect(
            lambda: checklist.setText("✓ 잠금 환경에서 앱 실행 요청 완료 · 목업")
        )
        row.addWidget(launch)
        maintenance = QPushButton("설치·업데이트·제거 · 1.0")
        maintenance.setObjectName("E-RUNTIME-MAINTENANCE")
        maintenance.setToolTip("네이티브 런처 구현 단계에서 연결합니다")
        row.addWidget(maintenance)
        row.addStretch()
        close = QPushButton("닫기")
        close.setDefault(True)
        close.clicked.connect(self.accept)
        row.addWidget(close)
        layout.addLayout(row)
