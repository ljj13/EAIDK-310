#Requires -Version 5.1
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$libraryPath = Join-Path $PSScriptRoot 'eaidk-uboot-write-lib.ps1'
. $libraryPath

function Assert-True {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) { throw $Message }
}

function Assert-Throws {
    param([scriptblock]$Action, [string]$Pattern, [string]$Message)
    $thrown = $false
    try {
        & $Action
    }
    catch {
        $thrown = $true
        if ($Pattern -and $_.Exception.Message -notmatch $Pattern) {
            throw "$Message (unexpected message: $($_.Exception.Message))"
        }
    }
    if (-not $thrown) {
        throw "$Message (no exception was thrown)"
    }
}

function New-FakeDisk {
    param(
        [string]$Serial = 'EAIDKTF123',
        [long]$Size = 124939927552,
        [string]$Bus = 'USB',
        [bool]$System = $false,
        [bool]$Boot = $false,
        [int]$Number = 3
    )
    return [pscustomobject]@{
        Number = $Number
        SerialNumber = $Serial
        Size = $Size
        BusType = $Bus
        IsSystem = $System
        IsBoot = $Boot
        Path = "\\.\PhysicalDrive$Number"
    }
}

function Assert-RangeEqual {
    param(
        [byte[]]$Left, [int]$LeftStart,
        [byte[]]$Right, [int]$RightStart,
        [int]$Length, [string]$Message
    )
    $leftSlice = New-Object byte[] $Length
    $rightSlice = New-Object byte[] $Length
    [Array]::Copy($Left, $LeftStart, $leftSlice, 0, $Length)
    [Array]::Copy($Right, $RightStart, $rightSlice, 0, $Length)
    if ([Convert]::ToBase64String($leftSlice) -ne [Convert]::ToBase64String($rightSlice)) {
        throw $Message
    }
}

$tmp = Join-Path ([System.IO.Path]::GetTempPath()) ('eaidk310-writer-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $tmp | Out-Null
$checks = 0

try {
    $plan = Assert-EaidkTargetDisk -Disk (New-FakeDisk) -ExpectedSerial 'EAIDKTF123' -ExpectedSizeBytes 124939927552
    Assert-True ($plan.Offset -eq 8388608) 'valid disk plan has wrong offset'
    Assert-True ($plan.Length -eq 4194304) 'valid disk plan has wrong length'
    $checks++

    Assert-Throws { Assert-EaidkTargetDisk -Disk (New-FakeDisk -Serial 'WRONG') -ExpectedSerial 'EAIDKTF123' -ExpectedSizeBytes 124939927552 } 'serial mismatch' 'wrong serial was not rejected'
    $checks++
    Assert-Throws { Assert-EaidkTargetDisk -Disk (New-FakeDisk -Size 123) -ExpectedSerial 'EAIDKTF123' -ExpectedSizeBytes 124939927552 } 'size mismatch' 'wrong size was not rejected'
    $checks++
    Assert-Throws { Assert-EaidkTargetDisk -Disk (New-FakeDisk -Bus 'SATA') -ExpectedSerial 'EAIDKTF123' -ExpectedSizeBytes 124939927552 } 'USB bus' 'non-USB disk was not rejected'
    $checks++
    Assert-Throws { Assert-EaidkTargetDisk -Disk (New-FakeDisk -System $true) -ExpectedSerial 'EAIDKTF123' -ExpectedSizeBytes 124939927552 } 'system or boot' 'system disk was not rejected'
    $checks++
    Assert-Throws { Assert-EaidkTargetDisk -Disk (New-FakeDisk) -ExpectedSerial 'EAIDKTF123' -ExpectedSizeBytes 124939927552 -MountedDriveLetters @('E:') } 'mounted' 'mounted disk was not rejected'
    $checks++

    $knownStream = New-Object System.IO.MemoryStream
    $knownBytes = [System.Text.Encoding]::ASCII.GetBytes('abc')
    $knownStream.Write($knownBytes, 0, $knownBytes.Length)
    $knownHash = Get-StreamRangeSha256 -Stream $knownStream -Offset 0 -Length 3
    Assert-True ($knownHash -eq 'BA7816BF8F01CFEA414140DE5DAE2223B00361A396177A9CB410FF61F20015AD') "known SHA-256 vector mismatch: $knownHash"
    $knownStream.Dispose()
    $checks++

    $goodManifest = Join-Path $tmp 'good-manifest.json'
    @'
{"writable":true,"region_sha256":"ABCDEF0123456789ABCDEF0123456789ABCDEF0123456789ABCDEF0123456789","changed_ranges":[[8388608,12582912]]}
'@ | Set-Content -LiteralPath $goodManifest -Encoding ascii
    $null = Assert-UbootRegionManifest -ManifestPath $goodManifest
    $checks++

    $notWritable = Join-Path $tmp 'not-writable.json'
    @'
{"writable":false,"region_sha256":"ABCDEF0123456789ABCDEF0123456789ABCDEF0123456789ABCDEF0123456789","changed_ranges":[[8388608,12582912]]}
'@ | Set-Content -LiteralPath $notWritable -Encoding ascii
    Assert-Throws { Assert-UbootRegionManifest -ManifestPath $notWritable } 'not writable' 'non-writable manifest was not rejected'
    $checks++

    $badRange = Join-Path $tmp 'bad-range.json'
    @'
{"writable":true,"region_sha256":"ABCDEF0123456789ABCDEF0123456789ABCDEF0123456789ABCDEF0123456789","changed_ranges":[[0,4194304]]}
'@ | Set-Content -LiteralPath $badRange -Encoding ascii
    Assert-Throws { Assert-UbootRegionManifest -ManifestPath $badRange } 'outside the U-Boot proper region' 'out-of-range manifest was not rejected'
    $checks++

    # Keep this unit test independent of the 16 MiB rescue-prefix Release asset.
    # A seeded pseudo-random fixture also makes unintended writes outside the
    # 8-12 MiB U-Boot proper window observable.
    $before = New-Object byte[] 16777216
    ([System.Random]::new(310)).NextBytes($before)

    $regionBytes = New-Object byte[] 4194304
    for ($i = 0; $i -lt $regionBytes.Length; $i += 4096) {
        $regionBytes[$i] = 0xA5
    }
    $regionFile = Join-Path $tmp 'region.bin'
    [System.IO.File]::WriteAllBytes($regionFile, $regionBytes)
    $regionFileHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $regionFile).Hash

    $targetFile = Join-Path $tmp 'target.bin'
    [System.IO.File]::WriteAllBytes($targetFile, $before)

    $sourceStream = [System.IO.File]::Open($regionFile, 'Open', 'Read', 'Read')
    $targetStream = [System.IO.File]::Open($targetFile, 'Open', 'ReadWrite', 'None')
    try {
        $writtenHash = Write-VerifiedStreamRange -SourceStream $sourceStream -TargetStream $targetStream -Offset 8388608 -Length 4194304
        Assert-True ($writtenHash -eq $regionFileHash) 'stream write hash does not match source region hash'
    }
    finally {
        $sourceStream.Dispose()
        $targetStream.Dispose()
    }
    $checks++

    $after = [System.IO.File]::ReadAllBytes($targetFile)
    Assert-RangeEqual $before 0 $after 0 8388608 'outer 0-8 MiB changed'
    Assert-RangeEqual $before 12582912 $after 12582912 4194304 'outer 12-16 MiB changed'
    $checks++

    $readBack = New-Object byte[] 4194304
    [Array]::Copy($after, 8388608, $readBack, 0, 4194304)
    $readBackFile = Join-Path $tmp 'readback.bin'
    [System.IO.File]::WriteAllBytes($readBackFile, $readBack)
    Assert-True ((Get-FileHash -Algorithm SHA256 -LiteralPath $readBackFile).Hash -eq $regionFileHash) 'read-back 8-12 MiB hash does not match source region'
    $checks++

    Write-Output "All U-Boot proper writer safety tests passed. ($checks checks)"
}
finally {
    Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
}
