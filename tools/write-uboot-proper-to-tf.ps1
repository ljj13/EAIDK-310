#Requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$RegionPath,
    [Parameter(Mandatory = $true)][string]$ManifestPath,
    [Parameter(Mandatory = $true)][int]$DiskNumber,
    [Parameter(Mandatory = $true)][string]$ExpectedSerial,
    [long]$ExpectedSizeBytes = 124939927552,
    [string]$ExpectedBaselineSha256 = '6254986C3E1E12D942D35769A8D8182422A017B6CA392237D0F284B31490A3EB',
    [switch]$Write
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot 'eaidk-uboot-write-lib.ps1')

function Get-BytesSha256 {
    param([Parameter(Mandatory = $true)][byte[]]$Bytes)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        return ([BitConverter]::ToString($sha.ComputeHash($Bytes)) -replace '-', '').ToUpperInvariant()
    }
    finally {
        $sha.Dispose()
    }
}

$regionPathFull = (Resolve-Path -LiteralPath $RegionPath).Path
$manifestPathFull = (Resolve-Path -LiteralPath $ManifestPath).Path

$regionBytes = [System.IO.File]::ReadAllBytes($regionPathFull)
if ($regionBytes.Length -ne $EaidkUbootLength) {
    throw "region file must be exactly 4 MiB: $regionPathFull"
}

$manifest = Assert-UbootRegionManifest -ManifestPath $manifestPathFull
$regionFileSha = (Get-FileHash -Algorithm SHA256 -LiteralPath $regionPathFull).Hash
if ($regionFileSha -ne ([string]$manifest.region_sha256).ToUpperInvariant()) {
    throw 'region file SHA-256 does not match manifest region_sha256'
}

$disk = Get-Disk -Number $DiskNumber -ErrorAction Stop
$mountedLetters = @(
    Get-Partition -DiskNumber $DiskNumber -ErrorAction SilentlyContinue |
        Where-Object { $_.DriveLetter } |
        ForEach-Object { "$($_.DriveLetter):" }
)
$plan = Assert-EaidkTargetDisk -Disk $disk -ExpectedSerial $ExpectedSerial -ExpectedSizeBytes $ExpectedSizeBytes -MountedDriveLetters $mountedLetters

$devicePath = [string]$disk.Path
if ([string]::IsNullOrWhiteSpace($devicePath)) {
    $devicePath = "\\.\PhysicalDrive$DiskNumber"
}

$result = [ordered]@{
    write = [bool]$Write
    disk_number = [int]$plan.DiskNumber
    device_path = $devicePath
    serial_number = [string]$plan.SerialNumber
    disk_size = [long]$plan.Size
    offset = [int]$plan.Offset
    length = [int]$plan.Length
    region_path = $regionPathFull
    region_sha256 = $regionFileSha
    manifest_path = $manifestPathFull
    variant = [string]$manifest.variant
    baseline_sha256 = ([string]$manifest.baseline_sha256).ToUpperInvariant()
}

$fileAccess = if ($Write) { [System.IO.FileAccess]::ReadWrite } else { [System.IO.FileAccess]::Read }
$deviceStream = [System.IO.File]::Open($devicePath, 'Open', $fileAccess, [System.IO.FileShare]::ReadWrite)
try {
    $currentPrefix = New-Object byte[] $EaidkPrefixLength
    $deviceStream.Position = 0
    $read = 0
    while ($read -lt $EaidkPrefixLength) {
        $count = $deviceStream.Read($currentPrefix, $read, ($EaidkPrefixLength - $read))
        if ($count -le 0) {
            throw 'unexpected end while reading current 16 MiB prefix'
        }
        $read += $count
    }

    $currentBaselineSha = Get-BytesSha256 -Bytes $currentPrefix
    if ($currentBaselineSha -ne $ExpectedBaselineSha256.ToUpperInvariant()) {
        throw "current TF prefix baseline SHA mismatch: expected $($ExpectedBaselineSha256.ToUpperInvariant()), got $currentBaselineSha"
    }

    if (-not $Write) {
        $result.baseline_verified = $true
        $result | ConvertTo-Json -Depth 4
        return
    }

    $backupDir = Join-Path (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path 'backups\eaidk310-prefix-backups'
    New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
    $backupPath = Join-Path $backupDir ((Get-Date -Format 'yyyyMMdd-HHmmss') + '-prefix-16m.img')
    [System.IO.File]::WriteAllBytes($backupPath, $currentPrefix)

    $regionStream = [System.IO.File]::Open($regionPathFull, 'Open', 'Read', [System.IO.FileShare]::Read)
    try {
        $writtenHash = Write-VerifiedStreamRange -SourceStream $regionStream -TargetStream $deviceStream -Offset $EaidkUbootOffset -Length $EaidkUbootLength
        if ($writtenHash -ne $regionFileSha) {
            throw 'written stream hash does not match region file hash'
        }
    }
    finally {
        $regionStream.Dispose()
    }

    $deviceStream.Flush()
    $readBackSha = Get-StreamRangeSha256 -Stream $deviceStream -Offset $EaidkUbootOffset -Length $EaidkUbootLength
    if ($readBackSha -ne $regionFileSha) {
        throw 'read-back region hash does not match region file hash'
    }

    $beforeOuterHead = New-Object byte[] $EaidkUbootOffset
    [Array]::Copy($currentPrefix, 0, $beforeOuterHead, 0, $EaidkUbootOffset)
    $tailLength = $EaidkPrefixLength - $EaidkUbootOffset - $EaidkUbootLength
    $beforeOuterTail = New-Object byte[] $tailLength
    [Array]::Copy($currentPrefix, ($EaidkUbootOffset + $EaidkUbootLength), $beforeOuterTail, 0, $tailLength)

    $afterOuterHeadSha = Get-StreamRangeSha256 -Stream $deviceStream -Offset 0 -Length $EaidkUbootOffset
    if ($afterOuterHeadSha -ne (Get-BytesSha256 -Bytes $beforeOuterHead)) {
        throw 'outer 0-8 MiB changed after write'
    }
    $afterOuterTailSha = Get-StreamRangeSha256 -Stream $deviceStream -Offset ($EaidkUbootOffset + $EaidkUbootLength) -Length $tailLength
    if ($afterOuterTailSha -ne (Get-BytesSha256 -Bytes $beforeOuterTail)) {
        throw 'outer 12-16 MiB changed after write'
    }

    $result.backup_path = $backupPath
    $result.outer_segments_unchanged = $true
    $result | ConvertTo-Json -Depth 4
}
finally {
    $deviceStream.Dispose()
}
