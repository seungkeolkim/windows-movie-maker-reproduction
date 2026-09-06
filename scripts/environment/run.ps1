[CmdletBinding(PositionalBinding = $false)]
param(
    [string]$FFmpegDirectory,
    [Parameter(ValueFromRemainingArguments)]
    [string[]]$AppArguments
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repositoryRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$environmentPath = Join-Path $repositoryRoot ".venv"

Push-Location $repositoryRoot
try {
    if (-not (Test-Path -LiteralPath $environmentPath -PathType Container)) {
        throw ".venv does not exist. Run .\scripts\environment\setup.ps1 first."
    }

    & (Join-Path $PSScriptRoot "check-prerequisites.ps1") -FFmpegDirectory $FFmpegDirectory
    if ($FFmpegDirectory) {
        $env:MOVIE_MAKER_FFMPEG_DIR = (Resolve-Path -LiteralPath $FFmpegDirectory).Path
    }

    $runArguments = @(
        "--managed-python",
        "run",
        "--locked",
        "--no-sync",
        "--",
        "movie-maker"
    )
    if ($AppArguments) {
        $forwardedArguments = @($AppArguments)
        if ($forwardedArguments[0] -eq "--") {
            $forwardedArguments = @($forwardedArguments | Select-Object -Skip 1)
        }
        $runArguments += $forwardedArguments
    }

    & uv @runArguments
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
