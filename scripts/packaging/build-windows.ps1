[CmdletBinding(PositionalBinding = $false)]
param(
    [string]$OutputDirectory,
    [string]$ZigPath,
    [string]$UvPath,
    [string]$CertificateThumbprint,
    [string]$TimestampUrl = "http://timestamp.digicert.com",
    [switch]$SkipTests
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$repositoryRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$contract = Get-Content -Raw -Encoding UTF8 (
    Join-Path $repositoryRoot "scripts\environment\runtime-contract.json"
) | ConvertFrom-Json
$version = [string]$contract.version
if (-not $UvPath) {
    $uvCommand = Get-Command uv.exe -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if (-not $uvCommand) {
        throw "uv.exe was not found. Pass -UvPath to the verified release binary."
    }
    $UvPath = $uvCommand.Source
}
$UvPath = (Resolve-Path -LiteralPath $UvPath).Path
$uvVersionText = (& $UvPath --version 2>&1 | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $uvVersionText -notmatch "uv\s+(\d+\.\d+\.\d+)") {
    throw "The supplied uv.exe version could not be verified: $uvVersionText"
}
if ([version]$Matches[1] -ne [version]$contract.uv.bundledVersion) {
    throw "The release requires uv $($contract.uv.bundledVersion); found $($Matches[1])."
}
$uvHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $UvPath).Hash.ToLowerInvariant()
if ($uvHash -ne [string]$contract.uv.sha256) {
    throw "The supplied uv.exe does not match the pinned release SHA-256."
}
if (-not $OutputDirectory) {
    $OutputDirectory = Join-Path $repositoryRoot "build\windows-package"
}
$OutputDirectory = [System.IO.Path]::GetFullPath($OutputDirectory).TrimEnd("\")
$releaseName = "MovieMakerReproduction-$version-windows-x64"
$releaseDirectory = Join-Path $OutputDirectory $releaseName
$payloadDirectory = Join-Path $releaseDirectory "payload"
$zipPath = Join-Path $OutputDirectory "$releaseName.zip"
$launcherBuildDirectory = Join-Path $OutputDirectory "launcher"

if (Test-Path -LiteralPath $releaseDirectory) {
    if (-not $releaseDirectory.StartsWith(
        "$OutputDirectory\", [StringComparison]::OrdinalIgnoreCase
    )) { throw "Refusing to replace a release directory outside the requested output root." }
    Remove-Item -LiteralPath $releaseDirectory -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $payloadDirectory | Out-Null

$buildArguments = @{ OutputDirectory = $launcherBuildDirectory }
if ($ZigPath) { $buildArguments.ZigPath = $ZigPath }
if ($SkipTests) { $buildArguments.SkipTests = $true }
& (Join-Path $repositoryRoot "launcher\build.ps1") @buildArguments

$payloadFiles = @(
    ".python-version",
    "pyproject.toml",
    "uv.lock",
    "README.md",
    "RELEASE-NOTES.md",
    "THIRD_PARTY_NOTICES.md"
)
foreach ($relativePath in $payloadFiles) {
    Copy-Item -LiteralPath (Join-Path $repositoryRoot $relativePath) `
        -Destination (Join-Path $payloadDirectory $relativePath) -Force
}
foreach ($directory in @(
    "src", "scripts\environment", "scripts\packaging", "docs", "licenses"
)) {
    $destination = Join-Path $payloadDirectory $directory
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $destination) | Out-Null
    Copy-Item -LiteralPath (Join-Path $repositoryRoot $directory) -Destination $destination `
        -Recurse -Force
}
Get-ChildItem -LiteralPath $payloadDirectory -Directory -Recurse -Force |
    Where-Object Name -eq "__pycache__" |
    Remove-Item -Recurse -Force
Get-ChildItem -LiteralPath $payloadDirectory -File -Recurse -Force |
    Where-Object Extension -in @(".pyc", ".pyo") |
    Remove-Item -Force
$bundledUvPath = Join-Path $payloadDirectory ([string]$contract.uv.bundledCandidate)
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $bundledUvPath) | Out-Null
Copy-Item -LiteralPath $UvPath -Destination $bundledUvPath -Force

$launcherSource = Join-Path $launcherBuildDirectory "MovieMakerLauncher.exe"
$setupSource = Join-Path $launcherBuildDirectory "MovieMakerSetup.exe"
Copy-Item -LiteralPath $launcherSource -Destination (
    Join-Path $payloadDirectory "MovieMakerLauncher.exe"
) -Force
Copy-Item -LiteralPath $setupSource -Destination (Join-Path $payloadDirectory "MovieMakerSetup.exe") `
    -Force
Copy-Item -LiteralPath $setupSource -Destination (Join-Path $releaseDirectory "MovieMakerSetup.exe") `
    -Force
Copy-Item -LiteralPath (Join-Path $repositoryRoot "RELEASE-NOTES.md") `
    -Destination (Join-Path $releaseDirectory "RELEASE-NOTES.md") -Force

$signingStatus = "unsigned"
if ($CertificateThumbprint) {
    $signTool = Get-Command signtool.exe -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if (-not $signTool) {
        throw "A certificate was supplied, but signtool.exe was not found."
    }
    foreach ($binary in @(
        (Join-Path $payloadDirectory "MovieMakerLauncher.exe"),
        (Join-Path $payloadDirectory "MovieMakerSetup.exe"),
        (Join-Path $releaseDirectory "MovieMakerSetup.exe")
    )) {
        & $signTool.Source sign /sha1 $CertificateThumbprint /fd SHA256 /tr $TimestampUrl `
            /td SHA256 $binary
        if ($LASTEXITCODE -ne 0) { throw "Code signing failed: $binary" }
        & $signTool.Source verify /pa /all $binary
        if ($LASTEXITCODE -ne 0) { throw "Authenticode verification failed: $binary" }
    }
    $signingStatus = "signed-and-verified"
}
$signingStatus | Set-Content -LiteralPath (Join-Path $payloadDirectory "SIGNING-STATUS.txt") `
    -Encoding ASCII -NoNewline
$signingStatus | Set-Content -LiteralPath (Join-Path $releaseDirectory "SIGNING-STATUS.txt") `
    -Encoding ASCII -NoNewline

$payloadHashes = @(
    Get-ChildItem -LiteralPath $payloadDirectory -File -Recurse | Sort-Object FullName |
        Where-Object Name -ne "payload-manifest.json" | ForEach-Object {
            [ordered]@{
                relativePath = $_.FullName.Substring($payloadDirectory.Length).TrimStart("\")
                sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $_.FullName).Hash.ToLowerInvariant()
                bytes = $_.Length
            }
        }
)
[ordered]@{
    schemaVersion = 1
    productId = [string]$contract.productId
    version = $version
    files = $payloadHashes
} | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (
    Join-Path $payloadDirectory "payload-manifest.json"
) -Encoding UTF8

$fileRecords = @(
    Get-ChildItem -LiteralPath $releaseDirectory -File -Recurse | Sort-Object FullName |
        Where-Object Name -ne "distribution.json" | ForEach-Object {
            [ordered]@{
                relativePath = $_.FullName.Substring($releaseDirectory.Length).TrimStart("\")
                sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $_.FullName).Hash.ToLowerInvariant()
                bytes = $_.Length
            }
        }
)
$distribution = [ordered]@{
    schemaVersion = 1
    productId = [string]$contract.productId
    displayName = [string]$contract.displayName
    version = $version
    architecture = "x86_64"
    productDirectory = "%LOCALAPPDATA%\Programs\MovieMakerReproduction"
    entryPoint = "MovieMakerLauncher.exe"
    maintenanceEntryPoint = "MovieMakerSetup.exe"
    signingStatus = $signingStatus
    bundled = [ordered]@{
        python = $false
        virtualEnvironment = $false
        pyside6 = $false
        ffmpeg = $false
        uv = $true
        dotNet = $false
    }
    bundledUv = [ordered]@{
        version = [string]$contract.uv.bundledVersion
        sha256 = $uvHash
        source = [string]$contract.uv.source
    }
    files = $fileRecords
}
$distribution | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (
    Join-Path $releaseDirectory "distribution.json"
) -Encoding UTF8

if (Test-Path -LiteralPath $zipPath) { Remove-Item -LiteralPath $zipPath -Force }
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
$archive = [System.IO.Compression.ZipFile]::Open(
    $zipPath, [System.IO.Compression.ZipArchiveMode]::Create
)
try {
    foreach ($file in Get-ChildItem -LiteralPath $releaseDirectory -File -Recurse |
            Sort-Object FullName) {
        $relative = "$releaseName/" + $file.FullName.Substring(
            $releaseDirectory.Length
        ).TrimStart("\").Replace("\", "/")
        $entry = $archive.CreateEntry($relative, [System.IO.Compression.CompressionLevel]::Optimal)
        $entry.LastWriteTime = [DateTimeOffset]::new(2000, 1, 1, 0, 0, 0, [TimeSpan]::Zero)
        $entryStream = $entry.Open()
        $sourceStream = [System.IO.File]::OpenRead($file.FullName)
        try { $sourceStream.CopyTo($entryStream) }
        finally {
            $sourceStream.Dispose()
            $entryStream.Dispose()
        }
    }
}
finally {
    $archive.Dispose()
}

$zipHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $zipPath).Hash.ToLowerInvariant()
"$zipHash  $releaseName.zip" | Set-Content -LiteralPath "$zipPath.sha256" `
    -Encoding ASCII -NoNewline
Write-Output "Windows release package completed."
Write-Output "  directory: $releaseDirectory"
Write-Output "  archive:   $zipPath"
Write-Output "  sha256:    $zipHash"
Write-Output "  signing:   $signingStatus"
