[CmdletBinding(PositionalBinding = $false)]
param(
    [string]$FFmpegDirectory
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$minimumUvVersion = [version]"0.12.1"
$repositoryRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)

if ($FFmpegDirectory -and -not (Test-Path -LiteralPath $FFmpegDirectory -PathType Container)) {
    throw "The specified FFmpeg directory does not exist: $FFmpegDirectory"
}

function Resolve-Executable {
    param(
        [Parameter(Mandatory)]
        [string]$Name
    )

    $fileName = "$Name.exe"
    $directories = [System.Collections.Generic.List[string]]::new()

    if ($FFmpegDirectory) {
        $directories.Add($FFmpegDirectory)
    }
    if ($env:MOVIE_MAKER_FFMPEG_DIR) {
        $directories.Add($env:MOVIE_MAKER_FFMPEG_DIR)
    }
    $directories.Add((Join-Path $repositoryRoot "tools\ffmpeg\bin"))

    foreach ($directory in $directories) {
        $candidate = Join-Path $directory $fileName
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }

    $command = Get-Command $Name -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($command) {
        return $command.Source
    }

    return $null
}

if ($env:OS -ne "Windows_NT") {
    throw "This PowerShell setup supports Windows only. Use the .sh scripts on Linux."
}
if (-not [Environment]::Is64BitOperatingSystem) {
    throw "64-bit Windows is required."
}

$uvCommand = Get-Command uv -CommandType Application -ErrorAction SilentlyContinue |
    Select-Object -First 1
if (-not $uvCommand) {
    throw "uv was not found. Install it from https://docs.astral.sh/uv/getting-started/installation/."
}

$uvVersionOutput = & $uvCommand.Source --version 2>&1
$uvExitCode = $LASTEXITCODE
$uvVersionText = ($uvVersionOutput | Out-String).Trim()
if ($uvExitCode -ne 0 -or $uvVersionText -notmatch "uv\s+(\d+\.\d+\.\d+)") {
    throw "Unable to determine the uv version: $uvVersionText"
}
$uvVersion = [version]$Matches[1]
if ($uvVersion -lt $minimumUvVersion) {
    throw "uv $minimumUvVersion or newer is required; found $uvVersion. Run uv self update or update uv with its original installer."
}

$ffmpegPath = Resolve-Executable -Name "ffmpeg"
$ffprobePath = Resolve-Executable -Name "ffprobe"
if (-not $ffmpegPath -or -not $ffprobePath) {
    throw @"
FFmpeg and ffprobe were not both found.
Install a Windows build listed at https://ffmpeg.org/download.html#build-windows, then use one of these options:
  1. Add the bin directory containing both executables to PATH.
  2. Set MOVIE_MAKER_FFMPEG_DIR to that bin directory.
  3. Pass -FFmpegDirectory C:\path\to\ffmpeg\bin to this script.
  4. Place the files in tools\ffmpeg\bin (this local directory is ignored by Git).
"@
}

$ffmpegParent = Split-Path -Parent $ffmpegPath
$ffprobeParent = Split-Path -Parent $ffprobePath
if ($ffmpegParent -ne $ffprobeParent) {
    throw "ffmpeg and ffprobe were found in different directories. Use executables from the same FFmpeg build."
}

$ffmpegVersionOutput = & $ffmpegPath -hide_banner -version 2>&1
$ffmpegExitCode = $LASTEXITCODE
$ffmpegVersion = $ffmpegVersionOutput | Select-Object -First 1
if ($ffmpegExitCode -ne 0) {
    throw "Unable to run ffmpeg: $ffmpegPath"
}
$ffprobeVersionOutput = & $ffprobePath -hide_banner -version 2>&1
$ffprobeExitCode = $LASTEXITCODE
$ffprobeVersion = $ffprobeVersionOutput | Select-Object -First 1
if ($ffprobeExitCode -ne 0) {
    throw "Unable to run ffprobe: $ffprobePath"
}

$encoderOutput = & $ffmpegPath -hide_banner -encoders 2>&1
$encoderExitCode = $LASTEXITCODE
$encoders = $encoderOutput | Out-String
if ($encoderExitCode -ne 0) {
    throw "Unable to inspect the FFmpeg encoder list."
}
if ($encoders -notmatch "(?m)\s(libx264|h264_mf)\s") {
    throw "A software H.264 encoder is required. Install an FFmpeg build containing libx264 or h264_mf."
}
if ($encoders -notmatch "(?m)\s(aac|aac_mf)\s") {
    throw "An FFmpeg build containing an AAC encoder is required."
}

$filterOutput = & $ffmpegPath -hide_banner -filters 2>&1
$filterExitCode = $LASTEXITCODE
$filters = $filterOutput | Out-String
if ($filterExitCode -ne 0) {
    throw "Unable to inspect the FFmpeg filter list."
}
$requiredFilters = @(
    "trim",
    "atrim",
    "setpts",
    "asetpts",
    "concat",
    "scale",
    "crop",
    "pad",
    "fps",
    "aresample",
    "volume",
    "afade",
    "amix",
    "xfade",
    "acrossfade",
    "drawtext"
)
$missingFilters = @(
    foreach ($filter in $requiredFilters) {
        if ($filters -notmatch "(?m)\s$([regex]::Escape($filter))\s") {
            $filter
        }
    }
)
if ($missingFilters.Count -gt 0) {
    throw "Required FFmpeg filters are missing: $($missingFilters -join ', ')"
}

Write-Host "Prerequisite check passed." -ForegroundColor Green
Write-Host "  uv:      $uvVersion"
Write-Host "  ffmpeg:  $ffmpegVersion"
Write-Host "  ffprobe: $ffprobeVersion"
Write-Host "  path:    $ffmpegParent"
