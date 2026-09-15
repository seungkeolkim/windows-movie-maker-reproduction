#include "launcher_core.hpp"

#include <shellapi.h>

#include <algorithm>
#include <chrono>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <regex>
#include <sstream>
#include <thread>

namespace {

class unique_handle {
public:
    unique_handle() = default;
    explicit unique_handle(HANDLE value) : value_(value) {}
    ~unique_handle() { reset(); }
    unique_handle(const unique_handle&) = delete;
    unique_handle& operator=(const unique_handle&) = delete;
    unique_handle(unique_handle&& other) noexcept : value_(other.release()) {}
    unique_handle& operator=(unique_handle&& other) noexcept {
        if (this != &other) {
            reset(other.release());
        }
        return *this;
    }
    HANDLE get() const { return value_; }
    HANDLE release() {
        HANDLE result = value_;
        value_ = nullptr;
        return result;
    }
    void reset(HANDLE value = nullptr) {
        if (value_ != nullptr && value_ != INVALID_HANDLE_VALUE) {
            CloseHandle(value_);
        }
        value_ = value;
    }
    explicit operator bool() const {
        return value_ != nullptr && value_ != INVALID_HANDLE_VALUE;
    }

private:
    HANDLE value_ = nullptr;
};

std::wstring join_path(const std::wstring& left, const std::wstring& right) {
    return (std::filesystem::path(left) / std::filesystem::path(right)).wstring();
}

bool is_file(const std::wstring& path) {
    DWORD attributes = GetFileAttributesW(path.c_str());
    return attributes != INVALID_FILE_ATTRIBUTES &&
        (attributes & FILE_ATTRIBUTE_DIRECTORY) == 0;
}

std::string read_bytes(const std::wstring& path) {
    std::ifstream stream(std::filesystem::path(path), std::ios::binary);
    return std::string(
        std::istreambuf_iterator<char>(stream),
        std::istreambuf_iterator<char>()
    );
}

std::string json_string(const std::string& source, const std::string& key) {
    std::regex expression("\\\"" + key + "\\\"\\s*:\\s*\\\"([^\\\"]*)\\\"");
    std::smatch match;
    return std::regex_search(source, match, expression) ? match[1].str() : std::string();
}

std::uint64_t json_unsigned(
    const std::string& source,
    const std::string& key,
    std::uint64_t fallback
) {
    std::regex expression("\\\"" + key + "\\\"\\s*:\\s*([0-9]+)");
    std::smatch match;
    if (!std::regex_search(source, match, expression)) {
        return fallback;
    }
    try {
        return std::stoull(match[1].str());
    } catch (...) {
        return fallback;
    }
}

std::vector<std::wstring> json_string_array(
    const std::string& source,
    const std::string& key
) {
    const std::string marker = "\"" + key + "\"";
    std::size_t position = source.find(marker);
    if (position == std::string::npos) {
        return {};
    }
    position = source.find('[', position + marker.size());
    const std::size_t end = source.find(']', position);
    if (position == std::string::npos || end == std::string::npos) {
        return {};
    }

    std::vector<std::wstring> result;
    bool in_string = false;
    bool escaped = false;
    std::string current;
    for (++position; position < end; ++position) {
        const char character = source[position];
        if (!in_string) {
            if (character == '"') {
                in_string = true;
                current.clear();
            }
            continue;
        }
        if (escaped) {
            switch (character) {
            case 'n': current.push_back('\n'); break;
            case 'r': current.push_back('\r'); break;
            case 't': current.push_back('\t'); break;
            default: current.push_back(character); break;
            }
            escaped = false;
        } else if (character == '\\') {
            escaped = true;
        } else if (character == '"') {
            result.push_back(mmr::utf8_to_wide(current));
            in_string = false;
        } else {
            current.push_back(character);
        }
    }
    return result;
}

void read_pipe(HANDLE pipe, std::string* output, std::size_t limit) {
    char buffer[4096];
    DWORD read = 0;
    while (ReadFile(pipe, buffer, sizeof(buffer), &read, nullptr) && read > 0) {
        if (output->size() < limit) {
            const std::size_t remaining = limit - output->size();
            output->append(buffer, std::min<std::size_t>(remaining, read));
        }
    }
}

}  // namespace

namespace mmr {

LauncherOptions parse_launcher_options(const std::vector<std::wstring>& arguments) {
    LauncherOptions result;
    bool forwarding = false;
    for (std::size_t index = 0; index < arguments.size(); ++index) {
        const std::wstring& argument = arguments[index];
        if (forwarding) {
            result.app_arguments.push_back(argument);
            continue;
        }
        if (argument == L"--") {
            forwarding = true;
        } else if (argument == L"--verbose") {
            result.verbose = true;
        } else if (argument == L"--help" || argument == L"-h") {
            result.help = true;
        } else if (argument == L"--ffmpeg-dir") {
            if (++index >= arguments.size()) {
                result.valid = false;
                result.error = L"--ffmpeg-dir requires a directory argument.";
                return result;
            }
            result.ffmpeg_directory = arguments[index];
        } else {
            result.valid = false;
            result.error = L"Unknown launcher option: " + argument +
                L". Put application options after the first --.";
            return result;
        }
    }
    for (std::size_t index = 0; index < result.app_arguments.size(); ++index) {
        if (result.app_arguments[index] == L"--online") {
            result.online = true;
        } else if (result.app_arguments[index] == L"--project" &&
                   index + 1 < result.app_arguments.size()) {
            result.project_path = result.app_arguments[index + 1];
            ++index;
        }
    }
    return result;
}

std::wstring quote_windows_argument(const std::wstring& argument) {
    std::wstring quoted = L"\"";
    std::size_t backslashes = 0;
    for (wchar_t character : argument) {
        if (character == L'\\') {
            ++backslashes;
            continue;
        }
        if (character == L'"') {
            quoted.append(backslashes * 2 + 1, L'\\');
            quoted.push_back(L'"');
            backslashes = 0;
            continue;
        }
        quoted.append(backslashes, L'\\');
        backslashes = 0;
        quoted.push_back(character);
    }
    quoted.append(backslashes * 2, L'\\');
    quoted.push_back(L'"');
    return quoted;
}

std::wstring build_windows_command_line(
    const std::wstring& executable,
    const std::vector<std::wstring>& arguments
) {
    std::wstring command_line = quote_windows_argument(executable);
    for (const std::wstring& argument : arguments) {
        command_line.push_back(L' ');
        command_line += quote_windows_argument(argument);
    }
    return command_line;
}

std::wstring executable_directory() {
    std::vector<wchar_t> buffer(32768);
    DWORD length = GetModuleFileNameW(nullptr, buffer.data(), static_cast<DWORD>(buffer.size()));
    if (length == 0 || length == buffer.size()) {
        return {};
    }
    return std::filesystem::path(std::wstring(buffer.data(), length)).parent_path().wstring();
}

std::wstring find_application_root(const std::wstring& start_directory) {
    std::filesystem::path current(start_directory);
    for (int depth = 0; depth < 6 && !current.empty(); ++depth) {
        const std::filesystem::path contract =
            current / L"scripts" / L"environment" / L"runtime-contract.json";
        if (is_file(contract.wstring())) {
            return std::filesystem::absolute(current).lexically_normal().wstring();
        }
        if (current.parent_path() == current) {
            break;
        }
        current = current.parent_path();
    }
    return {};
}

std::wstring find_executable(const std::wstring& name) {
    std::vector<wchar_t> buffer(32768);
    DWORD length = SearchPathW(
        nullptr,
        name.c_str(),
        name.find(L'.') == std::wstring::npos ? L".exe" : nullptr,
        static_cast<DWORD>(buffer.size()),
        buffer.data(),
        nullptr
    );
    if (length == 0 || length >= buffer.size()) {
        return {};
    }
    return std::wstring(buffer.data(), length);
}

bool read_runtime_contract(
    const std::wstring& app_root,
    RuntimeContract& contract,
    std::wstring& error
) {
    const std::wstring path = join_path(
        app_root,
        L"scripts\\environment\\runtime-contract.json"
    );
    const std::string source = read_bytes(path);
    if (source.empty()) {
        error = L"The runtime contract could not be read: " + path;
        return false;
    }
    contract.version = utf8_to_wide(json_string(source, "version"));
    contract.minimum_uv_version = utf8_to_wide(json_string(source, "minimumUvVersion"));
    contract.bundled_uv_candidate = utf8_to_wide(json_string(source, "bundledCandidate"));
    contract.configure_arguments = json_string_array(source, "configure");
    contract.run_arguments = json_string_array(source, "run");
    contract.log_retention_days = static_cast<int>(
        json_unsigned(source, "retentionDays", contract.log_retention_days)
    );
    contract.maximum_log_bytes = json_unsigned(
        source, "maximumBytes", contract.maximum_log_bytes
    );
    contract.maximum_captured_process_bytes = json_unsigned(
        source,
        "maximumCapturedProcessBytes",
        contract.maximum_captured_process_bytes
    );
    if (contract.version.empty() || contract.minimum_uv_version.empty() ||
        contract.bundled_uv_candidate.empty() ||
        contract.configure_arguments.empty() || contract.run_arguments.empty()) {
        error = L"The runtime contract is incomplete or invalid.";
        return false;
    }
    return true;
}

std::wstring utf8_to_wide(const std::string& value) {
    if (value.empty()) {
        return {};
    }
    int length = MultiByteToWideChar(
        CP_UTF8, MB_ERR_INVALID_CHARS, value.data(), static_cast<int>(value.size()), nullptr, 0
    );
    UINT page = CP_UTF8;
    DWORD flags = MB_ERR_INVALID_CHARS;
    if (length == 0) {
        page = CP_ACP;
        flags = 0;
        length = MultiByteToWideChar(
            page, flags, value.data(), static_cast<int>(value.size()), nullptr, 0
        );
    }
    std::wstring result(static_cast<std::size_t>(length), L'\0');
    MultiByteToWideChar(
        page, flags, value.data(), static_cast<int>(value.size()), result.data(), length
    );
    return result;
}

std::string wide_to_utf8(const std::wstring& value) {
    if (value.empty()) {
        return {};
    }
    const int length = WideCharToMultiByte(
        CP_UTF8, 0, value.data(), static_cast<int>(value.size()), nullptr, 0, nullptr, nullptr
    );
    std::string result(static_cast<std::size_t>(length), '\0');
    WideCharToMultiByte(
        CP_UTF8, 0, value.data(), static_cast<int>(value.size()), result.data(), length,
        nullptr, nullptr
    );
    return result;
}

std::wstring format_system_error(DWORD error) {
    wchar_t* message = nullptr;
    const DWORD length = FormatMessageW(
        FORMAT_MESSAGE_ALLOCATE_BUFFER | FORMAT_MESSAGE_FROM_SYSTEM |
            FORMAT_MESSAGE_IGNORE_INSERTS,
        nullptr,
        error,
        MAKELANGID(LANG_NEUTRAL, SUBLANG_DEFAULT),
        reinterpret_cast<wchar_t*>(&message),
        0,
        nullptr
    );
    std::wstring result = length > 0 ? std::wstring(message, length) : L"Unknown error";
    if (message != nullptr) {
        LocalFree(message);
    }
    while (!result.empty() && (result.back() == L'\r' || result.back() == L'\n')) {
        result.pop_back();
    }
    return result;
}

UINT dpi_for_window(HWND window) {
    HMODULE user = GetModuleHandleW(L"user32.dll");
    using GetWindowDpi = UINT(WINAPI*)(HWND);
    auto get_window_dpi = reinterpret_cast<GetWindowDpi>(
        GetProcAddress(user, "GetDpiForWindow")
    );
    if (window != nullptr && get_window_dpi != nullptr) {
        const UINT dpi = get_window_dpi(window);
        if (dpi != 0) return dpi;
    }
    using GetSystemDpi = UINT(WINAPI*)();
    auto get_system_dpi = reinterpret_cast<GetSystemDpi>(
        GetProcAddress(user, "GetDpiForSystem")
    );
    return get_system_dpi != nullptr ? get_system_dpi() : 96;
}

int scale_for_dpi(int value, UINT dpi) {
    return MulDiv(value, static_cast<int>(dpi), 96);
}

SIZE window_size_for_client(
    DWORD style,
    DWORD extended_style,
    int width,
    int height,
    UINT dpi
) {
    RECT rectangle{0, 0, scale_for_dpi(width, dpi), scale_for_dpi(height, dpi)};
    HMODULE user = GetModuleHandleW(L"user32.dll");
    using AdjustForDpi = BOOL(WINAPI*)(LPRECT, DWORD, BOOL, DWORD, UINT);
    auto adjust_for_dpi = reinterpret_cast<AdjustForDpi>(
        GetProcAddress(user, "AdjustWindowRectExForDpi")
    );
    if (adjust_for_dpi != nullptr) {
        adjust_for_dpi(&rectangle, style, FALSE, extended_style, dpi);
    } else {
        AdjustWindowRectEx(&rectangle, style, FALSE, extended_style);
    }
    return {rectangle.right - rectangle.left, rectangle.bottom - rectangle.top};
}

HFONT create_message_font(UINT dpi) {
    NONCLIENTMETRICSW metrics{};
    metrics.cbSize = sizeof(metrics);
    HMODULE user = GetModuleHandleW(L"user32.dll");
    using ParametersForDpi = BOOL(WINAPI*)(UINT, UINT, PVOID, UINT, UINT);
    auto parameters_for_dpi = reinterpret_cast<ParametersForDpi>(
        GetProcAddress(user, "SystemParametersInfoForDpi")
    );
    if (parameters_for_dpi != nullptr && parameters_for_dpi(
            SPI_GETNONCLIENTMETRICS, sizeof(metrics), &metrics, 0, dpi)) {
        return CreateFontIndirectW(&metrics.lfMessageFont);
    }
    if (!SystemParametersInfoW(SPI_GETNONCLIENTMETRICS, sizeof(metrics), &metrics, 0)) {
        return static_cast<HFONT>(GetStockObject(DEFAULT_GUI_FONT));
    }
    const UINT system_dpi = dpi_for_window(nullptr);
    if (system_dpi != 0 && system_dpi != dpi) {
        metrics.lfMessageFont.lfHeight = MulDiv(
            metrics.lfMessageFont.lfHeight, static_cast<int>(dpi), static_cast<int>(system_dpi)
        );
    }
    return CreateFontIndirectW(&metrics.lfMessageFont);
}

void apply_font_to_children(HWND parent, HFONT font) {
    EnumChildWindows(parent, [](HWND child, LPARAM value) -> BOOL {
        SendMessageW(child, WM_SETFONT, static_cast<WPARAM>(value), TRUE);
        return TRUE;
    }, reinterpret_cast<LPARAM>(font));
}

std::wstring sanitize_diagnostic_text(
    const std::wstring& original,
    const std::wstring& user_profile
) {
    std::wstring value = original;
    if (!user_profile.empty()) {
        std::size_t position = 0;
        while ((position = value.find(user_profile, position)) != std::wstring::npos) {
            value.replace(position, user_profile.size(), L"%USERPROFILE%");
            position += 13;
        }
    }
    value = std::regex_replace(
        value,
        std::wregex(L"([a-z][a-z0-9+.-]*://)[^\\s/@]+@", std::regex_constants::icase),
        L"$1<redacted>@"
    );
    value = std::regex_replace(
        value,
        std::wregex(L"(authorization\\s*:\\s*bearer\\s+)[^\\s]+", std::regex_constants::icase),
        L"$1<redacted>"
    );
    const std::vector<std::wstring> markers = {
        L"password=", L"token=", L"secret=", L"apikey=", L"api_key="
    };
    std::wstring lowered = value;
    std::transform(lowered.begin(), lowered.end(), lowered.begin(), towlower);
    for (const std::wstring& marker : markers) {
        std::size_t position = 0;
        while ((position = lowered.find(marker, position)) != std::wstring::npos) {
            const std::size_t start = position + marker.size();
            std::size_t end = value.find_first_of(L" \t\r\n;&", start);
            if (end == std::wstring::npos) end = value.size();
            value.replace(start, end - start, L"<redacted>");
            lowered.replace(start, end - start, L"<redacted>");
            position = start + 10;
        }
    }
    return value;
}

bool append_utf8_file(const std::wstring& path, const std::wstring& value) {
    const std::string bytes = wide_to_utf8(value);
    HANDLE file = CreateFileW(
        path.c_str(), FILE_APPEND_DATA, FILE_SHARE_READ | FILE_SHARE_WRITE,
        nullptr, OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr
    );
    if (file == INVALID_HANDLE_VALUE) return false;
    DWORD written = 0;
    const BOOL ok = WriteFile(
        file, bytes.data(), static_cast<DWORD>(bytes.size()), &written, nullptr
    );
    CloseHandle(file);
    return ok != FALSE && written == bytes.size();
}

void prune_launcher_logs(
    const std::wstring& directory,
    int retention_days,
    std::uint64_t maximum_bytes
) {
    struct LogInfo {
        std::filesystem::path path;
        std::filesystem::file_time_type time;
        std::uint64_t size;
    };
    std::vector<LogInfo> logs;
    std::uint64_t total = 0;
    std::error_code error;
    const auto cutoff = std::filesystem::file_time_type::clock::now() -
        std::chrono::hours(24 * retention_days);
    for (const auto& entry : std::filesystem::directory_iterator(directory, error)) {
        if (error || !entry.is_regular_file(error) || entry.path().extension() != L".log" ||
            entry.path().filename().wstring().rfind(L"launcher-", 0) != 0) {
            error.clear();
            continue;
        }
        const auto modified = entry.last_write_time(error);
        const std::uint64_t size = error ? 0 : entry.file_size(error);
        if (!error && modified < cutoff) {
            std::filesystem::remove(entry.path(), error);
        } else if (!error) {
            logs.push_back({entry.path(), modified, size});
            total += size;
        }
        error.clear();
    }
    std::sort(logs.begin(), logs.end(), [](const LogInfo& left, const LogInfo& right) {
        return left.time < right.time;
    });
    for (const LogInfo& log : logs) {
        if (total <= maximum_bytes) break;
        std::filesystem::remove(log.path, error);
        if (!error) total -= std::min(total, log.size);
        error.clear();
    }
}

ProcessResult run_process(
    const std::wstring& executable,
    const std::vector<std::wstring>& arguments,
    const std::wstring& working_directory,
    DWORD timeout_milliseconds,
    std::atomic_bool* cancellation,
    std::size_t capture_limit,
    bool terminate_tree,
    DWORD healthy_after_milliseconds
) {
    ProcessResult result;
    SECURITY_ATTRIBUTES security{};
    security.nLength = sizeof(security);
    security.bInheritHandle = TRUE;
    unique_handle stdout_read;
    unique_handle stdout_write;
    unique_handle stderr_read;
    unique_handle stderr_write;
    HANDLE read_handle = nullptr;
    HANDLE write_handle = nullptr;
    if (!CreatePipe(&read_handle, &write_handle, &security, 0)) {
        result.system_error = GetLastError();
        return result;
    }
    stdout_read.reset(read_handle);
    stdout_write.reset(write_handle);
    if (!SetHandleInformation(stdout_read.get(), HANDLE_FLAG_INHERIT, 0)) {
        result.system_error = GetLastError();
        return result;
    }
    read_handle = nullptr;
    write_handle = nullptr;
    if (!CreatePipe(&read_handle, &write_handle, &security, 0)) {
        result.system_error = GetLastError();
        return result;
    }
    stderr_read.reset(read_handle);
    stderr_write.reset(write_handle);
    if (!SetHandleInformation(stderr_read.get(), HANDLE_FLAG_INHERIT, 0)) {
        result.system_error = GetLastError();
        return result;
    }

    STARTUPINFOW startup{};
    startup.cb = sizeof(startup);
    startup.dwFlags = STARTF_USESTDHANDLES;
    startup.hStdInput = GetStdHandle(STD_INPUT_HANDLE);
    startup.hStdOutput = stdout_write.get();
    startup.hStdError = stderr_write.get();
    PROCESS_INFORMATION process{};
    std::wstring command_line = build_windows_command_line(executable, arguments);
    std::vector<wchar_t> mutable_command(command_line.begin(), command_line.end());
    mutable_command.push_back(L'\0');

    unique_handle job;
    if (terminate_tree) {
        job.reset(CreateJobObjectW(nullptr, nullptr));
        if (job && healthy_after_milliseconds == 0) {
            JOBOBJECT_EXTENDED_LIMIT_INFORMATION limits{};
            limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
            if (!SetInformationJobObject(
                    job.get(), JobObjectExtendedLimitInformation, &limits, sizeof(limits))) {
                job.reset();
            }
        }
    }

    DWORD creation_flags = CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT | CREATE_SUSPENDED;
    if (!CreateProcessW(
            executable.c_str(), mutable_command.data(), nullptr, nullptr, TRUE, creation_flags,
            nullptr, working_directory.empty() ? nullptr : working_directory.c_str(),
            &startup, &process)) {
        result.system_error = GetLastError();
        return result;
    }
    unique_handle process_handle(process.hProcess);
    unique_handle thread_handle(process.hThread);
    result.started = true;
    if (job && !AssignProcessToJobObject(job.get(), process_handle.get())) {
        job.reset();
    }
    ResumeThread(thread_handle.get());
    stdout_write.reset();
    stderr_write.reset();
    std::thread stdout_thread(read_pipe, stdout_read.get(), &result.standard_output, capture_limit);
    std::thread stderr_thread(read_pipe, stderr_read.get(), &result.standard_error, capture_limit);

    const auto started = std::chrono::steady_clock::now();
    while (true) {
        const DWORD wait = WaitForSingleObject(process_handle.get(), 50);
        if (wait == WAIT_OBJECT_0) {
            GetExitCodeProcess(process_handle.get(), &result.exit_code);
            break;
        }
        const auto elapsed = static_cast<DWORD>(std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - started
        ).count());
        if (healthy_after_milliseconds > 0 && elapsed >= healthy_after_milliseconds) {
            result.still_running = true;
            result.exit_code = STILL_ACTIVE;
            // This startup job has no kill-on-close flag, so closing it hands off a healthy app.
            job.reset();
            break;
        }
        if (cancellation != nullptr && cancellation->load()) {
            result.cancelled = true;
        } else if (timeout_milliseconds > 0 && elapsed >= timeout_milliseconds) {
            result.timed_out = true;
        }
        if (result.cancelled || result.timed_out) {
            if (job) {
                TerminateJobObject(job.get(), ERROR_CANCELLED);
            } else {
                TerminateProcess(process_handle.get(), ERROR_CANCELLED);
            }
            WaitForSingleObject(process_handle.get(), 5000);
            GetExitCodeProcess(process_handle.get(), &result.exit_code);
            break;
        }
    }
    if (result.still_running) {
        CancelSynchronousIo(stdout_thread.native_handle());
        CancelSynchronousIo(stderr_thread.native_handle());
        // Closing our read ends lets the child continue without retaining launcher pipe handles.
        stdout_read.reset();
        stderr_read.reset();
    }
    if (stdout_thread.joinable()) {
        stdout_thread.join();
    }
    if (stderr_thread.joinable()) {
        stderr_thread.join();
    }
    return result;
}

}  // namespace mmr
