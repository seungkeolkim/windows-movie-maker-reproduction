#include "launcher_core.hpp"

#include <commctrl.h>
#include <shellapi.h>
#include <shlobj.h>

#include <atomic>
#include <algorithm>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <memory>
#include <regex>
#include <thread>
#include <vector>

namespace {

constexpr wchar_t kClassName[] = L"MovieMakerReproductionSetupWindow";
constexpr UINT kComplete = WM_APP + 7;
constexpr int kInstall = 2001;
constexpr int kUpdate = 2002;
constexpr int kUninstall = 2003;
constexpr int kCancel = 2004;
constexpr int kStartMenu = 2010;
constexpr int kDesktop = 2011;
constexpr int kAssociation = 2012;
constexpr int kPath = 2013;
constexpr int kPurgeCache = 2014;
constexpr int kPurgeLogs = 2015;
constexpr int kPurgeAutosaves = 2016;
constexpr int kSummary = 2020;
constexpr int kOutput = 2021;

struct Completion {
    std::wstring mode;
    mmr::ProcessResult process;
    bool recovery_attempted = false;
    bool recovery_succeeded = false;
};

struct SetupState {
    HWND window = nullptr;
    HWND summary = nullptr;
    HWND output = nullptr;
    HWND install = nullptr;
    HWND update = nullptr;
    HWND uninstall = nullptr;
    HWND cancel = nullptr;
    HWND start_menu = nullptr;
    HWND desktop = nullptr;
    HWND association = nullptr;
    HWND path = nullptr;
    HWND purge_cache = nullptr;
    HWND purge_logs = nullptr;
    HWND purge_autosaves = nullptr;
    HFONT font = nullptr;
    std::wstring executable_root;
    std::wstring source_root;
    std::wstring state_path;
    std::wstring installed_version;
    mmr::RuntimeContract contract;
    std::thread worker;
    std::atomic_bool cancellation{false};
    bool installed = false;
    bool has_payload = false;
    bool busy = false;
    bool close_after = false;
};

std::string read_bytes(const std::wstring& path) {
    std::ifstream stream(std::filesystem::path(path), std::ios::binary);
    return std::string(std::istreambuf_iterator<char>(stream), std::istreambuf_iterator<char>());
}

std::wstring json_string(const std::string& source, const std::string& key) {
    std::regex expression("\\\"" + key + "\\\"\\s*:\\s*\\\"([^\\\"]*)\\\"");
    std::smatch match;
    return std::regex_search(source, match, expression) ? mmr::utf8_to_wide(match[1].str()) :
        std::wstring();
}

bool json_bool(const std::string& source, const std::string& key, bool fallback) {
    std::regex expression("\\\"" + key + "\\\"\\s*:\\s*(true|false)");
    std::smatch match;
    return std::regex_search(source, match, expression) ? match[1].str() == "true" : fallback;
}

std::wstring local_app_data() {
    wchar_t path[MAX_PATH]{};
    return SUCCEEDED(SHGetFolderPathW(nullptr, CSIDL_LOCAL_APPDATA, nullptr, SHGFP_TYPE_CURRENT, path))
        ? std::wstring(path) : std::wstring();
}

std::uint64_t directory_size(const std::wstring& root) {
    std::uint64_t result = 0;
    std::error_code error;
    for (const auto& entry : std::filesystem::recursive_directory_iterator(root, error)) {
        if (error) break;
        if (entry.is_regular_file(error)) result += entry.file_size(error);
        error.clear();
    }
    return result;
}

std::wstring megabytes(std::uint64_t bytes) {
    return std::to_wstring((bytes + 1024 * 1024 - 1) / (1024 * 1024)) + L" MB";
}

HWND add_control(
    SetupState& state,
    const wchar_t* class_name,
    const wchar_t* text,
    DWORD style,
    int id,
    DWORD extended = 0
) {
    const DWORD tab_style = lstrcmpW(class_name, L"STATIC") == 0 ? 0 : WS_TABSTOP;
    HWND control = CreateWindowExW(
        extended, class_name, text, WS_CHILD | WS_VISIBLE | tab_style | style, 0, 0, 0, 0,
        state.window, reinterpret_cast<HMENU>(static_cast<INT_PTR>(id)), GetModuleHandleW(nullptr), nullptr
    );
    SendMessageW(control, WM_SETFONT, reinterpret_cast<WPARAM>(state.font), TRUE);
    return control;
}

void set_busy(SetupState& state, bool busy) {
    state.busy = busy;
    EnableWindow(state.install, !busy && !state.installed && state.has_payload);
    EnableWindow(state.update, !busy && state.installed && state.has_payload &&
        state.installed_version != state.contract.version);
    EnableWindow(state.uninstall, !busy && state.installed);
    EnableWindow(state.cancel, busy);
}

void layout(SetupState& state, int width, int height) {
    const UINT dpi = mmr::dpi_for_window(state.window);
    const auto s = [dpi](int value) { return mmr::scale_for_dpi(value, dpi); };
    const int margin = s(20);
    const int inner = width - margin * 2;
    MoveWindow(state.summary, margin, s(18), inner, s(88), TRUE);
    MoveWindow(state.start_menu, margin, s(112), s(250), s(23), TRUE);
    MoveWindow(state.desktop, margin + s(260), s(112), s(220), s(23), TRUE);
    MoveWindow(state.association, margin, s(140), s(250), s(23), TRUE);
    MoveWindow(state.path, margin + s(260), s(140), s(300), s(23), TRUE);
    MoveWindow(state.purge_cache, margin, s(172), s(190), s(23), TRUE);
    MoveWindow(state.purge_logs, margin + s(195), s(172), s(190), s(23), TRUE);
    MoveWindow(state.purge_autosaves, margin + s(390), s(172), s(250), s(23), TRUE);
    MoveWindow(state.output, margin, s(205), inner, std::max(s(120), height - s(285)), TRUE);
    const int y = height - s(58);
    MoveWindow(state.install, margin, y, s(90), s(32), TRUE);
    MoveWindow(state.update, margin + s(98), y, s(90), s(32), TRUE);
    MoveWindow(state.uninstall, margin + s(196), y, s(90), s(32), TRUE);
    MoveWindow(state.cancel, width - margin - s(90), y, s(90), s(32), TRUE);
}

bool checked(HWND control) {
    return SendMessageW(control, BM_GETCHECK, 0, 0) == BST_CHECKED;
}

void broadcast_environment_change() {
    DWORD_PTR ignored = 0;
    SendMessageTimeoutW(
        HWND_BROADCAST, WM_SETTINGCHANGE, 0, reinterpret_cast<LPARAM>(L"Environment"),
        SMTO_ABORTIFHUNG, 5000, &ignored
    );
    SHChangeNotify(SHCNE_ASSOCCHANGED, SHCNF_IDLIST, nullptr, nullptr);
}

void start_lifecycle(SetupState& state, const std::wstring& mode) {
    if (state.busy) return;
    std::wstring warning;
    if (mode == L"Uninstall") {
        warning = L"설치 프로그램이 만든 파일과 등록만 제거합니다. 프로젝트와 원본 미디어는 보존됩니다.\n"
            L"아래에서 선택한 캐시·로그·자동 저장만 추가로 삭제합니다. 계속할까요?";
    } else if (mode == L"Update") {
        warning = L"기존 실행 가능한 설치를 보관한 상태에서 새 버전으로 교체합니다.\n"
            L"실패하면 이전 설치를 복원합니다. 계속할까요?";
    } else {
        warning = L"앱 파일과 선택한 바로가기·파일 연결·PATH 항목을 현재 사용자에게 설치합니다.\n"
            L"검증된 uv만 포함하며 Python, .venv, PySide6와 FFmpeg는 이 단계에서 설치하지 "
            L"않습니다. 계속할까요?";
    }
    if (MessageBoxW(state.window, warning.c_str(), L"변경 승인", MB_YESNO | MB_ICONWARNING |
            MB_DEFBUTTON2) != IDYES) return;

    const std::wstring powershell = mmr::find_executable(L"powershell.exe");
    if (powershell.empty()) {
        MessageBoxW(state.window, L"Windows PowerShell을 찾을 수 없습니다.", L"유지관리 불가",
            MB_OK | MB_ICONERROR);
        return;
    }
    const std::wstring script = (
        std::filesystem::path(state.source_root) / L"scripts" / L"packaging" /
        L"install-lifecycle.ps1"
    ).wstring();
    std::vector<std::wstring> arguments = {
        L"-NoProfile", L"-NonInteractive", L"-ExecutionPolicy", L"Bypass", L"-File", script,
        L"-Mode", mode
    };
    if (mode != L"Uninstall") {
        arguments.push_back(L"-SourceDirectory");
        arguments.push_back(state.source_root);
        if (checked(state.start_menu)) arguments.push_back(L"-StartMenuShortcut");
        if (checked(state.desktop)) arguments.push_back(L"-DesktopShortcut");
        if (checked(state.association)) arguments.push_back(L"-FileAssociation");
        if (checked(state.path)) arguments.push_back(L"-AddToPath");
    } else {
        if (checked(state.purge_cache)) arguments.push_back(L"-PurgeCache");
        if (checked(state.purge_logs)) arguments.push_back(L"-PurgeLogs");
        if (checked(state.purge_autosaves)) arguments.push_back(L"-PurgeAutosaves");
    }
    SetWindowTextW(state.output, L"작업을 시작했습니다. 기존 설치를 보존하는 트랜잭션을 준비합니다…");
    set_busy(state, true);
    state.cancellation.store(false);
    state.worker = std::thread([&state, powershell, arguments, mode]() {
        auto completion = std::make_unique<Completion>();
        completion->mode = mode;
        completion->process = mmr::run_process(
            powershell, arguments, state.source_root, 30 * 60 * 1000, &state.cancellation,
            64 * 1024
        );
        if (completion->process.cancelled) {
            completion->recovery_attempted = true;
            std::vector<std::wstring> recovery_arguments = arguments;
            for (std::size_t index = 0; index + 1 < recovery_arguments.size(); ++index) {
                if (recovery_arguments[index] == L"-Mode") {
                    recovery_arguments[index + 1] = L"Recover";
                    break;
                }
            }
            const mmr::ProcessResult recovery = mmr::run_process(
                powershell, recovery_arguments, state.source_root, 5 * 60 * 1000, nullptr,
                64 * 1024
            );
            completion->recovery_succeeded = recovery.started && recovery.exit_code == 0;
            completion->process.standard_output += "\n[recovery]\n" + recovery.standard_output;
            completion->process.standard_error += "\n[recovery]\n" + recovery.standard_error;
        }
        Completion* raw = completion.release();
        if (!PostMessageW(state.window, kComplete, 0, reinterpret_cast<LPARAM>(raw))) delete raw;
    });
}

LRESULT CALLBACK window_proc(HWND window, UINT message, WPARAM wparam, LPARAM lparam) {
    SetupState* state = reinterpret_cast<SetupState*>(GetWindowLongPtrW(window, GWLP_USERDATA));
    if (message == WM_NCCREATE) {
        state = static_cast<SetupState*>(reinterpret_cast<CREATESTRUCTW*>(lparam)->lpCreateParams);
        state->window = window;
        SetWindowLongPtrW(window, GWLP_USERDATA, reinterpret_cast<LONG_PTR>(state));
    }
    if (state == nullptr) return DefWindowProcW(window, message, wparam, lparam);
    switch (message) {
    case WM_CREATE: {
        state->font = mmr::create_message_font(mmr::dpi_for_window(window));
        const std::uint64_t required = state->has_payload ? directory_size(state->source_root) : 0;
        ULARGE_INTEGER free_bytes{};
        const std::wstring install_volume = local_app_data();
        GetDiskFreeSpaceExW(install_volume.c_str(), &free_bytes, nullptr, nullptr);
        std::wstring summary = L"새 버전: " + state->contract.version +
            L"    필요한 공간: " + megabytes(required) + L"    사용 가능: " +
            megabytes(free_bytes.QuadPart) + L"\r\n";
        summary += state->installed ? L"설치된 버전: " + state->installed_version :
            L"현재 설치 없음";
        std::string signing_bytes = read_bytes(
            std::filesystem::path(state->executable_root) / L"SIGNING-STATUS.txt"
        );
        while (!signing_bytes.empty() &&
               (signing_bytes.back() == '\r' || signing_bytes.back() == '\n' ||
                signing_bytes.back() == ' ')) {
            signing_bytes.pop_back();
        }
        const std::wstring signing = signing_bytes.empty()
            ? L"unknown" : mmr::utf8_to_wide(signing_bytes);
        summary += L"\r\n서명 상태: " + signing;
        state->summary = add_control(*state, L"STATIC", summary.c_str(), SS_LEFT, kSummary);
        state->start_menu = add_control(*state, L"BUTTON", L"시작 메뉴 바로가기(&S)",
            BS_AUTOCHECKBOX, kStartMenu);
        state->desktop = add_control(*state, L"BUTTON", L"바탕 화면 바로가기(&D)",
            BS_AUTOCHECKBOX, kDesktop);
        state->association = add_control(*state, L"BUTTON", L".mmrproj 파일 연결(&A)",
            BS_AUTOCHECKBOX, kAssociation);
        state->path = add_control(*state, L"BUTTON", L"런처 디렉터리를 사용자 PATH에 추가(&P)",
            BS_AUTOCHECKBOX, kPath);
        state->purge_cache = add_control(*state, L"BUTTON", L"제거 시 캐시 삭제(&C)",
            BS_AUTOCHECKBOX, kPurgeCache);
        state->purge_logs = add_control(*state, L"BUTTON", L"제거 시 로그 삭제(&L)",
            BS_AUTOCHECKBOX, kPurgeLogs);
        state->purge_autosaves = add_control(*state, L"BUTTON", L"제거 시 자동 저장 삭제(&V)",
            BS_AUTOCHECKBOX, kPurgeAutosaves);
        SendMessageW(state->start_menu, BM_SETCHECK, BST_CHECKED, 0);
        SendMessageW(state->association, BM_SETCHECK, BST_CHECKED, 0);
        if (state->installed) {
            const std::string stored = read_bytes(state->state_path);
            SendMessageW(state->start_menu, BM_SETCHECK,
                json_bool(stored, "startMenuShortcut", true) ? BST_CHECKED : BST_UNCHECKED, 0);
            SendMessageW(state->desktop, BM_SETCHECK,
                json_bool(stored, "desktopShortcut", false) ? BST_CHECKED : BST_UNCHECKED, 0);
            SendMessageW(state->association, BM_SETCHECK,
                json_bool(stored, "fileAssociation", true) ? BST_CHECKED : BST_UNCHECKED, 0);
            SendMessageW(state->path, BM_SETCHECK,
                json_bool(stored, "addToPath", false) ? BST_CHECKED : BST_UNCHECKED, 0);
        }
        state->output = add_control(*state, L"EDIT",
            L"변경 내용:\r\n- 검증된 uv를 포함합니다. Python/.venv/PySide6/FFmpeg는 번들하지 않습니다.\r\n"
            L"- 업데이트 실패 시 기존 설치를 복원합니다.\r\n"
            L"- 제거 시 사용자 프로젝트, 원본 미디어와 선택하지 않은 앱 데이터는 보존합니다.",
            ES_MULTILINE | ES_READONLY | ES_AUTOVSCROLL | WS_VSCROLL, kOutput, WS_EX_CLIENTEDGE);
        state->install = add_control(*state, L"BUTTON", L"설치(&I)", BS_DEFPUSHBUTTON, kInstall);
        state->update = add_control(*state, L"BUTTON", L"업데이트(&U)", BS_PUSHBUTTON, kUpdate);
        state->uninstall = add_control(*state, L"BUTTON", L"제거(&R)", BS_PUSHBUTTON, kUninstall);
        state->cancel = add_control(*state, L"BUTTON", L"취소(&X)", BS_PUSHBUTTON, kCancel);
        set_busy(*state, false);
        return 0;
    }
    case WM_SIZE:
        layout(*state, LOWORD(lparam), HIWORD(lparam));
        return 0;
    case WM_GETMINMAXINFO:
        {
            const UINT dpi = mmr::dpi_for_window(window);
            const SIZE minimum = mmr::window_size_for_client(
                static_cast<DWORD>(GetWindowLongPtrW(window, GWL_STYLE)),
                static_cast<DWORD>(GetWindowLongPtrW(window, GWL_EXSTYLE)), 720, 520, dpi
            );
            reinterpret_cast<MINMAXINFO*>(lparam)->ptMinTrackSize = {
                minimum.cx, minimum.cy
            };
        }
        return 0;
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
        case kInstall: start_lifecycle(*state, L"Install"); break;
        case kUpdate: start_lifecycle(*state, L"Update"); break;
        case kUninstall: start_lifecycle(*state, L"Uninstall"); break;
        case kCancel: state->cancellation.store(true); break;
        }
        return 0;
    case kComplete: {
        std::unique_ptr<Completion> result(reinterpret_cast<Completion*>(lparam));
        if (state->worker.joinable()) state->worker.join();
        set_busy(*state, false);
        std::wstring output = mmr::utf8_to_wide(result->process.standard_output);
        if (!result->process.standard_error.empty()) {
            output += L"\r\n" + mmr::utf8_to_wide(result->process.standard_error);
        }
        SetWindowTextW(state->output, output.c_str());
        if (result->process.cancelled) {
            const wchar_t* message = result->recovery_succeeded
                ? L"작업을 취소하고 이전 설치 상태를 자동 복구했습니다."
                : L"작업은 취소됐지만 자동 복구에 실패했습니다. 화면의 세부 출력을 확인하세요.";
            MessageBoxW(window, message, L"작업 취소",
                result->recovery_succeeded ? MB_OK | MB_ICONINFORMATION : MB_OK | MB_ICONERROR);
        } else if (!result->process.started || result->process.exit_code != 0) {
            MessageBoxW(window, L"작업에 실패했습니다. 이전 설치는 보존 또는 복원됐습니다.\n"
                L"화면의 세부 출력을 확인하세요.", L"유지관리 실패", MB_OK | MB_ICONERROR);
        } else {
            MessageBoxW(window, L"요청한 유지관리 작업을 완료했습니다.", L"완료",
                MB_OK | MB_ICONINFORMATION);
            broadcast_environment_change();
            if (result->mode == L"Uninstall") state->installed = false;
            else {
                state->installed = true;
                state->installed_version = state->contract.version;
            }
            set_busy(*state, false);
        }
        if (state->close_after) DestroyWindow(window);
        return 0;
    }
    case WM_CLOSE:
        if (state->busy) {
            if (MessageBoxW(window, L"진행 중인 작업을 취소하고 롤백을 기다린 뒤 닫을까요?",
                    L"설치 관리자 닫기", MB_YESNO | MB_ICONWARNING | MB_DEFBUTTON2) == IDYES) {
                state->close_after = true;
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

}  // namespace

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE, PWSTR, int show) {
    CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);
    INITCOMMONCONTROLSEX controls{sizeof(controls), ICC_STANDARD_CLASSES};
    InitCommonControlsEx(&controls);
    SetupState state;
    state.executable_root = mmr::executable_directory();
    const std::filesystem::path payload = std::filesystem::path(state.executable_root) / L"payload";
    state.has_payload = std::filesystem::is_regular_file(
        payload / L"scripts" / L"environment" / L"runtime-contract.json"
    );
    state.source_root = state.has_payload ? payload.wstring() : state.executable_root;
    std::wstring error;
    if (!mmr::read_runtime_contract(state.source_root, state.contract, error)) {
        MessageBoxW(nullptr, error.c_str(), L"설치 패키지 오류", MB_OK | MB_ICONERROR);
        return 3;
    }
    state.state_path = (
        std::filesystem::path(local_app_data()) / L"MovieMakerReproduction" / L"Installer" /
        L"install-state.json"
    ).wstring();
    state.installed = std::filesystem::is_regular_file(state.state_path);
    if (state.installed) state.installed_version = json_string(read_bytes(state.state_path), "version");

    WNDCLASSEXW window_class{};
    window_class.cbSize = sizeof(window_class);
    window_class.lpfnWndProc = window_proc;
    window_class.hInstance = instance;
    window_class.hCursor = LoadCursorW(nullptr, IDC_ARROW);
    window_class.hIcon = LoadIconW(nullptr, IDI_APPLICATION);
    window_class.hbrBackground = GetSysColorBrush(COLOR_WINDOW);
    window_class.lpszClassName = kClassName;
    if (!RegisterClassExW(&window_class)) return 4;
    const UINT initial_dpi = mmr::dpi_for_window(nullptr);
    const DWORD window_style = WS_OVERLAPPEDWINDOW | WS_CLIPCHILDREN;
    const SIZE initial_size = mmr::window_size_for_client(
        window_style, 0, 780, 600, initial_dpi
    );
    HWND window = CreateWindowExW(
        0, kClassName, L"Movie Maker Reproduction — 설치 및 유지관리",
        window_style, CW_USEDEFAULT, CW_USEDEFAULT, initial_size.cx, initial_size.cy,
        nullptr, nullptr, instance, &state
    );
    if (window == nullptr) return 4;
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
