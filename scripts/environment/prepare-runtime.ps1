[CmdletBinding(PositionalBinding = $false)]
param(
    [Parameter(Mandatory)][ValidateSet("Setup", "Repair")]
    [string]$Mode,
    [string]$AppRoot,
    [string]$UvExecutable,
    [switch]$RecoveryOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not $AppRoot) {
    $AppRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
}
$AppRoot = [System.IO.Path]::GetFullPath($AppRoot)
$contractPath = Join-Path $AppRoot "scripts\environment\runtime-contract.json"
$contract = Get-Content -Raw -Encoding UTF8 $contractPath | ConvertFrom-Json
$venvPath = Join-Path $AppRoot ".venv"
$transactionPath = Join-Path $AppRoot ".runtime-prepare-state.json"

if (-not $UvExecutable) {
    $bundledUv = Join-Path $AppRoot ([string]$contract.uv.bundledCandidate)
    if (Test-Path -LiteralPath $bundledUv -PathType Leaf) {
        $UvExecutable = $bundledUv
    }
    else {
        $uvCommand = Get-Command uv -CommandType Application -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if (-not $uvCommand) {
            throw "uv was not found. Reinstall a complete package or install uv."
        }
        $UvExecutable = $uvCommand.Source
    }
}
$UvExecutable = (Resolve-Path -LiteralPath $UvExecutable).Path
$backupPath = $null

function Assert-OwnedEnvironmentPath {
    param([Parameter(Mandatory)][string]$Path)

    $resolvedParent = [System.IO.Path]::GetFullPath((Split-Path -Parent $Path))
    if ($resolvedParent -ne $AppRoot) {
        throw "Refusing to change an environment outside the application root: $Path"
    }
    $leaf = Split-Path -Leaf $Path
    if ($leaf -ne ".venv" -and -not $leaf.StartsWith(".venv.w11-repair-")) {
        throw "Refusing to change an environment with an unexpected name: $Path"
    }
}

function Recover-InterruptedPreparation {
    if (-not (Test-Path -LiteralPath $transactionPath -PathType Leaf)) { return $false }
    $transaction = Get-Content -Raw -Encoding UTF8 $transactionPath | ConvertFrom-Json
    $recordedBackup = [string]$transaction.backupPath
    if ($recordedBackup) {
        $recordedBackup = [System.IO.Path]::GetFullPath($recordedBackup)
        Assert-OwnedEnvironmentPath -Path $recordedBackup
    }
    if ($recordedBackup -and (Test-Path -LiteralPath $recordedBackup -PathType Container)) {
        if (Test-Path -LiteralPath $venvPath) {
            Assert-OwnedEnvironmentPath -Path $venvPath
            Remove-Item -LiteralPath $venvPath -Recurse -Force
        }
        Move-Item -LiteralPath $recordedBackup -Destination $venvPath
        Write-Output "Restored the environment from an interrupted repair."
    }
    elseif (-not [bool]$transaction.hadEnvironment -and
            (Test-Path -LiteralPath $venvPath -PathType Container)) {
        Assert-OwnedEnvironmentPath -Path $venvPath
        Remove-Item -LiteralPath $venvPath -Recurse -Force
        Write-Output "Removed an incomplete environment from an interrupted setup."
    }
    Remove-Item -LiteralPath $transactionPath -Force
    return $true
}

$null = Recover-InterruptedPreparation
if ($RecoveryOnly) {
    Write-Output "Runtime preparation recovery completed."
    return
}
$hadEnvironment = Test-Path -LiteralPath $venvPath -PathType Container

Push-Location $AppRoot
try {
    if ($Mode -eq "Setup" -and $hadEnvironment) {
        throw "An environment already exists. Inspect it and use explicit repair if needed."
    }
    if ($Mode -eq "Repair") {
        $backupPath = Join-Path $AppRoot ".venv.w11-repair-$([guid]::NewGuid().ToString('N'))"
        Assert-OwnedEnvironmentPath -Path $backupPath
    }
    [pscustomobject][ordered]@{
        schemaVersion = 1
        hadEnvironment = $hadEnvironment
        backupPath = $backupPath
    } | ConvertTo-Json | Set-Content -LiteralPath $transactionPath -Encoding UTF8
    if ($Mode -eq "Repair" -and (Test-Path -LiteralPath $venvPath -PathType Container)) {
        Assert-OwnedEnvironmentPath -Path $venvPath
        Move-Item -LiteralPath $venvPath -Destination $backupPath
        Write-Output "Moved the previous generated environment aside for transactional repair."
    }

    $syncArguments = @($contract.commands.configure)
    Write-Output "Running locked runtime synchronization."
    & $UvExecutable @syncArguments
    if ($LASTEXITCODE -ne 0) {
        throw "uv sync failed with exit code $LASTEXITCODE."
    }

    $checkArguments = @($contract.commands.run) + "--check"
    Write-Output "Checking the configured GUI runtime."
    & $UvExecutable @checkArguments
    if ($LASTEXITCODE -ne 0) {
        throw "The GUI runtime check failed with exit code $LASTEXITCODE."
    }
    if (-not (Test-Path -LiteralPath $venvPath -PathType Container)) {
        throw "uv reported success but did not create the required .venv."
    }

    if ($backupPath) {
        Assert-OwnedEnvironmentPath -Path $backupPath
        Remove-Item -LiteralPath $backupPath -Recurse -Force
        $backupPath = $null
    }
    Remove-Item -LiteralPath $transactionPath -Force
    Write-Output "Runtime environment preparation completed."
}
catch {
    $failure = $_
    if ($backupPath -and (Test-Path -LiteralPath $backupPath -PathType Container)) {
        if (Test-Path -LiteralPath $venvPath) {
            Assert-OwnedEnvironmentPath -Path $venvPath
            Remove-Item -LiteralPath $venvPath -Recurse -Force
        }
        Assert-OwnedEnvironmentPath -Path $backupPath
        Move-Item -LiteralPath $backupPath -Destination $venvPath
        [Console]::Error.WriteLine("Repair failed; the previous environment was restored.")
    }
    elseif (-not $hadEnvironment -and (Test-Path -LiteralPath $venvPath -PathType Container)) {
        Assert-OwnedEnvironmentPath -Path $venvPath
        Remove-Item -LiteralPath $venvPath -Recurse -Force
        [Console]::Error.WriteLine("Setup failed; the incomplete generated environment was removed.")
    }
    if (Test-Path -LiteralPath $transactionPath -PathType Leaf) {
        Remove-Item -LiteralPath $transactionPath -Force
    }
    throw $failure
}
finally {
    Pop-Location
}
