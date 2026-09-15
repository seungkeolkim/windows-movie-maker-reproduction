[CmdletBinding(PositionalBinding = $false)]
param(
    [string]$AppRoot,
    [string]$FFmpegDirectory,
    [string]$UvExecutable,
    [ValidateSet("Protocol", "Json")]
    [string]$Format = "Protocol"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$script:components = [System.Collections.Generic.List[object]]::new()

function Add-Component {
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][ValidateSet("Ready", "NotReady", "RepairRequired")]
        [string]$State,
        [Parameter(Mandatory)][string]$Summary,
        [string]$Detail = "",
        [string]$ExecutablePath = "",
        [string]$Version = ""
    )

    $script:components.Add([ordered]@{
        name = $Name
        state = $State
        summary = $Summary
        detail = $Detail
        executablePath = $ExecutablePath
        version = $Version
    })
}

function Invoke-Captured {
    param(
        [Parameter(Mandatory)][string]$Executable,
        [Parameter(Mandatory)][string[]]$Arguments,
        [Parameter(Mandatory)][string]$WorkingDirectory
    )

    $previousLocation = Get-Location
    try {
        Set-Location -LiteralPath $WorkingDirectory
        $output = & $Executable @Arguments 2>&1 | Out-String
        return [pscustomobject]@{
            ExitCode = $LASTEXITCODE
            Output = $output.Trim()
        }
    }
    catch {
        return [pscustomobject]@{
            ExitCode = -1
            Output = $_.Exception.Message
        }
    }
    finally {
        Set-Location -LiteralPath $previousLocation
    }
}

function Resolve-MediaExecutable {
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][object]$Contract,
        [Parameter(Mandatory)][string]$Root
    )

    $directories = [System.Collections.Generic.List[string]]::new()
    if ($FFmpegDirectory) {
        $directories.Add($FFmpegDirectory)
    }
    $environmentDirectory = [Environment]::GetEnvironmentVariable(
        [string]$Contract.ffmpeg.environmentVariable
    )
    if ($environmentDirectory) {
        $directories.Add($environmentDirectory)
    }
    $directories.Add((Join-Path $Root ([string]$Contract.ffmpeg.bundledCandidate)))

    foreach ($directory in $directories) {
        $candidate = Join-Path $directory "$Name.exe"
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

function Convert-ToProtocolText {
    param([AllowEmptyString()][string]$Value)

    return $Value.Replace("`r", " ").Replace("`n", " ").Replace("`t", " ")
}

try {
    if (-not $AppRoot) {
        $AppRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
    }
    $AppRoot = [System.IO.Path]::GetFullPath($AppRoot)
    $contractPath = Join-Path $AppRoot "scripts\environment\runtime-contract.json"
    if (-not (Test-Path -LiteralPath $contractPath -PathType Leaf)) {
        Add-Component -Name "files" -State "NotReady" `
            -Summary "The runtime contract is missing." -Detail $contractPath
        $contract = $null
    }
    else {
        $contract = Get-Content -Raw -Encoding UTF8 $contractPath | ConvertFrom-Json
        $missingFiles = @(
            foreach ($relativePath in $contract.requiredFiles) {
                $candidate = Join-Path $AppRoot ([string]$relativePath)
                if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) {
                    [string]$relativePath
                }
            }
        )
        if ($missingFiles.Count -gt 0) {
            Add-Component -Name "files" -State "NotReady" `
                -Summary "Required application files are missing." -Detail ($missingFiles -join ", ")
        }
        else {
            Add-Component -Name "files" -State "Ready" -Summary "Required application files are present."
        }
    }

    if ($null -ne $contract) {
        if (-not $UvExecutable) {
            $bundledUv = Join-Path $AppRoot ([string]$contract.uv.bundledCandidate)
            if (Test-Path -LiteralPath $bundledUv -PathType Leaf) {
                $UvExecutable = $bundledUv
            }
            else {
                $uvCommand = Get-Command uv -CommandType Application -ErrorAction SilentlyContinue |
                    Select-Object -First 1
                if ($uvCommand) {
                    $UvExecutable = $uvCommand.Source
                }
            }
        }
        if (-not $UvExecutable -or -not (Test-Path -LiteralPath $UvExecutable -PathType Leaf)) {
            Add-Component -Name "uv" -State "NotReady" `
                -Summary "uv was not found." `
                -Detail "This package has no bundled uv.exe. Install uv or reinstall a complete package."
        }
        else {
            $UvExecutable = (Resolve-Path -LiteralPath $UvExecutable).Path
            $uvResult = Invoke-Captured -Executable $UvExecutable -Arguments @("--version") `
                -WorkingDirectory $AppRoot
            if ($uvResult.ExitCode -ne 0 -or $uvResult.Output -notmatch "uv\s+(\d+\.\d+\.\d+)") {
                Add-Component -Name "uv" -State "RepairRequired" `
                    -Summary "The uv version could not be inspected." -Detail $uvResult.Output `
                    -ExecutablePath $UvExecutable
            }
            else {
                $uvVersion = [version]$Matches[1]
                $minimumUvVersion = [version]$contract.minimumUvVersion
                if ($uvVersion -lt $minimumUvVersion) {
                    Add-Component -Name "uv" -State "NotReady" `
                        -Summary "The uv version is too old." `
                        -Detail "Required: $minimumUvVersion; found: $uvVersion" `
                        -ExecutablePath $UvExecutable -Version $uvVersion
                }
                else {
                    Add-Component -Name "uv" -State "Ready" -Summary "uv is available." `
                        -ExecutablePath $UvExecutable -Version $uvVersion
                }
            }
        }

        $pythonVersionPath = Join-Path $AppRoot ".python-version"
        $pythonVersion = ""
        if (Test-Path -LiteralPath $pythonVersionPath -PathType Leaf) {
            $pythonVersion = (Get-Content -Raw -Encoding UTF8 $pythonVersionPath).Trim()
        }
        if (-not $pythonVersion) {
            Add-Component -Name "python" -State "NotReady" `
                -Summary "The pinned Python version could not be read."
        }
        elseif (-not $UvExecutable -or -not (Test-Path -LiteralPath $UvExecutable -PathType Leaf)) {
            Add-Component -Name "python" -State "NotReady" `
                -Summary "Install uv before inspecting the pinned Python." `
                -Version $pythonVersion
        }
        else {
            $pythonResult = Invoke-Captured -Executable $UvExecutable -Arguments @(
                "python", "find", "--managed-python", "--no-python-downloads", $pythonVersion
            ) -WorkingDirectory $AppRoot
            if ($pythonResult.ExitCode -ne 0) {
                Add-Component -Name "python" -State "NotReady" `
                    -Summary "The uv-managed Python ${pythonVersion} is missing." `
                    -Detail "Explicit environment setup can install this version." `
                    -Version $pythonVersion
            }
            else {
                Add-Component -Name "python" -State "Ready" `
                    -Summary "Pinned Python ${pythonVersion} is available." `
                    -ExecutablePath (($pythonResult.Output -split "`r?`n")[0]) `
                    -Version $pythonVersion
            }
        }

        $venvRoot = Join-Path $AppRoot ".venv"
        $venvConfig = Join-Path $venvRoot "pyvenv.cfg"
        $venvPython = Join-Path $venvRoot "Scripts\python.exe"
        if (-not (Test-Path -LiteralPath $venvRoot -PathType Container)) {
            Add-Component -Name "environment" -State "NotReady" `
                -Summary "The project environment has not been configured."
        }
        elseif (
            -not (Test-Path -LiteralPath $venvConfig -PathType Leaf) -or
            -not (Test-Path -LiteralPath $venvPython -PathType Leaf)
        ) {
            Add-Component -Name "environment" -State "RepairRequired" `
                -Summary ".venv is damaged." `
                -Detail "pyvenv.cfg or Scripts\python.exe is missing."
        }
        else {
            $venvText = Get-Content -Raw -Encoding UTF8 $venvConfig
            $versionMatch = [regex]::Match($venvText, "(?m)^version_info\s*=\s*(\S+)\s*$")
            $homeMatch = [regex]::Match($venvText, "(?m)^home\s*=\s*(.+?)\s*$")
            if (-not $versionMatch.Success -or $versionMatch.Groups[1].Value -ne $pythonVersion) {
                Add-Component -Name "environment" -State "RepairRequired" `
                    -Summary ".venv uses the wrong Python version." `
                    -Detail "Required: $pythonVersion; found: $($versionMatch.Groups[1].Value)"
            }
            elseif (-not $homeMatch.Success -or $homeMatch.Groups[1].Value -notmatch "(?i)[\\/]uv[\\/]python[\\/]") {
                Add-Component -Name "environment" -State "RepairRequired" `
                    -Summary ".venv does not use a uv-managed Python."
            }
            elseif ($UvExecutable -and (Test-Path -LiteralPath $UvExecutable -PathType Leaf)) {
                $syncArguments = @($contract.commands.configure) + "--check"
                $syncResult = Invoke-Captured -Executable $UvExecutable -Arguments $syncArguments `
                    -WorkingDirectory $AppRoot
                if ($syncResult.ExitCode -ne 0) {
                    Add-Component -Name "environment" -State "RepairRequired" `
                        -Summary ".venv does not match the current lock environment." `
                        -Detail $syncResult.Output
                }
                else {
                    Add-Component -Name "environment" -State "Ready" `
                        -Summary ".venv matches the lock environment."
                }
            }
            else {
                Add-Component -Name "environment" -State "NotReady" `
                    -Summary "Install uv before checking lock synchronization."
            }
        }

        $ffmpegPath = Resolve-MediaExecutable -Name "ffmpeg" -Contract $contract -Root $AppRoot
        $ffprobePath = Resolve-MediaExecutable -Name "ffprobe" -Contract $contract -Root $AppRoot
        if (-not $ffmpegPath -or -not $ffprobePath) {
            Add-Component -Name "ffmpeg" -State "NotReady" `
                -Summary "FFmpeg and ffprobe were not both found." `
                -Detail "Search order: configured path, MOVIE_MAKER_FFMPEG_DIR, tools\ffmpeg\bin, PATH."
        }
        elseif ((Split-Path -Parent $ffmpegPath) -ne (Split-Path -Parent $ffprobePath)) {
            Add-Component -Name "ffmpeg" -State "RepairRequired" `
                -Summary "FFmpeg and ffprobe are in different directories." `
                -Detail "$ffmpegPath | $ffprobePath"
        }
        else {
            $ffmpegVersionResult = Invoke-Captured -Executable $ffmpegPath `
                -Arguments @("-hide_banner", "-version") -WorkingDirectory $AppRoot
            $ffprobeVersionResult = Invoke-Captured -Executable $ffprobePath `
                -Arguments @("-hide_banner", "-version") -WorkingDirectory $AppRoot
            $ffmpegVersionMatch = [regex]::Match($ffmpegVersionResult.Output, "(?m)^ffmpeg version\s+(\S+)")
            $ffprobeVersionMatch = [regex]::Match($ffprobeVersionResult.Output, "(?m)^ffprobe version\s+(\S+)")
            if (
                $ffmpegVersionResult.ExitCode -ne 0 -or
                $ffprobeVersionResult.ExitCode -ne 0 -or
                -not $ffmpegVersionMatch.Success -or
                -not $ffprobeVersionMatch.Success
            ) {
                Add-Component -Name "ffmpeg" -State "RepairRequired" `
                    -Summary "The FFmpeg distribution version could not be inspected." `
                    -ExecutablePath $ffmpegPath
            }
            elseif ($ffmpegVersionMatch.Groups[1].Value -ne $ffprobeVersionMatch.Groups[1].Value) {
                Add-Component -Name "ffmpeg" -State "RepairRequired" `
                    -Summary "FFmpeg and ffprobe versions differ." `
                    -Detail "$($ffmpegVersionMatch.Groups[1].Value) | $($ffprobeVersionMatch.Groups[1].Value)"
            }
            else {
                $encoderResult = Invoke-Captured -Executable $ffmpegPath `
                    -Arguments @("-hide_banner", "-encoders") -WorkingDirectory $AppRoot
                $filterResult = Invoke-Captured -Executable $ffmpegPath `
                    -Arguments @("-hide_banner", "-filters") -WorkingDirectory $AppRoot
                $missingEncoders = @(
                    foreach ($encoder in $contract.ffmpeg.requiredEncoders) {
                        if ($encoderResult.Output -notmatch "(?m)\s$([regex]::Escape($encoder))\s") {
                            [string]$encoder
                        }
                    }
                )
                $missingFilters = @(
                    foreach ($filter in $contract.ffmpeg.requiredFilters) {
                        if ($filterResult.Output -notmatch "(?m)\s$([regex]::Escape($filter))\s") {
                            [string]$filter
                        }
                    }
                )
                if ($encoderResult.ExitCode -ne 0 -or $filterResult.ExitCode -ne 0) {
                    Add-Component -Name "ffmpeg" -State "RepairRequired" `
                        -Summary "FFmpeg capabilities could not be inspected." `
                        -ExecutablePath $ffmpegPath
                }
                elseif ($missingEncoders.Count -gt 0 -or $missingFilters.Count -gt 0) {
                    Add-Component -Name "ffmpeg" -State "NotReady" `
                        -Summary "FFmpeg is missing required encoders or filters." `
                        -Detail "Encoders: $($missingEncoders -join ', '); filters: $($missingFilters -join ', ')" `
                        -ExecutablePath $ffmpegPath -Version $ffmpegVersionMatch.Groups[1].Value
                }
                else {
                    Add-Component -Name "ffmpeg" -State "Ready" `
                        -Summary "FFmpeg and ffprobe match and provide the required capabilities." `
                        -ExecutablePath $ffmpegPath -Version $ffmpegVersionMatch.Groups[1].Value
                }
            }
        }
    }

    $overallState = "Ready"
    if (@($script:components | Where-Object state -eq "RepairRequired").Count -gt 0) {
        $overallState = "RepairRequired"
    }
    elseif (@($script:components | Where-Object state -eq "NotReady").Count -gt 0) {
        $overallState = "NotReady"
    }
    $summary = switch ($overallState) {
        "Ready" { "The editor is ready to start." }
        "RepairRequired" { "The environment needs repair. Review diagnostics." }
        default { "Required runtime components must be prepared." }
    }
    $result = [ordered]@{
        schemaVersion = 1
        appRoot = $AppRoot
        overallState = $overallState
        summary = $summary
        components = @($script:components)
    }

    if ($Format -eq "Json") {
        $result | ConvertTo-Json -Depth 6 -Compress
    }
    else {
        Write-Output "MMR_RUNTIME_V1"
        Write-Output "overall`t$overallState`t$(Convert-ToProtocolText $summary)`t$(Convert-ToProtocolText $AppRoot)"
        foreach ($component in $script:components) {
            Write-Output (
                "component`t$($component.name)`t$($component.state)`t" +
                "$(Convert-ToProtocolText $component.summary)`t" +
                "$(Convert-ToProtocolText $component.detail)`t" +
                "$(Convert-ToProtocolText $component.executablePath)`t" +
                "$(Convert-ToProtocolText $component.version)"
            )
        }
    }
}
catch {
    if ($Format -eq "Json") {
        [ordered]@{
            schemaVersion = 1
            overallState = "RepairRequired"
            summary = "An internal runtime inspection error occurred."
            error = $_.Exception.Message
            components = @($script:components)
        } | ConvertTo-Json -Depth 6 -Compress
    }
    else {
        Write-Output "MMR_RUNTIME_V1"
        Write-Output "overall`tRepairRequired`tAn internal runtime inspection error occurred.`t"
        Write-Output "component`tinspection`tRepairRequired`tInspection did not complete.`t$(Convert-ToProtocolText $_.Exception.Message)`t`t"
    }
    exit 2
}
