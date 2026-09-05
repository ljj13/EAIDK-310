#Requires -Version 5.1
Set-StrictMode -Version Latest

$EaidkUbootOffset = 8388608
$EaidkUbootLength = 4194304
$EaidkPrefixLength = 16777216

function Assert-EaidkTargetDisk {
    param(
        [Parameter(Mandatory = $true)]$Disk,
        [Parameter(Mandatory = $true)][string]$ExpectedSerial,
        [Parameter(Mandatory = $true)][long]$ExpectedSizeBytes,
        [string[]]$MountedDriveLetters = @()
    )

    if ($null -eq $Disk) {
        throw 'disk object is null'
    }
    if ([string]$Disk.SerialNumber -ne $ExpectedSerial) {
        throw "disk serial mismatch: expected $ExpectedSerial, got $($Disk.SerialNumber)"
    }
    if ([long]$Disk.Size -ne $ExpectedSizeBytes) {
        throw "disk size mismatch: expected $ExpectedSizeBytes, got $($Disk.Size)"
    }
    if ([string]$Disk.BusType -ne 'USB') {
        throw "disk is not on the USB bus: $($Disk.BusType)"
    }
    if ($Disk.IsSystem -or $Disk.IsBoot) {
        throw 'disk is a system or boot disk'
    }
    if ($MountedDriveLetters.Count -gt 0) {
        throw "disk has mounted partitions: $($MountedDriveLetters -join ', ')"
    }

    return [pscustomobject]@{
        DiskNumber = [int]$Disk.Number
        DevicePath = [string]$Disk.Path
        Offset = $EaidkUbootOffset
        Length = $EaidkUbootLength
        SerialNumber = [string]$Disk.SerialNumber
        Size = [long]$Disk.Size
        BusType = [string]$Disk.BusType
    }
}

function Get-StreamRangeSha256 {
    param(
        [Parameter(Mandatory = $true)][System.IO.Stream]$Stream,
        [long]$Offset,
        [long]$Length
    )

    $original = $Stream.Position
    try {
        $Stream.Position = $Offset
        $buffer = New-Object byte[] $Length
        $read = 0
        while ($read -lt $Length) {
            $remaining = [int]($Length - $read)
            $count = $Stream.Read($buffer, $read, $remaining)
            if ($count -le 0) {
                throw "unexpected end of stream while reading $Length bytes at offset $Offset"
            }
            $read += $count
        }
        $sha = [System.Security.Cryptography.SHA256]::Create()
        try {
            return ([BitConverter]::ToString($sha.ComputeHash($buffer)) -replace '-', '').ToUpperInvariant()
        }
        finally {
            $sha.Dispose()
        }
    }
    finally {
        $Stream.Position = $original
    }
}

function Write-VerifiedStreamRange {
    param(
        [Parameter(Mandatory = $true)][System.IO.Stream]$SourceStream,
        [Parameter(Mandatory = $true)][System.IO.Stream]$TargetStream,
        [long]$Offset,
        [long]$Length
    )

    if ($SourceStream.Length -lt $Length) {
        throw "source stream is shorter than $Length bytes"
    }

    $SourceStream.Position = 0
    $TargetStream.Position = $Offset
    $buffer = New-Object byte[] (1MB)
    $remaining = $Length
    while ($remaining -gt 0) {
        $chunk = [int][Math]::Min($buffer.Length, $remaining)
        $read = $SourceStream.Read($buffer, 0, $chunk)
        if ($read -le 0) {
            throw 'unexpected end of source stream'
        }
        $TargetStream.Write($buffer, 0, $read)
        $remaining -= $read
    }
    $TargetStream.Flush()
    return Get-StreamRangeSha256 -Stream $TargetStream -Offset $Offset -Length $Length
}

function Assert-UbootRegionManifest {
    param([Parameter(Mandatory = $true)][string]$ManifestPath)

    $manifest = Get-Content -Raw -LiteralPath $ManifestPath | ConvertFrom-Json
    if ($manifest.writable -ne $true) {
        throw 'manifest is not writable'
    }
    $regionSha = [string]$manifest.region_sha256
    if ([string]::IsNullOrWhiteSpace($regionSha)) {
        throw 'manifest is missing region_sha256'
    }
    if ($regionSha -notmatch '^[0-9A-Fa-f]{64}$') {
        throw 'manifest region_sha256 is not a 64-character SHA-256'
    }
    foreach ($range in @($manifest.changed_ranges)) {
        $start = [int]$range[0]
        $end = [int]$range[1]
        if ($start -lt $EaidkUbootOffset -or $end -gt ($EaidkUbootOffset + $EaidkUbootLength)) {
            throw "manifest changed range lies outside the U-Boot proper region: [$start,$end)"
        }
    }
    return $manifest
}
