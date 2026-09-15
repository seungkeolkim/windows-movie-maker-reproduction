[CmdletBinding(PositionalBinding = $false)]
param(
    [Parameter(Mandatory)][ValidateSet("Install", "Update", "Uninstall", "Recover")]
    [string]$Mode,
    [string]$SourceDirectory,
    [string]$InstallDirectory,
    [string]$StateDirectory,
    [string]$StartMenuDirectory,
    [string]$DesktopDirectory,
    [string]$RegistryTestRoot,
    [string]$UserPathTestFile,
    [string]$AppDataTestRoot,
    [switch]$StartMenuShortcut,
    [switch]$DesktopShortcut,
    [switch]$FileAssociation,
    [switch]$AddToPath,
    [switch]$PurgeCache,
    [switch]$PurgeLogs,
    [switch]$PurgeAutosaves,
    [switch]$FailAfterSwap,
    [switch]$FailAfterRegistrations,
    [switch]$ExitAfterSwap
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$productId = "MovieMakerReproduction"
$displayName = "Movie Maker Reproduction"
$localAppData = [Environment]::GetFolderPath("LocalApplicationData")

if (-not $InstallDirectory) {
    $InstallDirectory = Join-Path $localAppData "Programs\$productId"
}
if (-not $StateDirectory) {
    $StateDirectory = Join-Path $localAppData "$productId\Installer"
}
if (-not $StartMenuDirectory) {
    $StartMenuDirectory = Join-Path (
        [Environment]::GetFolderPath("StartMenu")
    ) "Programs\$displayName"
}
if (-not $DesktopDirectory) {
    $DesktopDirectory = [Environment]::GetFolderPath("DesktopDirectory")
}

$InstallDirectory = [System.IO.Path]::GetFullPath($InstallDirectory).TrimEnd("\")
$StateDirectory = [System.IO.Path]::GetFullPath($StateDirectory).TrimEnd("\")
$statePath = Join-Path $StateDirectory "install-state.json"
$launcherPath = Join-Path $InstallDirectory "MovieMakerLauncher.exe"
$startMenuPath = Join-Path $StartMenuDirectory "$displayName.lnk"
$desktopPath = Join-Path $DesktopDirectory "$displayName.lnk"
$classesRoot = if ($RegistryTestRoot) {
    "$($RegistryTestRoot.TrimEnd('\'))\Classes"
} else {
    "Software\Classes"
}
$environmentKey = if ($RegistryTestRoot) {
    "$($RegistryTestRoot.TrimEnd('\'))\Environment"
} else {
    "Environment"
}

function Write-Stage {
    param([Parameter(Mandatory)][string]$Name, [Parameter(Mandatory)][string]$Message)
    Write-Output "[$Name] $Message"
}

function Get-FailureGuidance {
    param([Parameter(Mandatory)][System.Management.Automation.ErrorRecord]$Record)
    $exception = $Record.Exception
    $nativeCode = $exception.HResult -band 0xffff
    if ($exception -is [System.UnauthorizedAccessException] -or $nativeCode -eq 5) {
        return "[permission] The current user cannot modify an installation target. Check access permissions."
    }
    if ($nativeCode -eq 32 -or $nativeCode -eq 33) {
        return "[files-in-use] Close the editor and launcher, then retry. A Windows restart is only needed if the locking process cannot be closed."
    }
    if ($nativeCode -eq 112) {
        return "[disk-space] The target volume does not have enough free space."
    }
    return "[failure] The requested lifecycle operation did not complete."
}

function Get-Sha256 {
    param([Parameter(Mandatory)][string]$LiteralPath)
    $algorithm = [System.Security.Cryptography.SHA256]::Create()
    $stream = [System.IO.File]::OpenRead($LiteralPath)
    try {
        return -join ($algorithm.ComputeHash($stream) | ForEach-Object { $_.ToString("x2") })
    }
    finally {
        $stream.Dispose()
        $algorithm.Dispose()
    }
}

function Assert-ExactOwnedPath {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][string]$Expected
    )
    $resolved = [System.IO.Path]::GetFullPath($Path).TrimEnd("\")
    $resolvedExpected = [System.IO.Path]::GetFullPath($Expected).TrimEnd("\")
    if (-not $resolved.Equals($resolvedExpected, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to modify an unexpected path: $resolved"
    }
    if ([System.IO.Path]::GetPathRoot($resolved).TrimEnd("\") -eq $resolved) {
        throw "Refusing to modify a filesystem root."
    }
}

function Read-State {
    if (-not (Test-Path -LiteralPath $statePath -PathType Leaf)) {
        return $null
    }
    $state = Get-Content -Raw -Encoding UTF8 $statePath | ConvertFrom-Json
    if ($state.productId -ne $productId) {
        throw "The installer state belongs to a different product."
    }
    Assert-ExactOwnedPath -Path ([string]$state.installDirectory) -Expected $InstallDirectory
    return $state
}

function Write-State {
    param([Parameter(Mandatory)][object]$State)
    New-Item -ItemType Directory -Force -Path $StateDirectory | Out-Null
    $temporary = Join-Path $StateDirectory "install-state-$([guid]::NewGuid().ToString('N')).tmp"
    ConvertTo-Json -InputObject $State -Depth 6 | Set-Content -LiteralPath $temporary `
        -Encoding UTF8
    Move-Item -LiteralPath $temporary -Destination $statePath -Force
}

function Get-FileRecords {
    param([Parameter(Mandatory)][string]$Root)
    return @(
        Get-ChildItem -LiteralPath $Root -File -Recurse | Sort-Object FullName | ForEach-Object {
            [pscustomobject][ordered]@{
                relativePath = $_.FullName.Substring($Root.Length).TrimStart("\")
                sha256 = Get-Sha256 -LiteralPath $_.FullName
            }
        }
    )
}

function Test-PayloadIntegrity {
    param([Parameter(Mandatory)][string]$Root)
    $manifestPath = Join-Path $Root "payload-manifest.json"
    if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
        Write-Stage "integrity" "No payload manifest is present; this is a source/development install."
        return
    }
    $manifest = Get-Content -Raw -Encoding UTF8 $manifestPath | ConvertFrom-Json
    if ($manifest.productId -ne $productId) {
        throw "The payload integrity manifest belongs to a different product."
    }
    $listed = [System.Collections.Generic.HashSet[string]]::new(
        [StringComparer]::OrdinalIgnoreCase
    )
    foreach ($record in @($manifest.files)) {
        $relativePath = [string]$record.relativePath
        if (-not $listed.Add($relativePath)) {
            throw "Payload integrity failed; duplicate path: $relativePath"
        }
        $candidate = [System.IO.Path]::GetFullPath((
            Join-Path $Root $relativePath
        ))
        if (-not $candidate.StartsWith("$Root\", [StringComparison]::OrdinalIgnoreCase)) {
            throw "The payload manifest contains a path outside the source directory."
        }
        if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            throw "Payload integrity failed; missing file: $($record.relativePath)"
        }
        if ((Get-Sha256 -LiteralPath $candidate) -ne [string]$record.sha256) {
            throw "Payload integrity failed; hash mismatch: $($record.relativePath)"
        }
    }
    foreach ($file in Get-ChildItem -LiteralPath $Root -File -Recurse) {
        $relativePath = $file.FullName.Substring($Root.Length).TrimStart("\")
        if ($relativePath -eq "payload-manifest.json") { continue }
        if (-not $listed.Contains($relativePath)) {
            throw "Payload integrity failed; unlisted file: $relativePath"
        }
    }
    Write-Stage "integrity" "Verified all files listed in the payload manifest."
}

function Get-RegistryValue {
    param(
        [Parameter(Mandatory)][string]$Key,
        [Parameter(Mandatory)][AllowEmptyString()][string]$Name
    )
    $registryKey = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($Key, $false)
    try {
        if ($null -eq $registryKey -or $registryKey.GetValueNames() -notcontains $Name) {
            return [pscustomobject][ordered]@{
                exists = $false; value = $null; kind = "String"
            }
        }
        return [pscustomobject][ordered]@{
            exists = $true
            value = [string]$registryKey.GetValue(
                $Name, $null, [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames
            )
            kind = [string]$registryKey.GetValueKind($Name)
        }
    }
    finally {
        if ($null -ne $registryKey) { $registryKey.Dispose() }
    }
}

function Set-RegistryValue {
    param(
        [Parameter(Mandatory)][string]$Key,
        [Parameter(Mandatory)][AllowEmptyString()][string]$Name,
        [AllowNull()][string]$Value,
        [string]$Kind = "String"
    )
    $registryKey = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey($Key)
    try {
        $registryKind = [Microsoft.Win32.RegistryValueKind]::$Kind
        $registryKey.SetValue($Name, $Value, $registryKind)
    }
    finally {
        $registryKey.Dispose()
    }
}

function Remove-RegistryValue {
    param(
        [Parameter(Mandatory)][string]$Key,
        [Parameter(Mandatory)][AllowEmptyString()][string]$Name
    )
    $registryKey = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($Key, $true)
    try {
        if ($null -ne $registryKey) { $registryKey.DeleteValue($Name, $false) }
    }
    finally {
        if ($null -ne $registryKey) { $registryKey.Dispose() }
    }
}

function Register-Value {
    param(
        [Parameter(Mandatory)][AllowEmptyCollection()]
        [System.Collections.Generic.List[object]]$Records,
        [Parameter(Mandatory)][string]$Key,
        [Parameter(Mandatory)][AllowEmptyString()][string]$Name,
        [Parameter(Mandatory)][string]$Value,
        [string]$Kind = "String",
        [object[]]$ExistingRecords = @()
    )
    $owned = @($ExistingRecords | Where-Object {
        $null -ne $_ -and $_.PSObject.Properties.Name -contains "key" -and
        ([string]$_.key) -eq $Key -and ([string]$_.name) -eq $Name
    } | Select-Object -First 1)
    $current = Get-RegistryValue -Key $Key -Name $Name
    if ($owned.Count -gt 0) {
        $previousRecord = $owned[0]
        if (-not $current.exists -or $current.value -ne [string]$previousRecord.installedValue) {
            Write-Stage "preserve" "Registry value was changed by the user: $Key"
            $Records.Add($previousRecord)
            return
        }
        $previous = [pscustomobject][ordered]@{
            exists = [bool]$previousRecord.previousExists
            value = $previousRecord.previousValue
            kind = [string]$previousRecord.previousKind
        }
    }
    else {
        $previous = $current
    }
    Set-RegistryValue -Key $Key -Name $Name -Value $Value -Kind $Kind
    $Records.Add([pscustomobject][ordered]@{
        key = $Key
        name = $Name
        previousExists = $previous.exists
        previousValue = $previous.value
        previousKind = $previous.kind
        installedValue = $Value
        installedKind = $Kind
    })
}

function Restore-RegistryRecords {
    param([Parameter(Mandatory)][AllowEmptyCollection()][object[]]$Records)
    foreach ($record in @($Records)) {
        $current = Get-RegistryValue -Key ([string]$record.key) -Name ([string]$record.name)
        if (-not $current.exists -or $current.value -ne [string]$record.installedValue) {
            Write-Stage "preserve" "Registry value was changed by the user: $($record.key)"
            continue
        }
        if ([bool]$record.previousExists) {
            Set-RegistryValue -Key ([string]$record.key) -Name ([string]$record.name) `
                -Value ([string]$record.previousValue) -Kind ([string]$record.previousKind)
        }
        else {
            Remove-RegistryValue -Key ([string]$record.key) -Name ([string]$record.name)
        }
    }
}

function Get-RegistrySnapshot {
    param(
        [Parameter(Mandatory)][string]$Key,
        [Parameter(Mandatory)][AllowEmptyString()][string]$Name
    )
    $value = Get-RegistryValue -Key $Key -Name $Name
    return [pscustomobject][ordered]@{
        key = $Key
        name = $Name
        exists = $value.exists
        value = $value.value
        kind = $value.kind
    }
}

function Restore-RegistrySnapshots {
    param([Parameter(Mandatory)][AllowEmptyCollection()][object[]]$Snapshots)
    foreach ($snapshot in @($Snapshots)) {
        if ([bool]$snapshot.exists) {
            Set-RegistryValue -Key ([string]$snapshot.key) -Name ([string]$snapshot.name) `
                -Value ([string]$snapshot.value) -Kind ([string]$snapshot.kind)
        }
        else {
            Remove-RegistryValue -Key ([string]$snapshot.key) -Name ([string]$snapshot.name)
        }
    }
}

function Get-UserPath {
    if ($UserPathTestFile) {
        if (Test-Path -LiteralPath $UserPathTestFile -PathType Leaf) {
            return Get-Content -Raw -Encoding UTF8 $UserPathTestFile
        }
        return ""
    }
    $value = Get-RegistryValue -Key $environmentKey -Name "Path"
    return if ($value.exists) { [string]$value.value } else { "" }
}

function Set-UserPath {
    param([AllowEmptyString()][string]$Value)
    if ($UserPathTestFile) {
        $parent = Split-Path -Parent ([System.IO.Path]::GetFullPath($UserPathTestFile))
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
        Set-Content -LiteralPath $UserPathTestFile -Value $Value -Encoding UTF8 -NoNewline
        return
    }
    Set-RegistryValue -Key $environmentKey -Name "Path" -Value $Value -Kind "ExpandString"
    [Environment]::SetEnvironmentVariable("Path", $Value, "User")
}

function Add-PathEntry {
    $previous = Get-UserPath
    $entries = @($previous -split ";" | Where-Object { $_ })
    if ($entries | Where-Object { $_.TrimEnd("\").Equals(
        $InstallDirectory, [StringComparison]::OrdinalIgnoreCase
    ) }) {
        return [pscustomobject][ordered]@{
            changed = $false; previous = $previous; installed = $previous
        }
    }
    $installed = if ($previous) { "$($previous.TrimEnd(';'));$InstallDirectory" } else {
        $InstallDirectory
    }
    Set-UserPath $installed
    return [pscustomobject][ordered]@{
        changed = $true; previous = $previous; installed = $installed
    }
}

function Restore-PathEntry {
    param([Parameter(Mandatory)][object]$Record)
    if (-not [bool]$Record.changed) { return }
    $current = Get-UserPath
    if ($current -eq [string]$Record.installed) {
        Set-UserPath ([string]$Record.previous)
        return
    }
    $remaining = @(
        $current -split ";" | Where-Object {
            $_ -and -not $_.TrimEnd("\").Equals(
                $InstallDirectory, [StringComparison]::OrdinalIgnoreCase
            )
        }
    )
    Set-UserPath ($remaining -join ";")
    Write-Stage "preserve" "Kept user PATH changes while removing the installer-owned entry."
}

function New-OwnedShortcut {
    param([Parameter(Mandatory)][string]$Path)
    $parent = Split-Path -Parent $Path
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($Path)
    $shortcut.TargetPath = $launcherPath
    $shortcut.WorkingDirectory = $InstallDirectory
    $shortcut.Description = "$displayName runtime launcher"
    $shortcut.Save()
    return [pscustomobject][ordered]@{
        path = $Path
        sha256 = Get-Sha256 -LiteralPath $Path
    }
}

function Get-ShortcutSnapshot {
    param([Parameter(Mandatory)][string]$Path)
    $exists = Test-Path -LiteralPath $Path -PathType Leaf
    return [pscustomobject][ordered]@{
        path = $Path
        exists = $exists
        bytes = if ($exists) { [System.IO.File]::ReadAllBytes($Path) } else { $null }
    }
}

function Restore-ShortcutSnapshots {
    param([Parameter(Mandatory)][AllowEmptyCollection()][object[]]$Snapshots)
    foreach ($snapshot in @($Snapshots)) {
        $path = [string]$snapshot.path
        if ([bool]$snapshot.exists) {
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $path) | Out-Null
            [System.IO.File]::WriteAllBytes($path, [byte[]]$snapshot.bytes)
        }
        elseif (Test-Path -LiteralPath $path -PathType Leaf) {
            Remove-Item -LiteralPath $path -Force
        }
    }
}

function Test-ShortcutCanBeReplaced {
    param([Parameter(Mandatory)][string]$Path, [object[]]$ExistingRecords = @())
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $true }
    $owned = @($ExistingRecords | Where-Object {
        $null -ne $_ -and $_.PSObject.Properties.Name -contains "path" -and
        ([string]$_.path).Equals($Path, [StringComparison]::OrdinalIgnoreCase)
    } | Select-Object -First 1)
    if ($owned.Count -eq 0) { return $false }
    $currentHash = Get-Sha256 -LiteralPath $Path
    return $currentHash -eq [string]$owned[0].sha256
}

function Remove-OwnedShortcut {
    param([Parameter(Mandatory)][object]$Record)
    if (-not (Test-Path -LiteralPath $Record.path -PathType Leaf)) { return }
    $currentHash = Get-Sha256 -LiteralPath $Record.path
    if ($currentHash -eq [string]$Record.sha256) {
        Remove-Item -LiteralPath $Record.path -Force
    }
    else {
        Write-Stage "preserve" "Shortcut was changed by the user: $($Record.path)"
    }
}

function Remove-EmptyDirectories {
    param([Parameter(Mandatory)][string]$Root)
    if (-not (Test-Path -LiteralPath $Root -PathType Container)) { return }
    Get-ChildItem -LiteralPath $Root -Directory -Recurse | Sort-Object FullName -Descending |
        ForEach-Object {
            if (@(Get-ChildItem -LiteralPath $_.FullName -Force).Count -eq 0) {
                Remove-Item -LiteralPath $_.FullName -Force
            }
        }
    if (@(Get-ChildItem -LiteralPath $Root -Force).Count -eq 0) {
        Remove-Item -LiteralPath $Root -Force
    }
}

function Remove-OwnedFiles {
    param([Parameter(Mandatory)][AllowEmptyCollection()][object[]]$Records)
    Assert-ExactOwnedPath -Path $InstallDirectory -Expected $InstallDirectory
    foreach ($record in @($Records)) {
        $path = Join-Path $InstallDirectory ([string]$record.relativePath)
        $full = [System.IO.Path]::GetFullPath($path)
        if (-not $full.StartsWith(
            "$InstallDirectory\", [StringComparison]::OrdinalIgnoreCase
        )) {
            throw "Installer state contains a path outside the product directory."
        }
        if (-not (Test-Path -LiteralPath $full -PathType Leaf)) { continue }
        $hash = Get-Sha256 -LiteralPath $full
        if ($hash -eq [string]$record.sha256) {
            Remove-Item -LiteralPath $full -Force
        }
        else {
            Write-Stage "preserve" "Product file was changed after install: $($record.relativePath)"
        }
    }
    Remove-EmptyDirectories -Root $InstallDirectory
}

function Preserve-NonOwnedUpdateFiles {
    param(
        [Parameter(Mandatory)][string]$OldRoot,
        [Parameter(Mandatory)][object[]]$OldRecords,
        [Parameter(Mandatory)][string]$NewRoot,
        [Parameter(Mandatory)][string]$TransactionId
    )
    $preservedRoot = Join-Path $StateDirectory "PreservedFiles\$TransactionId"
    foreach ($file in Get-ChildItem -LiteralPath $OldRoot -File -Recurse) {
        $relative = $file.FullName.Substring($OldRoot.Length).TrimStart("\")
        $record = @($OldRecords | Where-Object relativePath -eq $relative | Select-Object -First 1)
        $unchangedOwned = $false
        if ($record.Count -gt 0) {
            $hash = Get-Sha256 -LiteralPath $file.FullName
            $unchangedOwned = $hash -eq [string]$record[0].sha256
        }
        if ($unchangedOwned) { continue }
        $destination = Join-Path $NewRoot $relative
        if (Test-Path -LiteralPath $destination) {
            $destination = Join-Path $preservedRoot $relative
            Write-Stage "preserve" "Saved a user-modified colliding file outside the product directory: $relative"
        }
        else {
            Write-Stage "preserve" "Kept a non-installer file across update: $relative"
        }
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $destination) | Out-Null
        Copy-Item -LiteralPath $file.FullName -Destination $destination -Force
    }
}

function Assert-TransactionPath {
    param([Parameter(Mandatory)][string]$Path)
    $resolved = [System.IO.Path]::GetFullPath($Path)
    $expectedParent = [System.IO.Path]::GetFullPath((Split-Path -Parent $InstallDirectory))
    $leaf = Split-Path -Leaf $resolved
    $installLeaf = Split-Path -Leaf $InstallDirectory
    if ((Split-Path -Parent $resolved) -ne $expectedParent -or
        ($leaf -notlike "$installLeaf.old-*" -and $leaf -notlike "$installLeaf.new-*")) {
        throw "Refusing an unexpected transaction path: $resolved"
    }
}

function Recover-InterruptedInstall {
    $parent = Split-Path -Parent $InstallDirectory
    $leaf = Split-Path -Leaf $InstallDirectory
    $backups = @(
        Get-ChildItem -LiteralPath $parent -Directory -Filter "$leaf.old-*" -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTime -Descending
    )
    $stages = @(
        Get-ChildItem -LiteralPath $parent -Directory -Filter "$leaf.new-*" -ErrorAction SilentlyContinue
    )
    if ($backups.Count -gt 0) {
        $committed = $false
        $state = Read-State
        $installedContract = Join-Path $InstallDirectory "scripts\environment\runtime-contract.json"
        if ($null -ne $state -and (Test-Path -LiteralPath $installedContract -PathType Leaf)) {
            $installedVersion = [string](
                Get-Content -Raw -Encoding UTF8 $installedContract | ConvertFrom-Json
            ).version
            $committed = $installedVersion -eq [string]$state.version
        }
        if (-not $committed) {
            if (Test-Path -LiteralPath $InstallDirectory) {
                Assert-ExactOwnedPath -Path $InstallDirectory -Expected $InstallDirectory
                Remove-Item -LiteralPath $InstallDirectory -Recurse -Force
            }
            Assert-TransactionPath -Path $backups[0].FullName
            Move-Item -LiteralPath $backups[0].FullName -Destination $InstallDirectory
            Write-Stage "recover" "Restored the previous executable installation."
            $backups = @($backups | Select-Object -Skip 1)
        }
        foreach ($backup in $backups) {
            Assert-TransactionPath -Path $backup.FullName
            Remove-Item -LiteralPath $backup.FullName -Recurse -Force
        }
    }
    foreach ($stage in $stages) {
        Assert-TransactionPath -Path $stage.FullName
        Remove-Item -LiteralPath $stage.FullName -Recurse -Force
    }
    Write-Stage "complete" "Interrupted install transaction recovery completed."
}

function Remove-OptionalAppData {
    $targets = @()
    $localProductRoot = if ($AppDataTestRoot) {
        Join-Path ([System.IO.Path]::GetFullPath($AppDataTestRoot)) "Local\OpenAI\$productId"
    } else {
        Join-Path $localAppData "OpenAI\$productId"
    }
    $roamingProductRoot = if ($AppDataTestRoot) {
        Join-Path ([System.IO.Path]::GetFullPath($AppDataTestRoot)) "Roaming\OpenAI\$productId"
    } else {
        Join-Path ([Environment]::GetFolderPath("ApplicationData")) "OpenAI\$productId"
    }
    if ($PurgeCache) { $targets += Join-Path $localProductRoot "Cache" }
    if ($PurgeLogs) { $targets += Join-Path $localProductRoot "Logs" }
    if ($PurgeAutosaves) { $targets += Join-Path $roamingProductRoot "autosaves" }
    foreach ($target in $targets) {
        $full = [System.IO.Path]::GetFullPath($target).TrimEnd("\")
        $insideLocal = $full.StartsWith(
            "$localProductRoot\", [StringComparison]::OrdinalIgnoreCase
        )
        $insideRoaming = $full.StartsWith(
            "$roamingProductRoot\", [StringComparison]::OrdinalIgnoreCase
        )
        if (-not $insideLocal -and -not $insideRoaming) {
            throw "Refusing to remove app data outside the application data root."
        }
        if (Test-Path -LiteralPath $full) {
            Remove-Item -LiteralPath $full -Recurse -Force
            Write-Stage "remove-data" "Removed explicitly selected generated data: $full"
        }
    }
}

if ($Mode -eq "Recover") {
    Recover-InterruptedInstall
    exit 0
}

if ($Mode -eq "Uninstall") {
    $state = Read-State
    if ($null -eq $state) { throw "No matching installation state was found." }
    Write-Stage "unregister" "Restoring installer-owned registrations when unchanged."
    Restore-RegistryRecords -Records @($state.registry)
    Restore-PathEntry -Record $state.path
    foreach ($shortcut in @($state.shortcuts)) { Remove-OwnedShortcut -Record $shortcut }
    Write-Stage "remove-files" "Removing only unchanged installer-owned product files."
    Remove-OwnedFiles -Records @($state.files)
    Remove-OptionalAppData
    Remove-Item -LiteralPath $statePath -Force
    Remove-EmptyDirectories -Root $StateDirectory
    Write-Stage "complete" "Uninstall completed. Projects, source media, settings and unselected data were preserved."
    exit 0
}

if (-not $SourceDirectory) { throw "SourceDirectory is required for install and update." }
$SourceDirectory = [System.IO.Path]::GetFullPath($SourceDirectory).TrimEnd("\")
foreach ($required in @(
    "MovieMakerLauncher.exe", ".python-version", "pyproject.toml", "uv.lock",
    "scripts\environment\runtime-contract.json"
)) {
    if (-not (Test-Path -LiteralPath (Join-Path $SourceDirectory $required) -PathType Leaf)) {
        throw "The product source is incomplete; missing: $required"
    }
}
$contract = Get-Content -Raw -Encoding UTF8 (
    Join-Path $SourceDirectory "scripts\environment\runtime-contract.json"
) | ConvertFrom-Json
$bundledUv = Join-Path $SourceDirectory ([string]$contract.uv.bundledCandidate)
if (-not (Test-Path -LiteralPath $bundledUv -PathType Leaf)) {
    throw "The product source is incomplete; bundled uv.exe is missing."
}
$null = Test-PayloadIntegrity -Root $SourceDirectory
$existingState = Read-State
if ($Mode -eq "Install" -and $null -ne $existingState) {
    throw "This product is already installed. Use Update instead."
}
if ($Mode -eq "Update" -and $null -eq $existingState) {
    throw "No existing installation was found. Use Install instead."
}
if ($Mode -eq "Update" -and ([version]$contract.version) -lt ([version]$existingState.version)) {
    throw "Downgrade refused because older application/project schemas may be incompatible."
}

$parentDirectory = Split-Path -Parent $InstallDirectory
New-Item -ItemType Directory -Force -Path $parentDirectory | Out-Null
$transactionId = [guid]::NewGuid().ToString("N")
$stageDirectory = "$InstallDirectory.new-$transactionId"
$backupDirectory = "$InstallDirectory.old-$transactionId"
$newState = $null
$movedOld = $false
$registryRecords = [System.Collections.Generic.List[object]]::new()
$shortcutRecords = [System.Collections.Generic.List[object]]::new()
$pathRecord = $null
$previousRegistryRecords = if ($null -ne $existingState) { @($existingState.registry) } else { @() }
$previousShortcutRecords = if ($null -ne $existingState) { @($existingState.shortcuts) } else { @() }
$programId = "$productId.Project"
$registryTargets = @(
    [pscustomobject]@{ key = "$classesRoot\.mmrproj"; name = "" },
    [pscustomobject]@{ key = "$classesRoot\$programId"; name = "" },
    [pscustomobject]@{ key = "$classesRoot\$programId\DefaultIcon"; name = "" },
    [pscustomobject]@{ key = "$classesRoot\$programId\shell\open\command"; name = "" }
)
$rollbackRegistrySnapshots = @(
    $registryTargets | ForEach-Object { Get-RegistrySnapshot -Key $_.key -Name $_.name }
)
$rollbackShortcutSnapshots = @(
    Get-ShortcutSnapshot -Path $startMenuPath
    Get-ShortcutSnapshot -Path $desktopPath
)
$rollbackUserPath = Get-UserPath
try {
    Write-Stage "stage" "Copying the verified product payload into a private staging directory."
    New-Item -ItemType Directory -Force -Path $stageDirectory | Out-Null
    Get-ChildItem -LiteralPath $SourceDirectory -Force | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination $stageDirectory -Recurse -Force
    }
    $stagedLauncher = Join-Path $stageDirectory "MovieMakerLauncher.exe"
    if (-not (Test-Path -LiteralPath $stagedLauncher -PathType Leaf)) {
        throw "The staged launcher is missing."
    }
    if (Test-Path -LiteralPath $InstallDirectory) {
        Assert-ExactOwnedPath -Path $InstallDirectory -Expected $InstallDirectory
        Move-Item -LiteralPath $InstallDirectory -Destination $backupDirectory
        $movedOld = $true
    }
    Move-Item -LiteralPath $stageDirectory -Destination $InstallDirectory
    if ($movedOld -and $null -ne $existingState) {
        Preserve-NonOwnedUpdateFiles -OldRoot $backupDirectory -OldRecords @($existingState.files) `
            -NewRoot $InstallDirectory -TransactionId $transactionId
    }
    if ($FailAfterSwap) {
        throw "Injected post-swap failure for transactional integration testing."
    }
    if ($ExitAfterSwap) {
        [Environment]::Exit(91)
    }

    if ($FileAssociation) {
        Write-Stage "register" "Applying the optional .mmrproj file association."
        Register-Value $registryRecords "$classesRoot\.mmrproj" "" $programId `
            -ExistingRecords $previousRegistryRecords
        Register-Value $registryRecords "$classesRoot\$programId" "" "$displayName Project" `
            -ExistingRecords $previousRegistryRecords
        Register-Value $registryRecords "$classesRoot\$programId\DefaultIcon" "" `
            "`"$launcherPath`",0" -ExistingRecords $previousRegistryRecords
        Register-Value $registryRecords "$classesRoot\$programId\shell\open\command" "" `
            "`"$launcherPath`" -- --project `"%1`"" -ExistingRecords $previousRegistryRecords
    }
    elseif ($null -ne $existingState -and [bool]$existingState.options.fileAssociation) {
        Restore-RegistryRecords -Records $previousRegistryRecords
    }
    if ($StartMenuShortcut) {
        Write-Stage "shortcut" "Applying the Start menu shortcut option."
        if (Test-ShortcutCanBeReplaced -Path $startMenuPath `
                -ExistingRecords $previousShortcutRecords) {
            $shortcutRecords.Add((New-OwnedShortcut -Path $startMenuPath))
        }
        else { Write-Stage "preserve" "An existing Start menu shortcut was not overwritten." }
    }
    elseif ($null -ne $existingState) {
        foreach ($shortcut in $previousShortcutRecords | Where-Object path -eq $startMenuPath) {
            Remove-OwnedShortcut -Record $shortcut
        }
    }
    if ($DesktopShortcut) {
        Write-Stage "shortcut" "Applying the desktop shortcut option."
        if (Test-ShortcutCanBeReplaced -Path $desktopPath `
                -ExistingRecords $previousShortcutRecords) {
            $shortcutRecords.Add((New-OwnedShortcut -Path $desktopPath))
        }
        else { Write-Stage "preserve" "An existing desktop shortcut was not overwritten." }
    }
    elseif ($null -ne $existingState) {
        foreach ($shortcut in $previousShortcutRecords | Where-Object path -eq $desktopPath) {
            Remove-OwnedShortcut -Record $shortcut
        }
    }
    Write-Stage "path" "Applying the optional launcher-directory PATH setting."
    if ($AddToPath -and $null -ne $existingState -and [bool]$existingState.path.changed) {
        $pathRecord = $existingState.path
    }
    elseif ($AddToPath) {
        $pathRecord = @(Add-PathEntry) | Select-Object -Last 1
    }
    elseif ($null -ne $existingState -and [bool]$existingState.path.changed) {
        Restore-PathEntry -Record $existingState.path
        $currentPath = Get-UserPath
        $pathRecord = [pscustomobject][ordered]@{
            changed = $false; previous = $currentPath; installed = $currentPath
        }
    }
    else {
        $currentPath = Get-UserPath
        $pathRecord = [pscustomobject][ordered]@{
            changed = $false; previous = $currentPath; installed = $currentPath
        }
    }
    if ($FailAfterRegistrations) {
        throw "Injected registration failure for transactional integration testing."
    }
    Write-Stage "inventory" "Hashing installed product files for ownership-safe removal."
    $fileInventory = @(Get-FileRecords -Root $SourceDirectory)
    $newState = [pscustomobject][ordered]@{
        schemaVersion = 1
        productId = $productId
        displayName = $displayName
        version = [string]$contract.version
        installDirectory = $InstallDirectory
        files = $fileInventory
        shortcuts = @($shortcutRecords.ToArray())
        registry = @($registryRecords.ToArray())
        path = $pathRecord
        options = [pscustomobject][ordered]@{
            startMenuShortcut = [bool]$StartMenuShortcut
            desktopShortcut = [bool]$DesktopShortcut
            fileAssociation = [bool]$FileAssociation
            addToPath = [bool]$AddToPath
        }
        signingStatus = if (Test-Path -LiteralPath (Join-Path $SourceDirectory "SIGNING-STATUS.txt")) {
            (Get-Content -Raw (Join-Path $SourceDirectory "SIGNING-STATUS.txt")).Trim()
        } else { "unsigned" }
    }
    Write-Stage "state" "Writing installer ownership and previous-value state atomically."
    Write-State -State $newState
    if ($movedOld) {
        Remove-Item -LiteralPath $backupDirectory -Recurse -Force
        $movedOld = $false
    }
    if ($AddToPath) {
        Write-Stage "restart" "No Windows restart is required; reopen existing terminals to see PATH changes."
    }
    Write-Stage "complete" "$Mode completed for version $($contract.version)."
}
catch {
    $failure = $_
    Write-Stage "rollback" "$Mode failed; restoring the previous executable installation."
    Restore-RegistrySnapshots -Snapshots $rollbackRegistrySnapshots
    Set-UserPath $rollbackUserPath
    Restore-ShortcutSnapshots -Snapshots $rollbackShortcutSnapshots
    if (Test-Path -LiteralPath $InstallDirectory) {
        Assert-ExactOwnedPath -Path $InstallDirectory -Expected $InstallDirectory
        Remove-Item -LiteralPath $InstallDirectory -Recurse -Force
    }
    if ($movedOld -and (Test-Path -LiteralPath $backupDirectory)) {
        Move-Item -LiteralPath $backupDirectory -Destination $InstallDirectory
    }
    if (Test-Path -LiteralPath $stageDirectory) {
        Remove-Item -LiteralPath $stageDirectory -Recurse -Force
    }
    if ($null -ne $existingState) {
        Write-State -State $existingState
    }
    elseif (Test-Path -LiteralPath $statePath -PathType Leaf) {
        Remove-Item -LiteralPath $statePath -Force
    }
    $guidance = Get-FailureGuidance -Record $failure
    throw "$guidance Details: $($failure.Exception.Message)"
}
