#include "launcher_core.hpp"

#include <commctrl.h>
#include <commdlg.h>
#include <shellapi.h>
#include <shlobj.h>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <filesystem>
#include <fstream>
#include <memory>
#include <sstream>
#include <thread>
#include <vector>

namespace {

constexpr wchar_t kWindowClass[] = L"MovieMakerReproductionLauncherWindow";
constexpr UINT kWorkerComplete = WM_APP + 1;
constexpr int kInspect = 1001;
constexpr int kPrepare = 1002;
constexpr int kRun = 1003;
constexpr int kCancel = 1004;
constexpr int kLogs = 1005;
constexpr int kMaintenance = 1006;
constexpr int kBrowseFfmpeg = 1007;
constexpr int kBrowseProject = 1008;
constexpr int kOnline = 1009;
constexpr int kFfmpegEdit = 1010;
constexpr int kProjectEdit = 1011;
constexpr int kComponents = 1012;
constexpr int kDetails = 1013;
constexpr int kStatus = 1014;
constexpr int kReason = 1015;

enum class Task { inspect, prepare, run };

struct Component {
    std::wstring name;
    std::wstring state;
    std::wstring summary;
    std::wstring detail;
    std::wstring executable_path;
    std::wstring version;
};

struct Snapshot {
    bool valid = false;
    std::wstring overall = L"NotReady";
    std::wstring summary;
    std::wstring app_root;
    std::vector<Component> components;
};

struct WorkerResult {
    Task task;
    mmr::ProcessResult process;
    Snapshot snapshot;
    std::wstring message;
    bool recovery_attempted = false;
    bool recovery_succeeded = false;
};

struct State {
    HWND window = nullptr;
    HWND status = nullptr;
    HWND reason = nullptr;
    HWND components = nullptr;
    HWND details = nullptr;
    HWND ffmpeg_edit = nullptr;
    HWND project_edit = nullptr;
    HWND online = nullptr;
    HWND inspect = nullptr;
    HWND prepare = nullptr;
    HWND run = nullptr;
    HWND cancel = nullptr;
    HWND logs = nullptr;
    HWND maintenance = nullptr;
    HWND browse_ffmpeg = nullptr;
    HWND browse_project = nullptr;
    HFONT font = nullptr;
    std::wstring app_root;
    std::wstring log_directory;
    std::wstring log_file;
    mmr::RuntimeContract contract;
    mmr::LauncherOptions options;
    Snapshot snapshot;
    std::thread worker;
    std::atomic_bool cancellation{false};
    bool busy = false;
    bool close_after_worker = false;
};

std::wstring resolve_uv(const State& state) {
    const std::filesystem::path bundled = std::filesystem::path(state.app_root) /
        state.contract.bundled_uv_candidate;
    std::error_code error;
    if (std::filesystem::is_regular_file(bundled, error)) {
        return bundled.wstring();
    }
    return mmr::find_executable(L"uv.exe");
}

std::vector<std::wstring> split(const std::wstring& value, wchar_t delimiter) {
    std::vector<std::wstring> result;
    std::size_t start = 0;
    while (true) {
        const std::size_t end = value.find(delimiter, start);
        if (end == std::wstring::npos) {
            result.push_back(value.substr(start));
            break;
        }
        result.push_back(value.substr(start, end - start));
        start = end + 1;
    }
    return result;
}

Snapshot parse_protocol(const std::string& bytes) {
    Snapshot result;
    std::wistringstream stream(mmr::utf8_to_wide(bytes));
    std::wstring line;
    if (!std::getline(stream, line)) {
        return result;
    }
    if (!line.empty() && line.back() == L'\r') {
        line.pop_back();
    }
    if (line != L"MMR_RUNTIME_V1") {
        return result;
    }
    while (std::getline(stream, line)) {
        if (!line.empty() && line.back() == L'\r') {
            line.pop_back();
        }
        const std::vector<std::wstring> fields = split(line, L'\t');
        if (fields.empty()) {
            continue;
        }
        if (fields[0] == L"overall" && fields.size() >= 4) {
            result.overall = fields[1];
            result.summary = fields[2];
            result.app_root = fields[3];
            result.valid = true;
        } else if (fields[0] == L"component" && fields.size() >= 7) {
            result.components.push_back({
                fields[1], fields[2], fields[3], fields[4], fields[5], fields[6]
            });
        }
    }
    return result;
}

std::wstring component_title(const std::wstring& name) {
    if (name == L"files") return L"앱 파일";
    if (name == L"uv") return L"uv";
    if (name == L"python") return L"고정 Python";
    if (name == L"environment") return L"잠금 환경";
    if (name == L"ffmpeg") return L"FFmpeg / ffprobe";
    return L"런타임 점검";
}

std::wstring state_title(const std::wstring& state) {
    if (state == L"Ready") return L"[✓] 준비됨";
    if (state == L"RepairRequired") return L"[!] 복구 필요";
    return L"[–] 준비되지 않음";
}

std::wstring component_summary(const Component& component) {
    const bool ready = component.state == L"Ready";
    if (component.name == L"files") {
        return ready ? L"필수 앱 파일을 확인했습니다." : L"필수 앱 파일이 누락됐습니다.";
    }
    if (component.name == L"uv") {
        return ready ? L"지원되는 uv를 사용할 수 있습니다." : L"uv 설치 또는 업데이트가 필요합니다.";
    }
    if (component.name == L"python") {
        return ready ? L"고정된 uv 관리형 Python을 확인했습니다." : L"고정 Python 구성이 필요합니다.";
    }
    if (component.name == L"environment") {
        if (ready) return L".venv가 현재 잠금 환경과 일치합니다.";
        return component.state == L"RepairRequired"
            ? L".venv가 손상됐거나 잠금 상태와 다릅니다."
            : L".venv를 처음 구성해야 합니다.";
    }
    if (component.name == L"ffmpeg") {
        return ready
            ? L"같은 배포본의 FFmpeg/ffprobe와 필수 기능을 확인했습니다."
            : L"FFmpeg 경로, 버전, 인코더 또는 필터를 확인하세요.";
    }
    return mmr::utf8_to_wide(mmr::wide_to_utf8(component.summary));
}

std::wstring control_text(HWND control) {
    const int length = GetWindowTextLengthW(control);
    std::wstring value(static_cast<std::size_t>(length + 1), L'\0');
    if (length > 0) {
        GetWindowTextW(control, value.data(), length + 1);
    }
    value.resize(static_cast<std::size_t>(length));
    return value;
}

std::wstring local_app_data() {
    wchar_t path[MAX_PATH]{};
    if (FAILED(SHGetFolderPathW(nullptr, CSIDL_LOCAL_APPDATA, nullptr, SHGFP_TYPE_CURRENT, path))) {
        return {};
    }
    return path;
}

std::wstring timestamp(bool filename) {
    SYSTEMTIME time{};
    GetLocalTime(&time);
    wchar_t buffer[64]{};
    swprintf(
        buffer,
        sizeof(buffer) / sizeof(buffer[0]),
        filename ? L"%04u%02u%02u-%02u%02u%02u-%lu" : L"%04u-%02u-%02uT%02u:%02u:%02u.%03u",
        time.wYear, time.wMonth, time.wDay, time.wHour, time.wMinute, time.wSecond,
        filename ? GetCurrentProcessId() : time.wMilliseconds
    );
    return buffer;
}

std::wstring current_user_profile() {
    wchar_t profile[32768]{};
    const DWORD length = GetEnvironmentVariableW(L"USERPROFILE", profile, 32768);
    return length > 0 && length < 32768 ? std::wstring(profile, length) : std::wstring();
}

void append_log(State& state, const std::wstring& stage, const std::wstring& message) {
    if (state.log_file.empty()) return;
    const std::wstring line = timestamp(false) + L" [" + stage + L"] " +
        mmr::sanitize_diagnostic_text(message, current_user_profile()) + L"\r\n";
    (void)mmr::append_utf8_file(state.log_file, line);
}

void initialize_log(State& state) {
    const std::wstring base = local_app_data();
    if (base.empty()) return;
    state.log_directory = (
        std::filesystem::path(base) / L"OpenAI" / L"MovieMakerReproduction" / L"Logs"
    ).wstring();
    std::error_code error;
    std::filesystem::create_directories(state.log_directory, error);
    if (error) {
        state.log_directory.clear();
        return;
    }

    mmr::prune_launcher_logs(
        state.log_directory,
        state.contract.log_retention_days,
        state.contract.maximum_log_bytes
    );
    state.log_file = (
        std::filesystem::path(state.log_directory) / (L"launcher-" + timestamp(true) + L".log")
    ).wstring();
    append_log(state, L"startup", L"Launcher version " + state.contract.version);
}

HWND create_control(
    State& state,
    DWORD extended,
    const wchar_t* class_name,
    const wchar_t* text,
    DWORD style,
    int id
) {
    const DWORD tab_style = lstrcmpW(class_name, L"STATIC") == 0 ? 0 : WS_TABSTOP;
    HWND control = CreateWindowExW(
        extended, class_name, text, style | WS_CHILD | WS_VISIBLE | tab_style,
        0, 0, 0, 0, state.window, reinterpret_cast<HMENU>(static_cast<INT_PTR>(id)),
        GetModuleHandleW(nullptr), nullptr
    );
    SendMessageW(control, WM_SETFONT, reinterpret_cast<WPARAM>(state.font), TRUE);
    return control;
}

void set_busy(State& state, bool busy, const wchar_t* text = nullptr) {
    state.busy = busy;
    EnableWindow(state.inspect, !busy);
    EnableWindow(state.prepare, !busy);
    EnableWindow(state.run, !busy && state.snapshot.overall == L"Ready");
    EnableWindow(state.cancel, busy);
    EnableWindow(state.browse_ffmpeg, !busy);
    EnableWindow(state.browse_project, !busy);
    if (text != nullptr) SetWindowTextW(state.status, text);
}

void layout(State& state, int width, int height) {
    const UINT dpi = mmr::dpi_for_window(state.window);
    const auto s = [dpi](int value) { return mmr::scale_for_dpi(value, dpi); };
    const int margin = s(18);
    const int inner = width - margin * 2;
    MoveWindow(state.status, margin, s(16), inner, s(30), TRUE);
    MoveWindow(state.reason, margin, s(50), inner, s(38), TRUE);
    MoveWindow(state.components, margin, s(92), inner, s(152), TRUE);
    ListView_SetColumnWidth(state.components, 0, s(132));
    ListView_SetColumnWidth(state.components, 1, s(118));
    ListView_SetColumnWidth(state.components, 2, std::max(s(200), inner - s(250)));
    MoveWindow(state.details, margin, s(252), inner, std::max(s(90), height - s(430)), TRUE);
    const int fields_y = height - s(168);
    MoveWindow(state.ffmpeg_edit, margin, fields_y, inner - s(118), s(25), TRUE);
    MoveWindow(state.browse_ffmpeg, width - margin - s(110), fields_y, s(110), s(25), TRUE);
    MoveWindow(state.project_edit, margin, fields_y + s(32), inner - s(118), s(25), TRUE);
    MoveWindow(state.browse_project, width - margin - s(110), fields_y + s(32), s(110), s(25), TRUE);
    MoveWindow(state.online, margin, fields_y + s(64), s(210), s(24), TRUE);
    const int buttons_y = height - s(48);
    MoveWindow(state.inspect, margin, buttons_y, s(88), s(30), TRUE);
    MoveWindow(state.prepare, margin + s(94), buttons_y, s(104), s(30), TRUE);
    MoveWindow(state.run, margin + s(204), buttons_y, s(100), s(30), TRUE);
    MoveWindow(state.cancel, margin + s(310), buttons_y, s(78), s(30), TRUE);
    MoveWindow(state.logs, width - margin - s(222), buttons_y, s(104), s(30), TRUE);
    MoveWindow(state.maintenance, width - margin - s(112), buttons_y, s(112), s(30), TRUE);
}

void refresh_snapshot(State& state) {
    ListView_DeleteAllItems(state.components);
    std::wstring detail;
    bool uv_ready = false;
    bool environment_action = false;
    bool repair = state.snapshot.overall == L"RepairRequired";
    for (std::size_t index = 0; index < state.snapshot.components.size(); ++index) {
        const Component& component = state.snapshot.components[index];
        LVITEMW item{};
        item.mask = LVIF_TEXT;
        item.iItem = static_cast<int>(index);
        std::wstring title = component_title(component.name);
        item.pszText = title.data();
        ListView_InsertItem(state.components, &item);
        std::wstring status = state_title(component.state);
        ListView_SetItemText(state.components, static_cast<int>(index), 1, status.data());
        std::wstring summary = component_summary(component);
        ListView_SetItemText(state.components, static_cast<int>(index), 2, summary.data());
        detail += L"[" + title + L"] " + status + L"\r\n" + summary + L"\r\n";
        if (!component.detail.empty()) detail += L"세부 정보: " + component.detail + L"\r\n";
        if (!component.executable_path.empty()) {
            detail += L"확인된 실행 파일: " + component.executable_path + L"\r\n";
        }
        if (!component.version.empty()) detail += L"버전: " + component.version + L"\r\n";
        detail += L"\r\n";
        if (component.name == L"uv" && component.state == L"Ready") uv_ready = true;
        if ((component.name == L"python" || component.name == L"environment") &&
            component.state != L"Ready") environment_action = true;
    }
    SetWindowTextW(state.details, detail.c_str());
    if (state.snapshot.overall == L"Ready") {
        SetWindowTextW(state.status, L"[✓] 준비됨");
        SetWindowTextW(state.reason, L"모든 필수 구성 요소를 확인했습니다. 편집기를 실행할 수 있습니다.");
    } else if (repair) {
        SetWindowTextW(state.status, L"[!] 복구 필요");
        SetWindowTextW(state.reason, L"잠금 환경 또는 실행 파일 상태를 복구한 뒤 다시 점검하세요.");
    } else {
        SetWindowTextW(state.status, L"[–] 준비되지 않음");
        SetWindowTextW(state.reason, L"목록에서 준비되지 않은 항목과 다음 행동을 확인하세요.");
    }
    SetWindowTextW(state.prepare, repair ? L"환경 복구(&P)" : L"환경 구성(&P)");
    EnableWindow(state.prepare, !state.busy && uv_ready && environment_action);
    EnableWindow(state.run, !state.busy && state.snapshot.overall == L"Ready");
    append_log(state, L"inspection", L"state=" + state.snapshot.overall);
}

std::wstring choose_folder(HWND owner) {
    BROWSEINFOW info{};
    info.hwndOwner = owner;
    info.lpszTitle = L"FFmpeg와 ffprobe가 함께 있는 bin 폴더를 선택하세요.";
    info.ulFlags = BIF_RETURNONLYFSDIRS | BIF_NEWDIALOGSTYLE;
    PIDLIST_ABSOLUTE item = SHBrowseForFolderW(&info);
    if (item == nullptr) return {};
    wchar_t path[MAX_PATH]{};
    const bool ok = SHGetPathFromIDListW(item, path) != FALSE;
    CoTaskMemFree(item);
    return ok ? std::wstring(path) : std::wstring();
}

std::wstring choose_project(HWND owner) {
    wchar_t path[32768]{};
    OPENFILENAMEW dialog{};
    dialog.lStructSize = sizeof(dialog);
    dialog.hwndOwner = owner;
    dialog.lpstrFilter = L"Movie Maker 프로젝트 (*.mmrproj)\0*.mmrproj\0모든 파일 (*.*)\0*.*\0";
    dialog.lpstrFile = path;
    dialog.nMaxFile = 32768;
    dialog.Flags = OFN_FILEMUSTEXIST | OFN_PATHMUSTEXIST | OFN_EXPLORER;
    return GetOpenFileNameW(&dialog) ? std::wstring(path) : std::wstring();
}

std::vector<std::wstring> inspector_arguments(const State& state) {
    std::vector<std::wstring> arguments = {
        L"-NoProfile", L"-NonInteractive", L"-ExecutionPolicy", L"Bypass", L"-File",
        (std::filesystem::path(state.app_root) / L"scripts" / L"environment" /
            L"inspect-runtime.ps1").wstring(),
        L"-AppRoot", state.app_root, L"-Format", L"Protocol"
    };
    const std::wstring ffmpeg = control_text(state.ffmpeg_edit);
    if (!ffmpeg.empty()) {
        arguments.push_back(L"-FFmpegDirectory");
        arguments.push_back(ffmpeg);
    }
    const std::wstring uv = resolve_uv(state);
    if (!uv.empty()) {
        arguments.push_back(L"-UvExecutable");
        arguments.push_back(uv);
    }
    return arguments;
}

void post_worker(State* state, std::unique_ptr<WorkerResult> result) {
    WorkerResult* raw = result.release();
    if (!PostMessageW(state->window, kWorkerComplete, 0, reinterpret_cast<LPARAM>(raw))) {
        delete raw;
    }
}

void begin_inspection(State& state) {
    if (state.busy) return;
    set_busy(state, true, L"[↻] 준비 중 — 실행 환경을 점검하고 있습니다…");
    state.cancellation.store(false);
    append_log(state, L"inspection", L"started");
    const std::wstring powershell = mmr::find_executable(L"powershell.exe");
    const std::vector<std::wstring> arguments = inspector_arguments(state);
    state.worker = std::thread([&state, powershell, arguments]() {
        auto result = std::make_unique<WorkerResult>();
        result->task = Task::inspect;
        if (powershell.empty()) {
            result->message = L"Windows PowerShell을 찾을 수 없습니다.";
        } else {
            result->process = mmr::run_process(
                powershell, arguments, state.app_root, 120000, &state.cancellation,
                static_cast<std::size_t>(state.contract.maximum_captured_process_bytes)
            );
            result->snapshot = parse_protocol(result->process.standard_output);
        }
        post_worker(&state, std::move(result));
    });
}

void begin_prepare(State& state) {
    if (state.busy) return;
    const bool repair = state.snapshot.overall == L"RepairRequired";
    const wchar_t* explanation = repair
        ? L"손상되었거나 불일치한 .venv를 옆으로 이동한 뒤, 잠긴 런타임 환경을 다시 만듭니다.\n"
          L"실패하면 이전 환경을 복원합니다. 네트워크와 디스크를 사용할 수 있습니다. 계속할까요?"
        : L"uv가 잠긴 런타임 환경을 구성합니다. Python과 패키지를 내려받을 수 있으며\n"
          L"디스크를 사용합니다. 계속할까요?";
    if (MessageBoxW(state.window, explanation, L"환경 변경 승인", MB_YESNO | MB_ICONWARNING |
            MB_DEFBUTTON2) != IDYES) {
        append_log(state, L"prepare", L"user declined");
        return;
    }
    const std::wstring uv = resolve_uv(state);
    const std::wstring powershell = mmr::find_executable(L"powershell.exe");
    if (uv.empty() || powershell.empty()) {
        MessageBoxW(state.window, L"패키지의 uv 또는 Windows PowerShell을 찾을 수 없습니다.",
            L"환경 구성 불가", MB_OK | MB_ICONERROR);
        return;
    }
    std::vector<std::wstring> arguments = {
        L"-NoProfile", L"-NonInteractive", L"-ExecutionPolicy", L"Bypass", L"-File",
        (std::filesystem::path(state.app_root) / L"scripts" / L"environment" /
            L"prepare-runtime.ps1").wstring(),
        L"-Mode", repair ? L"Repair" : L"Setup", L"-AppRoot", state.app_root,
        L"-UvExecutable", uv
    };
    set_busy(state, true, repair ? L"[↻] 준비 중 — 환경을 복구하고 있습니다…" :
        L"[↻] 준비 중 — 환경을 구성하고 있습니다…");
    state.cancellation.store(false);
    append_log(state, L"prepare", repair ? L"repair approved" : L"setup approved");
    state.worker = std::thread([&state, powershell, arguments]() {
        auto result = std::make_unique<WorkerResult>();
        result->task = Task::prepare;
        result->process = mmr::run_process(
            powershell, arguments, state.app_root, 20 * 60 * 1000, &state.cancellation,
            static_cast<std::size_t>(state.contract.maximum_captured_process_bytes)
        );
        if (result->process.cancelled) {
            result->recovery_attempted = true;
            std::vector<std::wstring> recovery_arguments = arguments;
            recovery_arguments.push_back(L"-RecoveryOnly");
            const mmr::ProcessResult recovery = mmr::run_process(
                powershell, recovery_arguments, state.app_root, 5 * 60 * 1000, nullptr,
                static_cast<std::size_t>(state.contract.maximum_captured_process_bytes)
            );
            result->recovery_succeeded = recovery.started && recovery.exit_code == 0;
            result->process.standard_output += "\n[recovery]\n" + recovery.standard_output;
            result->process.standard_error += "\n[recovery]\n" + recovery.standard_error;
        }
        post_worker(&state, std::move(result));
    });
}

std::vector<std::wstring> editor_arguments(State& state) {
    std::vector<std::wstring> app = state.options.app_arguments;
    const std::wstring project = control_text(state.project_edit);
    const bool online = SendMessageW(state.online, BM_GETCHECK, 0, 0) == BST_CHECKED;
    if (project != state.options.project_path || online != state.options.online) {
        std::vector<std::wstring> rebuilt;
        for (std::size_t index = 0; index < app.size(); ++index) {
            if (app[index] == L"--online") continue;
            if (app[index] == L"--project" && index + 1 < app.size()) {
                ++index;
                continue;
            }
            rebuilt.push_back(app[index]);
        }
        app = std::move(rebuilt);
        if (online) app.push_back(L"--online");
        if (!project.empty()) {
            app.push_back(L"--project");
            app.push_back(project);
        }
    }
    std::vector<std::wstring> result = state.contract.run_arguments;
    result.insert(result.end(), app.begin(), app.end());
    return result;
}

void begin_run(State& state) {
    if (state.busy || state.snapshot.overall != L"Ready") return;
    const std::wstring uv = resolve_uv(state);
    if (uv.empty()) {
        MessageBoxW(state.window, L"패키지의 uv를 찾지 못했습니다. 다시 설치하세요.", L"실행 불가",
            MB_OK | MB_ICONERROR);
        return;
    }
    const std::wstring ffmpeg = control_text(state.ffmpeg_edit);
    if (!ffmpeg.empty()) SetEnvironmentVariableW(L"MOVIE_MAKER_FFMPEG_DIR", ffmpeg.c_str());
    const std::vector<std::wstring> arguments = editor_arguments(state);
    set_busy(state, true, L"[↻] 준비 중 — 편집기 시작을 확인하고 있습니다…");
    state.cancellation.store(false);
    append_log(state, L"run", L"executable=" + uv + L"; argv units=" +
        std::to_wstring(arguments.size()));
    state.worker = std::thread([&state, uv, arguments]() {
        auto result = std::make_unique<WorkerResult>();
        result->task = Task::run;
        result->process = mmr::run_process(
            uv, arguments, state.app_root, 30000, &state.cancellation,
            static_cast<std::size_t>(state.contract.maximum_captured_process_bytes), true, 3000
        );
        post_worker(&state, std::move(result));
    });
}

void handle_worker(State& state, std::unique_ptr<WorkerResult> result) {
    if (state.worker.joinable()) state.worker.join();
    set_busy(state, false);
    const mmr::ProcessResult& process = result->process;
    if (process.started) {
        append_log(state, L"process", L"exit=" + std::to_wstring(process.exit_code) +
            L"; cancelled=" + (process.cancelled ? L"true" : L"false") +
            L"; timeout=" + (process.timed_out ? L"true" : L"false"));
        if (!process.standard_output.empty()) {
            append_log(state, L"stdout", mmr::utf8_to_wide(process.standard_output));
        }
        if (!process.standard_error.empty()) {
            append_log(state, L"stderr", mmr::utf8_to_wide(process.standard_error));
        }
    } else if (process.system_error != ERROR_SUCCESS) {
        append_log(state, L"process", L"start error=" +
            mmr::format_system_error(process.system_error));
    }

    if (result->task == Task::inspect) {
        if (result->snapshot.valid) {
            state.snapshot = std::move(result->snapshot);
            refresh_snapshot(state);
        } else {
            SetWindowTextW(state.status, L"[!] 복구 필요");
            SetWindowTextW(state.reason,
                result->message.empty() ? L"환경 점검 결과를 읽지 못했습니다. 진단 로그를 확인하세요."
                                        : result->message.c_str());
            EnableWindow(state.run, FALSE);
            EnableWindow(state.prepare, FALSE);
        }
    } else if (result->task == Task::prepare) {
        if (process.cancelled) {
            const wchar_t* message = result->recovery_succeeded
                ? L"환경 작업을 취소하고 이전 환경을 복원하거나 불완전한 환경을 정리했습니다."
                : L"환경 작업은 취소됐지만 자동 복구에 실패했습니다. 진단 로그를 확인하세요.";
            MessageBoxW(state.window, message, L"작업 취소",
                result->recovery_succeeded ? MB_OK | MB_ICONINFORMATION : MB_OK | MB_ICONERROR);
        } else if (!process.started || process.exit_code != 0) {
            MessageBoxW(state.window, L"환경 작업에 실패했습니다. 기존 환경 보존 여부와 세부 원인을\n"
                L"진단 로그에서 확인하세요.", L"환경 작업 실패", MB_OK | MB_ICONERROR);
        }
        if (!state.close_after_worker) begin_inspection(state);
    } else {
        if (process.still_running) {
            SetWindowTextW(state.status, L"[✓] 편집기 실행됨");
            SetWindowTextW(state.reason, L"편집기가 시작 직후 종료되지 않고 정상 실행 중입니다.");
        } else if (process.cancelled) {
            SetWindowTextW(state.status, L"[–] 실행 취소됨");
            SetWindowTextW(state.reason, L"시작 중인 편집기 프로세스를 정리했습니다.");
        } else {
            SetWindowTextW(state.status, L"[!] 시작 직후 실패");
            SetWindowTextW(state.reason, L"편집기가 시작 직후 종료됐습니다. 진단 로그를 확인하세요.");
            MessageBoxW(state.window, L"편집기가 시작 직후 종료됐습니다. 종료 코드와 제한된 출력을\n"
                L"진단 로그에 기록했습니다.", L"편집기 실행 실패", MB_OK | MB_ICONERROR);
        }
    }
    if (state.close_after_worker && !state.busy) DestroyWindow(state.window);
}

void open_maintenance(State& state) {
    const std::wstring setup = (
        std::filesystem::path(state.app_root) / L"MovieMakerSetup.exe"
    ).wstring();
    if (std::filesystem::is_regular_file(setup)) {
        ShellExecuteW(state.window, L"open", setup.c_str(), nullptr, state.app_root.c_str(), SW_SHOWNORMAL);
    } else {
        MessageBoxW(state.window, L"설치된 배포본에서 업데이트·제거 도구를 사용할 수 있습니다.\n"
            L"개발 소스 트리에서는 scripts\\packaging의 수명주기 검사를 사용하세요.",
            L"유지관리", MB_OK | MB_ICONINFORMATION);
    }
}

LRESULT CALLBACK window_proc(HWND window, UINT message, WPARAM wparam, LPARAM lparam) {
    State* state = reinterpret_cast<State*>(GetWindowLongPtrW(window, GWLP_USERDATA));
    if (message == WM_NCCREATE) {
        auto* create = reinterpret_cast<CREATESTRUCTW*>(lparam);
        state = static_cast<State*>(create->lpCreateParams);
        state->window = window;
        SetWindowLongPtrW(window, GWLP_USERDATA, reinterpret_cast<LONG_PTR>(state));
    }
    if (state == nullptr) return DefWindowProcW(window, message, wparam, lparam);

    switch (message) {
    case WM_CREATE: {
        state->font = mmr::create_message_font(mmr::dpi_for_window(window));
        state->status = create_control(*state, 0, L"STATIC", L"[↻] 준비 중", SS_LEFT, kStatus);
        state->reason = create_control(*state, 0, L"STATIC", L"환경 점검을 시작합니다.", SS_LEFT,
            kReason);
        state->components = create_control(*state, WS_EX_CLIENTEDGE, WC_LISTVIEWW, L"",
            LVS_REPORT | LVS_SINGLESEL | LVS_SHOWSELALWAYS, kComponents);
        ListView_SetExtendedListViewStyle(state->components, LVS_EX_FULLROWSELECT | LVS_EX_DOUBLEBUFFER);
        LVCOLUMNW column{};
        column.mask = LVCF_TEXT | LVCF_WIDTH;
        column.pszText = const_cast<wchar_t*>(L"구성 요소"); column.cx = 132;
        ListView_InsertColumn(state->components, 0, &column);
        column.pszText = const_cast<wchar_t*>(L"상태"); column.cx = 118;
        ListView_InsertColumn(state->components, 1, &column);
        column.pszText = const_cast<wchar_t*>(L"설명"); column.cx = 440;
        ListView_InsertColumn(state->components, 2, &column);
        state->details = create_control(*state, WS_EX_CLIENTEDGE, L"EDIT", L"",
            ES_MULTILINE | ES_AUTOVSCROLL | ES_READONLY | WS_VSCROLL, kDetails);
        state->ffmpeg_edit = create_control(*state, WS_EX_CLIENTEDGE, L"EDIT",
            state->options.ffmpeg_directory.c_str(), ES_AUTOHSCROLL, kFfmpegEdit);
        SendMessageW(state->ffmpeg_edit, EM_SETCUEBANNER, TRUE,
            reinterpret_cast<LPARAM>(L"선택적 FFmpeg bin 경로"));
        state->browse_ffmpeg = create_control(*state, 0, L"BUTTON", L"FFmpeg 찾기(&F)",
            BS_PUSHBUTTON, kBrowseFfmpeg);
        state->project_edit = create_control(*state, WS_EX_CLIENTEDGE, L"EDIT",
            state->options.project_path.c_str(), ES_AUTOHSCROLL, kProjectEdit);
        SendMessageW(state->project_edit, EM_SETCUEBANNER, TRUE,
            reinterpret_cast<LPARAM>(L"선택적 .mmrproj 프로젝트"));
        state->browse_project = create_control(*state, 0, L"BUTTON", L"프로젝트 찾기(&J)",
            BS_PUSHBUTTON, kBrowseProject);
        state->online = create_control(*state, 0, L"BUTTON", L"온라인 앱 기능 허용(&O)",
            BS_AUTOCHECKBOX, kOnline);
        SendMessageW(state->online, BM_SETCHECK, state->options.online ? BST_CHECKED : BST_UNCHECKED, 0);
        state->inspect = create_control(*state, 0, L"BUTTON", L"다시 점검(&I)", BS_PUSHBUTTON,
            kInspect);
        state->prepare = create_control(*state, 0, L"BUTTON", L"환경 구성(&P)", BS_PUSHBUTTON,
            kPrepare);
        state->run = create_control(*state, 0, L"BUTTON", L"편집기 실행(&R)",
            BS_DEFPUSHBUTTON, kRun);
        state->cancel = create_control(*state, 0, L"BUTTON", L"취소(&C)", BS_PUSHBUTTON, kCancel);
        state->logs = create_control(*state, 0, L"BUTTON", L"진단 로그(&L)", BS_PUSHBUTTON, kLogs);
        state->maintenance = create_control(*state, 0, L"BUTTON", L"유지관리(&M)", BS_PUSHBUTTON,
            kMaintenance);
        EnableWindow(state->run, FALSE);
        EnableWindow(state->prepare, FALSE);
        EnableWindow(state->cancel, FALSE);
        PostMessageW(window, WM_COMMAND, MAKEWPARAM(kInspect, BN_CLICKED), 0);
        return 0;
    }
    case WM_SIZE:
        layout(*state, LOWORD(lparam), HIWORD(lparam));
        return 0;
    case WM_GETMINMAXINFO: {
        auto* limits = reinterpret_cast<MINMAXINFO*>(lparam);
        const UINT dpi = mmr::dpi_for_window(window);
        const SIZE minimum = mmr::window_size_for_client(
            static_cast<DWORD>(GetWindowLongPtrW(window, GWL_STYLE)),
            static_cast<DWORD>(GetWindowLongPtrW(window, GWL_EXSTYLE)), 720, 600, dpi
        );
        limits->ptMinTrackSize.x = minimum.cx;
        limits->ptMinTrackSize.y = minimum.cy;
        return 0;
    }
    case WM_DPICHANGED: {
        const RECT* suggested = reinterpret_cast<RECT*>(lparam);
        SetWindowPos(window, nullptr, suggested->left, suggested->top,
            suggested->right - suggested->left, suggested->bottom - suggested->top,
            SWP_NOACTIVATE | SWP_NOZORDER);
        HFONT previous = state->font;
        state->font = mmr::create_message_font(HIWORD(wparam));
        mmr::apply_font_to_children(window, state->font);
        if (previous != nullptr) DeleteObject(previous);
        return 0;
    }
    case WM_COMMAND:
        switch (LOWORD(wparam)) {
        case kInspect: begin_inspection(*state); break;
        case kPrepare: begin_prepare(*state); break;
        case kRun: begin_run(*state); break;
        case kCancel:
            state->cancellation.store(true);
            SetWindowTextW(state->reason, L"진행 중인 자식 프로세스를 정리하고 있습니다…");
            break;
        case kLogs:
            if (!state->log_directory.empty()) {
                ShellExecuteW(window, L"open", state->log_directory.c_str(), nullptr, nullptr,
                    SW_SHOWNORMAL);
            } else {
                MessageBoxW(window, L"로그 디렉터리를 만들 수 없었습니다. 화면의 진단 내용을 복사하세요.",
                    L"로그 기록 불가", MB_OK | MB_ICONWARNING);
            }
            break;
        case kMaintenance: open_maintenance(*state); break;
        case kBrowseFfmpeg: {
            const std::wstring path = choose_folder(window);
            if (!path.empty()) {
                SetWindowTextW(state->ffmpeg_edit, path.c_str());
                begin_inspection(*state);
            }
            break;
        }
        case kBrowseProject: {
            const std::wstring path = choose_project(window);
            if (!path.empty()) SetWindowTextW(state->project_edit, path.c_str());
            break;
        }
        }
        return 0;
    case kWorkerComplete:
        handle_worker(*state, std::unique_ptr<WorkerResult>(reinterpret_cast<WorkerResult*>(lparam)));
        return 0;
    case WM_CLOSE:
        if (state->busy) {
            if (MessageBoxW(window, L"진행 중인 작업을 취소하고 자식 프로세스를 정리한 뒤 닫을까요?",
                    L"런처 닫기", MB_YESNO | MB_ICONWARNING | MB_DEFBUTTON2) == IDYES) {
                state->close_after_worker = true;
                state->cancellation.store(true);
            }
            return 0;
        }
        DestroyWindow(window);
        return 0;
    case WM_DESTROY:
        if (state->font != nullptr) DeleteObject(state->font);
        PostQuitMessage(0);
        return 0;
    }
    return DefWindowProcW(window, message, wparam, lparam);
}

void enable_dpi_awareness() {
    HMODULE user = GetModuleHandleW(L"user32.dll");
    using SetAwareness = BOOL(WINAPI*)(HANDLE);
    auto set_awareness = reinterpret_cast<SetAwareness>(
        GetProcAddress(user, "SetProcessDpiAwarenessContext")
    );
    if (set_awareness != nullptr) {
        set_awareness(reinterpret_cast<HANDLE>(-4));  // PER_MONITOR_AWARE_V2
    } else {
        SetProcessDPIAware();
    }
}

}  // namespace

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE, PWSTR, int show) {
    enable_dpi_awareness();
    CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);
    INITCOMMONCONTROLSEX controls{sizeof(controls), ICC_LISTVIEW_CLASSES | ICC_STANDARD_CLASSES};
    InitCommonControlsEx(&controls);

    int argument_count = 0;
    LPWSTR* raw_arguments = CommandLineToArgvW(GetCommandLineW(), &argument_count);
    std::vector<std::wstring> arguments;
    for (int index = 1; index < argument_count; ++index) arguments.emplace_back(raw_arguments[index]);
    if (raw_arguments != nullptr) LocalFree(raw_arguments);

    State state;
    state.options = mmr::parse_launcher_options(arguments);
    if (!state.options.valid) {
        MessageBoxW(nullptr, state.options.error.c_str(), L"런처 인자 오류", MB_OK | MB_ICONERROR);
        CoUninitialize();
        return 2;
    }
    if (state.options.help) {
        MessageBoxW(nullptr,
            L"MovieMakerLauncher.exe [--verbose] [--ffmpeg-dir <bin>] [-- <앱 인자>...]\n\n"
            L"첫 번째 -- 뒤의 인자는 빈 값과 유니코드를 포함해 편집기에 그대로 전달됩니다.",
            L"Movie Maker Launcher 도움말", MB_OK | MB_ICONINFORMATION);
        CoUninitialize();
        return 0;
    }
    state.app_root = mmr::find_application_root(mmr::executable_directory());
    if (state.app_root.empty()) {
        MessageBoxW(nullptr, L"런처 위치에서 앱 루트와 런타임 계약을 찾지 못했습니다.",
            L"앱 파일 누락", MB_OK | MB_ICONERROR);
        CoUninitialize();
        return 3;
    }
    std::wstring contract_error;
    if (!mmr::read_runtime_contract(state.app_root, state.contract, contract_error)) {
        MessageBoxW(nullptr, contract_error.c_str(), L"런타임 계약 오류", MB_OK | MB_ICONERROR);
        CoUninitialize();
        return 3;
    }
    initialize_log(state);

    WNDCLASSEXW window_class{};
    window_class.cbSize = sizeof(window_class);
    window_class.lpfnWndProc = window_proc;
    window_class.hInstance = instance;
    window_class.hCursor = LoadCursorW(nullptr, IDC_ARROW);
    window_class.hIcon = LoadIconW(nullptr, IDI_APPLICATION);
    window_class.hIconSm = window_class.hIcon;
    window_class.hbrBackground = GetSysColorBrush(COLOR_WINDOW);
    window_class.lpszClassName = kWindowClass;
    if (!RegisterClassExW(&window_class)) {
        CoUninitialize();
        return 4;
    }
    const UINT initial_dpi = mmr::dpi_for_window(nullptr);
    const DWORD window_style = WS_OVERLAPPEDWINDOW | WS_CLIPCHILDREN;
    const SIZE initial_size = mmr::window_size_for_client(
        window_style, 0, 820, 680, initial_dpi
    );
    HWND window = CreateWindowExW(
        0, kWindowClass, L"Movie Maker Reproduction — 실행 준비 및 진단",
        window_style, CW_USEDEFAULT, CW_USEDEFAULT, initial_size.cx, initial_size.cy,
        nullptr, nullptr, instance, &state
    );
    if (window == nullptr) {
        CoUninitialize();
        return 4;
    }
    ShowWindow(window, show);
    UpdateWindow(window);
    MSG message{};
    while (GetMessageW(&message, nullptr, 0, 0) > 0) {
        if (!IsDialogMessageW(window, &message)) {
            TranslateMessage(&message);
            DispatchMessageW(&message);
        }
    }
    if (state.worker.joinable()) state.worker.join();
    CoUninitialize();
    return static_cast<int>(message.wParam);
}
