#Requires -Version 5.1
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$packageScript = Join-Path $projectRoot 'u-boot-eaidk310\scripts\package-artifacts.ps1'
$prepareCli = Join-Path $projectRoot 'tools\prepare_eaidk310_uboot.py'
$baselinePath = Join-Path $projectRoot 'eaidk-310-uboot.img'
$pwsh = (Get-Process -Id $PID).Path
$python = (Get-Command python -ErrorAction Stop).Source

function Assert-True {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) { throw $Message }
}

function Assert-BytesEqual {
    param([byte[]]$Left, [byte[]]$Right, [string]$Message)
    if ($Left.Length -ne $Right.Length) { throw "$Message (length mismatch)" }
    if ([Convert]::ToBase64String($Left) -ne [Convert]::ToBase64String($Right)) {
        throw $Message
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
    Assert-BytesEqual $leftSlice $rightSlice $Message
}

function Invoke-Package {
    param([string[]]$Arguments)
    $output = & $pwsh -NoProfile -ExecutionPolicy Bypass -File $packageScript @Arguments 2>&1
    return [pscustomobject]@{ ExitCode = $LASTEXITCODE; Output = ($output -join "`n") }
}

function Invoke-Prepare {
    param([string[]]$Arguments)
    $output = & $python $prepareCli @Arguments 2>&1
    return [pscustomobject]@{ ExitCode = $LASTEXITCODE; Output = ($output -join "`n") }
}

$image = [System.IO.File]::ReadAllBytes($baselinePath)
$slotStart = 8MB
$ubootLength = 4MB
$prefixLength = 16MB
$loadSize = [BitConverter]::ToUInt32($image, $slotStart + 20)
$payloadStart = $slotStart + 2048
$publishedPayload = New-Object byte[] $loadSize
[Array]::Copy($image, $payloadStart, $publishedPayload, 0, $loadSize)

$tmpRoot = Join-Path ([System.IO.Path]::GetTempPath()) ('eaidk310-pkg-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $tmpRoot | Out-Null
$checks = 0

try {
    $payloadFile = Join-Path $tmpRoot 'published-payload.bin'
    [System.IO.File]::WriteAllBytes($payloadFile, $publishedPayload)

    $outPublished = Join-Path $tmpRoot 'out-published'
    $r = Invoke-Package @('-ControlPayload', $payloadFile, '-HandoffPayload', $payloadFile, '-OutputRoot', $outPublished)
    Assert-True ($r.ExitCode -eq 0) "published packaging failed: $($r.Output)"
    $checks++

    foreach ($variant in @('control', 'sdio-handoff')) {
        $dir = Join-Path $outPublished $variant
        $regionFile = Join-Path $dir 'uboot.img'
        $prefixFile = Join-Path $dir 'prefix-preview.img'
        $manifestFile = Join-Path $dir 'manifest.json'
        $sumsFile = Join-Path $dir 'SHA256SUMS.txt'
        Assert-True (Test-Path $regionFile) "missing region: $variant"
        Assert-True (Test-Path $prefixFile) "missing prefix: $variant"
        Assert-True (Test-Path $manifestFile) "missing manifest: $variant"
        Assert-True (Test-Path $sumsFile) "missing sums: $variant"
        $checks++

        $regionBytes = [System.IO.File]::ReadAllBytes($regionFile)
        $prefixBytes = [System.IO.File]::ReadAllBytes($prefixFile)
        Assert-True ($regionBytes.Length -eq $ubootLength) "region is not 4 MiB: $variant"
        Assert-True ($prefixBytes.Length -eq $prefixLength) "prefix is not 16 MiB: $variant"
        $checks++

        for ($i = 1; $i -lt 4; $i++) {
            Assert-RangeEqual $regionBytes 0 $regionBytes ($i * 1MB) 1MB "slot $i differs: $variant"
        }
        $checks++

        Assert-RangeEqual $image 0 $prefixBytes 0 8MB "outer 0-8 MiB changed: $variant"
        Assert-RangeEqual $image 12MB $prefixBytes 12MB 4MB "outer 12-16 MiB changed: $variant"
        Assert-RangeEqual $regionBytes 0 $prefixBytes 8MB 4MB "inner 8-12 MiB does not match region: $variant"
        $checks++

        $manifest = Get-Content -Raw $manifestFile | ConvertFrom-Json
        Assert-True ($manifest.writable -eq $true) "manifest not writable: $variant"
        Assert-True ($manifest.variant -eq $variant) "manifest variant mismatch"
        foreach ($range in $manifest.changed_ranges) {
            $start = [int]$range[0]
            $end = [int]$range[1]
            Assert-True ($start -ge 8388608 -and $end -le 12582912) "changed range outside U-Boot proper region: $variant"
        }
        foreach ($key in @('source_commit','patch_sha256','variant','payload_size','payload_sha256','region_sha256','prefix_sha256','load_address','header_sha256','public_crc_matches','changed_ranges','baseline_sha256','generated_at_utc','writable')) {
            Assert-True ($null -ne $manifest.$key) "manifest missing key ${key}: ${variant}"
        }
        $checks++

        $sums = Get-Content $sumsFile
        Assert-True ($sums.Count -eq 2) "unexpected SHA256SUMS line count: $variant"
        Assert-True ($sums[0] -match ' uboot\.img$') "missing uboot.img sum: $variant"
        Assert-True ($sums[1] -match ' prefix-preview\.img$') "missing prefix sum: $variant"
        $checks++
    }

    $changedPayload = New-Object byte[] $publishedPayload.Length
    [Array]::Copy($publishedPayload, $changedPayload, $publishedPayload.Length)
    $changedPayload[100] = $changedPayload[100] -bxor 0x01
    $changedFile = Join-Path $tmpRoot 'changed-payload.bin'
    [System.IO.File]::WriteAllBytes($changedFile, $changedPayload)

    $outChanged = Join-Path $tmpRoot 'out-changed'
    $r = Invoke-Package @('-ControlPayload', $changedFile, '-HandoffPayload', $changedFile, '-OutputRoot', $outChanged)
    Assert-True ($r.ExitCode -eq 0) "changed packaging failed: $($r.Output)"
    $checks++

    foreach ($variant in @('control', 'sdio-handoff')) {
        $dir = Join-Path $outChanged $variant
        $manifest = Get-Content -Raw (Join-Path $dir 'manifest.json') | ConvertFrom-Json
        Assert-True ($manifest.changed_ranges.Count -gt 0) "changed payload should have changed ranges: $variant"
        foreach ($range in $manifest.changed_ranges) {
            $start = [int]$range[0]
            $end = [int]$range[1]
            Assert-True ($start -ge 8388608 -and $end -le 12582912) "changed range outside U-Boot proper region: $variant"
        }
        $checks++

        $prefixBytes = [System.IO.File]::ReadAllBytes((Join-Path $dir 'prefix-preview.img'))
        Assert-RangeEqual $image 0 $prefixBytes 0 8MB "outer 0-8 MiB changed: $variant"
        Assert-RangeEqual $image 12MB $prefixBytes 12MB 4MB "outer 12-16 MiB changed: $variant"
        $regionBytes = [System.IO.File]::ReadAllBytes((Join-Path $dir 'uboot.img'))
        Assert-RangeEqual $regionBytes 0 $prefixBytes 8MB 4MB "inner 8-12 MiB does not match region: $variant"
        $checks++
    }

    $r = Invoke-Package @('-ControlPayload', $payloadFile, '-HandoffPayload', $payloadFile, '-OutputRoot', $outPublished)
    Assert-True ($r.ExitCode -ne 0) "existing variant directory was not refused"
    Assert-True ($r.Output -match 'already exists') "refusal message missing 'already exists'"
    $checks++

    Write-Output "All packaging tests passed. ($checks checks)"
}
finally {
    Remove-Item -LiteralPath $tmpRoot -Recurse -Force -ErrorAction SilentlyContinue
}
