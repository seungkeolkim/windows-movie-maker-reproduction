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
    if (-not (Test-Path -LiteralPath $environmentPath -PathType Container)) {
        throw ".venv does not exist. Run .\scripts\environment\setup.ps1 first."
    }

    & (Join-Path $PSScriptRoot "check-prerequisites.ps1") -FFmpegDirectory $FFmpegDirectory
    if ($FFmpegDirectory) {
        $env:MOVIE_MAKER_FFMPEG_DIR = (Resolve-Path -LiteralPath $FFmpegDirectory).Path
    }

    $runArguments = @($runtimeContract.commands.run)
    if ($AppArguments) {
        $forwardedArguments = @($AppArguments)
        if ($forwardedArguments[0] -eq "--") {
            $forwardedArguments = @($forwardedArguments | Select-Object -Skip 1)
        }
        $runArguments += $forwardedArguments
    }

    & $uvExecutable @runArguments
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
