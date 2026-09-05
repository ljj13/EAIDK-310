param(
    [Parameter(Mandatory = $true)]
    [string]$SourcePath,

    [Parameter(Mandatory = $true)]
    [int]$DiskNumber,

    [Parameter(Mandatory = $true)]
    [string]$ExpectedSerial,

    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$expectedSourceSize = 16777216L
$expectedSourceSha256 = '6254986C3E1E12D942D35769A8D8182422A017B6CA392237D0F284B31490A3EB'
$expectedCardSize = 124939927552L
$devicePath = "\\.\PhysicalDrive$DiskNumber"

function Get-PrefixSha256 {
    param(
        [System.IO.Stream]$Stream,
        [long]$Length
    )

    [void]$Stream.Seek(0, [System.IO.SeekOrigin]::Begin)
    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    $buffer = New-Object byte[] 4194304
    $remaining = $Length
    try {
        while ($remaining -gt 0) {
            $wanted = [int][math]::Min($buffer.Length, $remaining)
            $read = $Stream.Read($buffer, 0, $wanted)
            if ($read -le 0) {
                throw 'Unexpected end of stream during verification.'
            }
            [void]$sha256.TransformBlock($buffer, 0, $read, $null, 0)
            $remaining -= $read
        }
        [void]$sha256.TransformFinalBlock([byte[]]::new(0), 0, 0)
        return ([BitConverter]::ToString($sha256.Hash)).Replace('-', '')
    }
    finally {
        $sha256.Dispose()
    }
}

$sourceFile = Get-Item -LiteralPath $SourcePath
if ($sourceFile.Length -ne $expectedSourceSize) {
    throw "Source size is not exactly 16 MiB: $($sourceFile.Length) bytes."
}
$sourceHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $SourcePath).Hash
if ($sourceHash -ne $expectedSourceSha256) {
    throw "Source SHA-256 does not match the verified official image."
}

$disk = Get-Disk -Number $DiskNumber
if ($disk.SerialNumber.Trim() -ne $ExpectedSerial) {
    throw "Disk serial number does not match the expected serial."
}
if ($disk.BusType -ne 'USB') {
    throw 'Refusing to write because the target is not a USB device.'
}
if ($disk.IsBoot -or $disk.IsSystem) {
    throw 'Refusing to write a boot or system disk.'
}
if ($disk.Size -ne $expectedCardSize) {
    throw "Card capacity does not match the expected 128 GB TF card: $($disk.Size) bytes."
}

$mountedPartitions = Get-Partition -DiskNumber $DiskNumber -ErrorAction SilentlyContinue |
    Where-Object { $_.DriveLetter -and $_.DriveLetter -ne [char]0 }
if ($mountedPartitions) {
    throw 'Refusing to write because the target still has a mounted drive letter.'
}

if ($DryRun) {
    [pscustomobject]@{
        mode = 'dry-run'
        sourcePath = $SourcePath
        sourceSizeBytes = $sourceFile.Length
        sourceSha256 = $sourceHash
        diskNumber = $DiskNumber
        diskSerial = $disk.SerialNumber.Trim()
        diskSizeBytes = $disk.Size
        devicePath = $devicePath
    } | ConvertTo-Json -Compress
    exit 0
}

$sourceStream = [System.IO.File]::Open(
    $SourcePath,
    [System.IO.FileMode]::Open,
    [System.IO.FileAccess]::Read,
    [System.IO.FileShare]::Read
)
$deviceStream = [System.IO.File]::Open(
    $devicePath,
    [System.IO.FileMode]::Open,
    [System.IO.FileAccess]::ReadWrite,
    [System.IO.FileShare]::ReadWrite
)

try {
    $buffer = New-Object byte[] 4194304
    $remaining = $sourceFile.Length
    [void]$deviceStream.Seek(0, [System.IO.SeekOrigin]::Begin)
    while ($remaining -gt 0) {
        $wanted = [int][math]::Min($buffer.Length, $remaining)
        $read = $sourceStream.Read($buffer, 0, $wanted)
        if ($read -le 0) {
            throw 'Unexpected end of source image while writing.'
        }
        $deviceStream.Write($buffer, 0, $read)
        $remaining -= $read
    }
    $deviceStream.Flush($true)

    $deviceHash = Get-PrefixSha256 -Stream $deviceStream -Length $sourceFile.Length
    if ($deviceHash -ne $sourceHash) {
        throw "Read-back SHA-256 mismatch after writing."
    }

    [pscustomobject]@{
        mode = 'write-and-verify'
        sourcePath = $SourcePath
        sourceSizeBytes = $sourceFile.Length
        sourceSha256 = $sourceHash
        diskNumber = $DiskNumber
        diskSerial = $disk.SerialNumber.Trim()
        devicePath = $devicePath
        readBackSha256 = $deviceHash
        verified = $true
    } | ConvertTo-Json -Compress
}
finally {
    $sourceStream.Dispose()
    $deviceStream.Dispose()
}
