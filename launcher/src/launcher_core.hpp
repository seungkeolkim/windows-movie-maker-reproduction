#pragma once

#ifndef UNICODE
#define UNICODE
#endif
#ifndef _UNICODE
#define _UNICODE
#endif

#include <windows.h>

#include <atomic>
#include <cstdint>
#include <string>
#include <vector>

namespace mmr {

struct LauncherOptions {
    bool valid = true;
    bool help = false;
    bool verbose = false;
    bool online = false;
    std::wstring error;
    std::wstring ffmpeg_directory;
    std::wstring project_path;
    std::vector<std::wstring> app_arguments;
};

struct RuntimeContract {
    std::wstring version;
    std::wstring minimum_uv_version;
    std::wstring bundled_uv_candidate;
    std::vector<std::wstring> configure_arguments;
    std::vector<std::wstring> run_arguments;
    std::uint64_t maximum_captured_process_bytes = 8192;
    int log_retention_days = 14;
    std::uint64_t maximum_log_bytes = 10 * 1024 * 1024;
};

struct ProcessResult {
    bool started = false;
    bool cancelled = false;
    bool timed_out = false;
    bool still_running = false;
    DWORD exit_code = static_cast<DWORD>(-1);
    DWORD system_error = ERROR_SUCCESS;
    std::string standard_output;
    std::string standard_error;
};

LauncherOptions parse_launcher_options(const std::vector<std::wstring>& arguments);
std::wstring quote_windows_argument(const std::wstring& argument);
std::wstring build_windows_command_line(
    const std::wstring& executable,
    const std::vector<std::wstring>& arguments
);
std::wstring executable_directory();
std::wstring find_application_root(const std::wstring& start_directory);
std::wstring find_executable(const std::wstring& name);
bool read_runtime_contract(
    const std::wstring& app_root,
    RuntimeContract& contract,
    std::wstring& error
);
std::wstring utf8_to_wide(const std::string& value);
std::string wide_to_utf8(const std::wstring& value);
std::wstring format_system_error(DWORD error);
UINT dpi_for_window(HWND window);
int scale_for_dpi(int value, UINT dpi);
SIZE window_size_for_client(DWORD style, DWORD extended_style, int width, int height, UINT dpi);
HFONT create_message_font(UINT dpi);
void apply_font_to_children(HWND parent, HFONT font);
std::wstring sanitize_diagnostic_text(
    const std::wstring& value,
    const std::wstring& user_profile
);
bool append_utf8_file(const std::wstring& path, const std::wstring& value);
void prune_launcher_logs(
    const std::wstring& directory,
    int retention_days,
    std::uint64_t maximum_bytes
);

ProcessResult run_process(
    const std::wstring& executable,
    const std::vector<std::wstring>& arguments,
    const std::wstring& working_directory,
    DWORD timeout_milliseconds,
    std::atomic_bool* cancellation,
    std::size_t capture_limit,
    bool terminate_tree = true,
    DWORD healthy_after_milliseconds = 0
);

}  // namespace mmr
