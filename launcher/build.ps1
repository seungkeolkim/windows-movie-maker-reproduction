[CmdletBinding(PositionalBinding = $false)]
param(
    [string]$OutputDirectory,
    [string]$ZigPath,
    [switch]$SkipTests
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repositoryRoot = Split-Path -Parent $PSScriptRoot
if (-not $OutputDirectory) {
    $OutputDirectory = Join-Path $repositoryRoot "build\launcher"
}
$OutputDirectory = [System.IO.Path]::GetFullPath($OutputDirectory)
New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null

$coreSource = Join-Path $PSScriptRoot "src\launcher_core.cpp"
$mainSource = Join-Path $PSScriptRoot "src\launcher_main.cpp"
$setupSource = Join-Path $PSScriptRoot "src\setup_main.cpp"
$testSource = Join-Path $PSScriptRoot "tests\launcher_contract_tests.cpp"
$probeFixtureSource = Join-Path $PSScriptRoot "tests\runtime_probe_fixture.cpp"
$resourceSource = Join-Path $PSScriptRoot "resources\launcher.rc"
$resourceDirectory = Split-Path -Parent $resourceSource
$resourceOutput = Join-Path $OutputDirectory "launcher.res"
$setupResourceSource = Join-Path $PSScriptRoot "resources\setup.rc"
$setupResourceOutput = Join-Path $OutputDirectory "setup.res"
$launcherOutput = Join-Path $OutputDirectory "MovieMakerLauncher.exe"
$setupOutput = Join-Path $OutputDirectory "MovieMakerSetup.exe"
$testOutput = Join-Path $OutputDirectory "LauncherContractTests.exe"
$probeFixtureOutput = Join-Path $OutputDirectory "RuntimeProbeFixture.exe"

if (-not $ZigPath -and $env:MMR_ZIG_PATH) {
    $ZigPath = $env:MMR_ZIG_PATH
}
if ($ZigPath) {
    $ZigPath = (Resolve-Path -LiteralPath $ZigPath).Path
    Push-Location $resourceDirectory
    try {
        & $ZigPath rc /nologo /fo $resourceOutput $resourceSource
        if ($LASTEXITCODE -ne 0) {
            throw "Zig resource compilation failed with exit code $LASTEXITCODE."
        }
        & $ZigPath rc /nologo /fo $setupResourceOutput $setupResourceSource
        if ($LASTEXITCODE -ne 0) {
            throw "Zig setup resource compilation failed with exit code $LASTEXITCODE."
        }
    }
    finally {
        Pop-Location
    }
    $common = @(
        "c++",
        "-target", "x86_64-windows-gnu",
        "-std=c++17",
        "-O2",
        "-Wno-nullability-completeness",
        "-DUNICODE",
        "-D_UNICODE",
        "-municode"
    )
    & $ZigPath @common "-Wl,--subsystem,windows" $coreSource $mainSource $resourceOutput `
        -o $launcherOutput -lcomctl32 -lcomdlg32 -lshell32 -lole32 -luuid -lgdi32
    if ($LASTEXITCODE -ne 0) {
        throw "Zig launcher compilation failed with exit code $LASTEXITCODE."
    }
    & $ZigPath @common "-Wl,--subsystem,windows" $coreSource $setupSource `
        $setupResourceOutput -o $setupOutput -lcomctl32 -lshell32 -lole32 -luuid -lgdi32
    if ($LASTEXITCODE -ne 0) {
        throw "Zig setup compilation failed with exit code $LASTEXITCODE."
    }
    & $ZigPath @common $coreSource $testSource -o $testOutput -lshell32 -lgdi32
    if ($LASTEXITCODE -ne 0) {
        throw "Zig test compilation failed with exit code $LASTEXITCODE."
    }
    & $ZigPath @common $probeFixtureSource -o $probeFixtureOutput
    if ($LASTEXITCODE -ne 0) {
        throw "Zig runtime probe fixture compilation failed with exit code $LASTEXITCODE."
    }
    $compiler = "zig $(& $ZigPath version)"
}
else {
    $compilerCommand = Get-Command cl.exe -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1
    $resourceCommand = Get-Command rc.exe -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if (-not $compilerCommand -or -not $resourceCommand) {
        throw @"
No supported build compiler is active. Run this script in an MSVC Developer PowerShell,
or pass -ZigPath to a verified portable Zig compiler. Build tools are release-time only;
they are never included in or required by the installed product.
"@
    }
    Push-Location $resourceDirectory
    try {
        & $resourceCommand.Source /nologo /fo $resourceOutput $resourceSource
        if ($LASTEXITCODE -ne 0) {
            throw "Resource compilation failed with exit code $LASTEXITCODE."
        }
        & $resourceCommand.Source /nologo /fo $setupResourceOutput $setupResourceSource
        if ($LASTEXITCODE -ne 0) {
            throw "Setup resource compilation failed with exit code $LASTEXITCODE."
        }
    }
    finally {
        Pop-Location
    }
    $common = @(
        "/nologo", "/std:c++17", "/EHsc", "/O2", "/MT", "/utf-8", "/Brepro",
        "/DUNICODE", "/D_UNICODE"
    )
    & $compilerCommand.Source @common $coreSource $mainSource $resourceOutput `
        "/Fe:$launcherOutput" /link /SUBSYSTEM:WINDOWS comctl32.lib comdlg32.lib `
        shell32.lib ole32.lib user32.lib gdi32.lib
    if ($LASTEXITCODE -ne 0) {
        throw "MSVC launcher compilation failed with exit code $LASTEXITCODE."
    }
    & $compilerCommand.Source @common $coreSource $setupSource $setupResourceOutput `
        "/Fe:$setupOutput" /link /SUBSYSTEM:WINDOWS comctl32.lib shell32.lib ole32.lib `
        user32.lib gdi32.lib
    if ($LASTEXITCODE -ne 0) {
        throw "MSVC setup compilation failed with exit code $LASTEXITCODE."
    }
    & $compilerCommand.Source @common $coreSource $testSource "/Fe:$testOutput" `
        /link /SUBSYSTEM:CONSOLE shell32.lib gdi32.lib
    if ($LASTEXITCODE -ne 0) {
        throw "MSVC test compilation failed with exit code $LASTEXITCODE."
    }
    & $compilerCommand.Source @common $probeFixtureSource "/Fe:$probeFixtureOutput" `
        /link /SUBSYSTEM:CONSOLE
    if ($LASTEXITCODE -ne 0) {
        throw "MSVC runtime probe fixture compilation failed with exit code $LASTEXITCODE."
    }
    $compiler = "MSVC $($compilerCommand.Version)"
}

if (-not $SkipTests) {
    & $testOutput
    if ($LASTEXITCODE -ne 0) {
        throw "Launcher contract tests failed with exit code $LASTEXITCODE."
    }
}

$launcherHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $launcherOutput).Hash.ToLowerInvariant()
$setupHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $setupOutput).Hash.ToLowerInvariant()
$metadata = [ordered]@{
    schemaVersion = 1
    productVersion = "0.1.0"
    architecture = "x86_64"
    compiler = $compiler
    runtimeDependencies = @(
        "Windows 10/11 Win32 system libraries",
        "Windows PowerShell 5.1 operating-system component"
    )
    dotNetRequired = $false
    pythonBundled = $false
    ffmpegBundled = $false
    signingStatus = "unsigned"
    sha256 = $launcherHash
    setupSha256 = $setupHash
}
$metadata | ConvertTo-Json -Depth 4 | Set-Content -Encoding UTF8 `
    (Join-Path $OutputDirectory "launcher-build.json")

Write-Output "Launcher build completed."
Write-Output "  executable: $launcherOutput"
Write-Output "  sha256:    $launcherHash"
Write-Output "  signing:   unsigned (no certificate supplied at this build step)"
