[CmdletBinding(PositionalBinding = $false)]
param(
    [switch]$Dev,
    [string]$FFmpegDirectory
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repositoryRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$pythonVersionFile = Join-Path $repositoryRoot ".python-version"
$runtimeContract = Get-Content -Raw -Encoding UTF8 (
    Join-Path $PSScriptRoot "runtime-contract.json"
) | ConvertFrom-Json
$bundledUvPath = Join-Path $repositoryRoot ([string]$runtimeContract.uv.bundledCandidate)
$uvExecutable = if (Test-Path -LiteralPath $bundledUvPath -PathType Leaf) {
    (Resolve-Path -LiteralPath $bundledUvPath).Path
} else {
    (Get-Command uv -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
}

Push-Location $repositoryRoot
try {
    & (Join-Path $PSScriptRoot "check-prerequisites.ps1") -FFmpegDirectory $FFmpegDirectory

    $pythonVersion = (Get-Content -Raw -Encoding UTF8 $pythonVersionFile).Trim()
    if (-not $pythonVersion) {
        throw ".python-version is empty."
    }

    Write-Host "Preparing uv-managed CPython $pythonVersion..." -ForegroundColor Cyan
    & $uvExecutable --managed-python python install $pythonVersion
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to install uv-managed CPython $pythonVersion. Update uv and try again."
    }

    $syncArguments = @($runtimeContract.commands.configure)
    if ($Dev) {
        $syncArguments = @("--managed-python", "sync", "--locked", "--group", "dev")
        Write-Host "Syncing .venv with development dependencies..." -ForegroundColor Cyan
    }
    else {
        Write-Host "Syncing .venv with runtime dependencies..." -ForegroundColor Cyan
    }

    & $uvExecutable @syncArguments
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to sync .venv. Check uv.lock and the network connection."
    }

    $uvPythonDirectory = (& $uvExecutable python dir 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to locate the uv-managed Python directory."
    }
    $baseInterpreterDirectory = (
        & $uvExecutable --managed-python run --locked --no-sync -- python -c "import sys; print(sys.base_prefix)" 2>&1 |
            Out-String
    ).Trim()
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to inspect the Python interpreter used by .venv."
    }
    if (-not $baseInterpreterDirectory.StartsWith(
        $uvPythonDirectory,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        throw ".venv does not use a uv-managed Python. Rename or remove .venv, then run setup again."
    }

    if ($FFmpegDirectory) {
        $env:MOVIE_MAKER_FFMPEG_DIR = (Resolve-Path -LiteralPath $FFmpegDirectory).Path
    }

    Write-Host "Checking the GUI runtime..." -ForegroundColor Cyan
    & $uvExecutable --managed-python run --locked --no-sync -- movie-maker --check
    if ($LASTEXITCODE -ne 0) {
        throw "The GUI runtime check failed. Review the Qt or DLL error above."
    }

    Write-Host "Environment setup completed." -ForegroundColor Green
    Write-Host "Run the app with: .\scripts\environment\run.ps1"
}
finally {
    Pop-Location
}
