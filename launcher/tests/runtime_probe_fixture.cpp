#include <windows.h>

#include <filesystem>
#include <iostream>
#include <string>

int wmain(int argc, wchar_t** argv) {
    if (argc < 2) return 2;
    const std::wstring command = argv[argc - 1];
    const std::wstring name = std::filesystem::path(argv[0]).stem().wstring();
    if (command == L"-version") {
        std::wcout << (name == L"ffprobe" ? L"ffprobe" : L"ffmpeg")
                   << L" version fixture-1 Copyright fixture\n";
        return 0;
    }
    if (command == L"-encoders") {
        std::wcout << L" V..... libx264 fixture\n A..... aac fixture\n";
        if (GetEnvironmentVariableW(L"MMR_PROBE_MISSING_CAPABILITIES", nullptr, 0) > 0) {
            std::wcout << L"missing mode\n";
            return 0;
        }
        return 0;
    }
    if (command == L"-filters") {
        if (GetEnvironmentVariableW(L"MMR_PROBE_MISSING_CAPABILITIES", nullptr, 0) > 0) {
            std::wcout << L" ... trim fixture\n";
            return 0;
        }
        std::wcout
            << L" ... trim fixture\n ... atrim fixture\n ... setpts fixture\n"
            << L" ... asetpts fixture\n ... concat fixture\n ... scale fixture\n"
            << L" ... crop fixture\n ... pad fixture\n ... fps fixture\n"
            << L" ... format fixture\n ... setsar fixture\n ... aresample fixture\n"
            << L" ... aformat fixture\n ... atempo fixture\n ... adelay fixture\n"
            << L" ... volume fixture\n ... afade fixture\n ... amix fixture\n"
            << L" ... alimiter fixture\n ... apad fixture\n ... anull fixture\n"
            << L" ... anullsrc fixture\n ... xfade fixture\n ... acrossfade fixture\n"
            << L" ... drawtext fixture\n ... tpad fixture\n ... transpose fixture\n"
            << L" ... hflip fixture\n ... vflip fixture\n ... eq fixture\n"
            << L" ... colorbalance fixture\n ... hue fixture\n";
        return 0;
    }
    return 3;
}
