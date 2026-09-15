#include "../src/launcher_core.hpp"

#include <shellapi.h>

#include <atomic>
#include <chrono>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <string>
#include <thread>
#include <vector>

namespace {

int failures = 0;

void require(bool condition, const char* message) {
    if (!condition) {
        std::cerr << "FAIL: " << message << "\n";
        ++failures;
    }
}

void test_argument_round_trip() {
    const std::wstring executable = L"C:\\Program Files\\Movie Maker\\launcher.exe";
    const std::vector<std::wstring> arguments = {
        L"", L"plain", L"space value", L"한글 프로젝트", L"embedded\"quote",
        L"trailing slash\\", L"many\\\\\"quotes", L"--project",
        L"C:\\영상 작업\\제주 여행.mmrproj"
    };
    const std::wstring command_line = mmr::build_windows_command_line(executable, arguments);
    int count = 0;
    LPWSTR* parsed = CommandLineToArgvW(command_line.c_str(), &count);
    require(parsed != nullptr, "CommandLineToArgvW should parse the command line");
    if (parsed == nullptr) {
        return;
    }
    require(count == static_cast<int>(arguments.size() + 1), "argument count should survive");
    require(parsed[0] == executable, "executable path should survive");
    for (std::size_t index = 0; index < arguments.size() &&
         index + 1 < static_cast<std::size_t>(count); ++index) {
        require(parsed[index + 1] == arguments[index], "an argv unit changed in transit");
    }
    LocalFree(parsed);
}

void test_launcher_separator() {
    const std::vector<std::wstring> values = {
        L"--verbose", L"--ffmpeg-dir", L"C:\\FFmpeg Build\\bin", L"--",
        L"--online", L"", L"space value", L"한글", L"quoted\"value", L"tail\\"
    };
    const mmr::LauncherOptions options = mmr::parse_launcher_options(values);
    require(options.valid, "valid launcher options should parse");
    require(options.verbose, "verbose should be a launcher option");
    require(options.ffmpeg_directory == L"C:\\FFmpeg Build\\bin", "FFmpeg path changed");
    require(options.app_arguments.size() == 6, "all post-separator values must be forwarded");
    require(options.app_arguments[1].empty(), "an empty app argument must be preserved");
    require(options.app_arguments.back() == L"tail\\", "a trailing slash must be preserved");
}

void test_invalid_launcher_option() {
    const mmr::LauncherOptions options = mmr::parse_launcher_options({L"--project", L"x"});
    require(!options.valid, "app options before -- must be rejected");
}

void test_process_capture_and_exit_code() {
    const std::wstring powershell = mmr::find_executable(L"powershell.exe");
    require(!powershell.empty(), "Windows PowerShell should be available for the test");
    if (powershell.empty()) {
        return;
    }
    std::atomic_bool cancellation(false);
    const mmr::ProcessResult result = mmr::run_process(
        powershell,
        {L"-NoProfile", L"-NonInteractive", L"-Command",
         L"[Console]::Out.Write('stdout'); [Console]::Error.Write('stderr'); exit 23"},
        L"",
        10000,
        &cancellation,
        1024
    );
    require(result.started, "captured process should start");
    require(result.exit_code == 23, "process exit code should be preserved");
    require(result.standard_output == "stdout", "stdout should be captured");
    require(result.standard_error == "stderr", "stderr should be captured separately");
}

void test_process_timeout() {
    const std::wstring powershell = mmr::find_executable(L"powershell.exe");
    if (powershell.empty()) {
        return;
    }
    std::atomic_bool cancellation(false);
    const mmr::ProcessResult result = mmr::run_process(
        powershell,
        {L"-NoProfile", L"-NonInteractive", L"-Command", L"Start-Sleep -Seconds 10"},
        L"",
        150,
        &cancellation,
        1024
    );
    require(result.started, "timeout process should start");
    require(result.timed_out, "timeout should be classified");
}

void test_timeout_terminates_descendant_processes() {
    const std::wstring powershell = mmr::find_executable(L"powershell.exe");
    if (powershell.empty()) return;
    std::atomic_bool cancellation(false);
    const mmr::ProcessResult result = mmr::run_process(
        powershell,
        {L"-NoProfile", L"-NonInteractive", L"-Command",
         L"$p=Start-Process powershell.exe -WindowStyle Hidden -ArgumentList "
         L"'-NoProfile','-NonInteractive','-Command','Start-Sleep -Seconds 30' -PassThru; "
         L"[Console]::Out.Write($p.Id); Start-Sleep -Seconds 30"},
        L"", 500, &cancellation, 1024
    );
    require(result.timed_out, "the parent process should time out");
    require(!result.standard_output.empty(), "the descendant process id should be captured");
    if (result.standard_output.empty()) return;
    const DWORD child_id = static_cast<DWORD>(std::stoul(result.standard_output));
    HANDLE child = OpenProcess(SYNCHRONIZE, FALSE, child_id);
    if (child != nullptr) {
        require(WaitForSingleObject(child, 2000) == WAIT_OBJECT_0,
            "a timed-out job must terminate descendant processes");
        CloseHandle(child);
    }
}

void test_process_cancellation_and_capture_limit() {
    const std::wstring powershell = mmr::find_executable(L"powershell.exe");
    if (powershell.empty()) {
        return;
    }
    std::atomic_bool cancellation(false);
    std::thread cancel_thread([&cancellation]() {
        Sleep(150);
        cancellation.store(true);
    });
    const mmr::ProcessResult cancelled = mmr::run_process(
        powershell,
        {L"-NoProfile", L"-NonInteractive", L"-Command", L"Start-Sleep -Seconds 10"},
        L"", 10000, &cancellation, 1024
    );
    cancel_thread.join();
    require(cancelled.cancelled, "explicit cancellation should be classified");

    cancellation.store(false);
    const mmr::ProcessResult limited = mmr::run_process(
        powershell,
        {L"-NoProfile", L"-NonInteractive", L"-Command", L"[Console]::Out.Write('x' * 4096)"},
        L"", 10000, &cancellation, 128
    );
    require(limited.exit_code == 0, "limited-output process should succeed");
    require(limited.standard_output.size() == 128, "captured output must respect its byte limit");
}

void test_diagnostic_redaction_and_nonfatal_write_failure() {
    const std::wstring sanitized = mmr::sanitize_diagnostic_text(
        L"C:\\Users\\Tester\\project token=abc123 password=hunter2 safe=value",
        L"C:\\Users\\Tester"
    );
    require(sanitized.find(L"C:\\Users\\Tester") == std::wstring::npos,
        "the user profile should be redacted");
    require(sanitized.find(L"abc123") == std::wstring::npos,
        "token values should be redacted");
    require(sanitized.find(L"hunter2") == std::wstring::npos,
        "password values should be redacted");
    require(sanitized.find(L"safe=value") != std::wstring::npos,
        "non-sensitive diagnostics should remain");
    const std::wstring credentials = mmr::sanitize_diagnostic_text(
        L"https://user:pass@example.invalid/simple Authorization: Bearer abc.def", L""
    );
    require(credentials.find(L"user:pass") == std::wstring::npos,
        "URL credentials should be redacted");
    require(credentials.find(L"abc.def") == std::wstring::npos,
        "bearer credentials should be redacted");
    require(!mmr::append_utf8_file(L"Z:\\missing\\launcher.log", L"diagnostic"),
        "an unavailable log path should fail without throwing");
}

void test_healthy_start_is_distinct_from_immediate_failure() {
    const std::wstring powershell = mmr::find_executable(L"powershell.exe");
    if (powershell.empty()) return;
    std::atomic_bool cancellation(false);
    const mmr::ProcessResult immediate = mmr::run_process(
        powershell,
        {L"-NoProfile", L"-NonInteractive", L"-Command", L"exit 41"},
        L"", 5000, &cancellation, 256, true, 300
    );
    require(!immediate.still_running && immediate.exit_code == 41,
        "an immediate startup failure must preserve its exit code");
    const mmr::ProcessResult healthy = mmr::run_process(
        powershell,
        {L"-NoProfile", L"-NonInteractive", L"-Command", L"Start-Sleep -Milliseconds 800"},
        L"", 5000, &cancellation, 256, true, 150
    );
    require(healthy.still_running, "a process surviving the startup window should be healthy");
}

void test_log_retention_and_capacity_pruning() {
    const std::filesystem::path root = std::filesystem::temp_directory_path() /
        (L"mmr-log-test-" + std::to_wstring(GetCurrentProcessId()));
    std::error_code error;
    std::filesystem::remove_all(root, error);
    std::filesystem::create_directories(root);
    const auto write = [&root](const wchar_t* name, std::size_t bytes) {
        std::ofstream stream(root / name, std::ios::binary);
        stream << std::string(bytes, 'x');
    };
    write(L"launcher-old.log", 20);
    write(L"launcher-recent-a.log", 80);
    write(L"launcher-recent-b.log", 80);
    write(L"unrelated.log", 200);
    const auto now = std::filesystem::file_time_type::clock::now();
    std::filesystem::last_write_time(root / L"launcher-old.log", now - std::chrono::hours(48));
    std::filesystem::last_write_time(root / L"launcher-recent-a.log", now - std::chrono::hours(2));
    std::filesystem::last_write_time(root / L"launcher-recent-b.log", now - std::chrono::hours(1));

    mmr::prune_launcher_logs(root.wstring(), 1, 100);

    require(!std::filesystem::exists(root / L"launcher-old.log"),
        "expired launcher logs should be removed");
    require(!std::filesystem::exists(root / L"launcher-recent-a.log"),
        "the oldest launcher log should be removed to meet capacity");
    require(std::filesystem::exists(root / L"launcher-recent-b.log"),
        "the newest launcher log should remain within capacity");
    require(std::filesystem::exists(root / L"unrelated.log"),
        "log pruning must not remove files outside the launcher namespace");
    std::filesystem::remove_all(root, error);
}

void test_dpi_scaling_contract() {
    require(mmr::scale_for_dpi(100, 96) == 100, "96 DPI should preserve logical pixels");
    require(mmr::scale_for_dpi(100, 192) == 200, "200 percent DPI should double dimensions");
    const SIZE outer = mmr::window_size_for_client(
        WS_OVERLAPPEDWINDOW | WS_CLIPCHILDREN, 0, 720, 600, 192
    );
    require(outer.cx >= 1440 && outer.cy >= 1200,
        "the outer window must preserve the requested client area at 200 percent DPI");
}

}  // namespace

int wmain() {
    test_argument_round_trip();
    test_launcher_separator();
    test_invalid_launcher_option();
    test_process_capture_and_exit_code();
    test_process_timeout();
    test_timeout_terminates_descendant_processes();
    test_process_cancellation_and_capture_limit();
    test_diagnostic_redaction_and_nonfatal_write_failure();
    test_healthy_start_is_distinct_from_immediate_failure();
    test_log_retention_and_capacity_pruning();
    test_dpi_scaling_contract();
    if (failures == 0) {
        std::cout << "Launcher contract tests passed.\n";
        return 0;
    }
    std::cerr << failures << " launcher contract test(s) failed.\n";
    return 1;
}
