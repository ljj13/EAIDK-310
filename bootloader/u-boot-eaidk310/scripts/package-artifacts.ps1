#Requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$ControlPayload,
    [Parameter(Mandatory = $true)][string]$HandoffPayload,
    [Parameter(Mandatory = $true)][string]$OutputRoot,
    [string]$BaselineImage
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$toolsDir = Join-Path $projectRoot 'tools'
$prepareCli = Join-Path $toolsDir 'prepare_eaidk310_uboot.py'
$sourceLock = Join-Path $projectRoot 'u-boot-eaidk310\source-lock.json'
$patchPath = Join-Path $projectRoot 'u-boot-eaidk310\patches\0001-arm-dts-add-eaidk310-variants.patch'

if (-not $BaselineImage) {
    $BaselineImage = Join-Path $projectRoot 'eaidk-310-uboot.img'
}
$baselinePath = (Resolve-Path $BaselineImage).Path

$pythonInfo = Get-Command python -ErrorAction SilentlyContinue
$pythonCommand = if ($pythonInfo) { $pythonInfo.Source } else { 'python' }

$lock = Get-Content -Raw $sourceLock | ConvertFrom-Json
$sourceCommit = [string]$lock.commit
$patchSha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $patchPath).Hash

$ubootOffset = 8MB
$ubootLength = 4MB
$prefixLength = 16MB
$slotSize = 1MB
$maxPayloadBytes = 1046528

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

function Invoke-Prepare {
    param([string[]]$Arguments)
    $output = & $pythonCommand $prepareCli @Arguments 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "prepare CLI failed ($($Arguments -join ' ')): $($output -join ' ')"
    }
    return ($output -join "`n")
}

function Write-Manifest {
    param([string]$Path, $Manifest)
    $Manifest | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $Path -Encoding utf8
}

$controlPayloadPath = (Resolve-Path $ControlPayload).Path
$handoffPayloadPath = (Resolve-Path $HandoffPayload).Path
if (-not (Test-Path -LiteralPath $OutputRoot)) {
    New-Item -ItemType Directory -Path $OutputRoot | Out-Null
}
$outputRootPath = (Resolve-Path $OutputRoot).Path

$baselineBytes = [System.IO.File]::ReadAllBytes($baselinePath)
if ($baselineBytes.Length -ne $prefixLength) {
    throw "baseline image must be exactly 16 MiB: $baselinePath"
}

$variants = @(
    @{ Name = 'control'; Payload = $controlPayloadPath },
    @{ Name = 'sdio-handoff'; Payload = $handoffPayloadPath }
)

foreach ($variant in $variants) {
    $variantName = $variant.Name
    $payloadPath = $variant.Payload
    $variantDir = Join-Path $outputRootPath $variantName

    if (Test-Path -LiteralPath $variantDir) {
        throw "variant directory already exists: $variantDir"
    }
    New-Item -ItemType Directory -Path $variantDir | Out-Null

    $payloadBytes = [System.IO.File]::ReadAllBytes($payloadPath)
    if ($payloadBytes.Length -gt $maxPayloadBytes) {
        throw "$variantName payload exceeds $maxPayloadBytes bytes"
    }

    $region = Join-Path $variantDir 'uboot.img'
    $prefix = Join-Path $variantDir 'prefix-preview.img'
    $manifest = Join-Path $variantDir 'manifest.json'
    $sums = Join-Path $variantDir 'SHA256SUMS.txt'
    $cliManifest = Join-Path $variantDir ('.' + [guid]::NewGuid().ToString('N') + '-compose.json')

    $null = Invoke-Prepare @('pack', '--payload', $payloadPath, '--output-region', $region, '--load-address', '0x200000')
    $null = Invoke-Prepare @('compose', '--baseline', $baselinePath, '--region', $region, '--output-prefix', $prefix, '--manifest', $cliManifest)

    $cliReport = Get-Content -Raw $cliManifest | ConvertFrom-Json
    Remove-Item -LiteralPath $cliManifest -Force

    $manifestObj = [ordered]@{
        source_commit = $sourceCommit
        patch_sha256 = $patchSha256
        variant = $variantName
        payload_size = $payloadBytes.Length
        payload_sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $payloadPath).Hash
        region_sha256 = ([string]$cliReport.region_sha256).ToUpperInvariant()
        prefix_sha256 = ([string]$cliReport.prefix_sha256).ToUpperInvariant()
        load_address = [int]$cliReport.load_address
        header_sha256 = @($cliReport.header_sha256 | ForEach-Object { ([string]$_).ToUpperInvariant() })
        public_crc_matches = [bool]$cliReport.public_crc_matches
        changed_ranges = @($cliReport.changed_ranges)
        baseline_sha256 = ([string]$cliReport.baseline_sha256).ToUpperInvariant()
        generated_at_utc = [DateTime]::UtcNow.ToString('o')
        writable = $false
    }
    Write-Manifest $manifest $manifestObj

    $verifyJson = Invoke-Prepare @('verify', '--region', $region, '--require-public-crc')
    $verifyReport = $verifyJson | ConvertFrom-Json
    if ($verifyReport.public_crc_matches -ne $true) {
        throw "public Rockchip CRC check failed for $variantName"
    }

    $regionBytes = [System.IO.File]::ReadAllBytes($region)
    $prefixBytes = [System.IO.File]::ReadAllBytes($prefix)
    if ($regionBytes.Length -ne $ubootLength) {
        throw "region is not 4 MiB for $variantName"
    }
    if ($prefixBytes.Length -ne $prefixLength) {
        throw "prefix is not 16 MiB for $variantName"
    }

    for ($i = 1; $i -lt 4; $i++) {
        Assert-RangeEqual $regionBytes 0 $regionBytes ($i * $slotSize) $slotSize "slot $i differs for $variantName"
    }

    Assert-RangeEqual $baselineBytes 0 $prefixBytes 0 $ubootOffset "prefix 0-8 MiB changed for $variantName"
    Assert-RangeEqual $baselineBytes ($ubootOffset + $ubootLength) $prefixBytes ($ubootOffset + $ubootLength) ($prefixLength - $ubootOffset - $ubootLength) "prefix 12-16 MiB changed for $variantName"
    Assert-RangeEqual $regionBytes 0 $prefixBytes $ubootOffset $ubootLength "prefix 8-12 MiB does not match region for $variantName"

    $regionFileSha = (Get-FileHash -Algorithm SHA256 -LiteralPath $region).Hash
    $prefixFileSha = (Get-FileHash -Algorithm SHA256 -LiteralPath $prefix).Hash
    if ($regionFileSha -ne $manifestObj.region_sha256) {
        throw "region SHA mismatch for $variantName"
    }
    if ($prefixFileSha -ne $manifestObj.prefix_sha256) {
        throw "prefix SHA mismatch for $variantName"
    }

    foreach ($range in $manifestObj.changed_ranges) {
        $start = [int]$range[0]
        $end = [int]$range[1]
        if ($start -lt $ubootOffset -or $end -gt ($ubootOffset + $ubootLength)) {
            throw "changed range lies outside the U-Boot proper region for $variantName"
        }
    }

    $manifestObj.writable = $true
    Write-Manifest $manifest $manifestObj

    $regionSha = (Get-FileHash -Algorithm SHA256 -LiteralPath $region).Hash
    $prefixSha = (Get-FileHash -Algorithm SHA256 -LiteralPath $prefix).Hash
    "$regionSha  uboot.img" | Set-Content -LiteralPath $sums -Encoding ascii
    "$prefixSha  prefix-preview.img" | Add-Content -LiteralPath $sums -Encoding ascii
}

Write-Output "Packaged control and sdio-handoff artifacts under $outputRootPath"
