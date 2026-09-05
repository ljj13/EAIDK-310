#Requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Port,
    [Parameter(Mandatory = $true)][string]$User,
    [Parameter(Mandatory = $true)][string]$Password,
    [Parameter(Mandatory = $true)][string[]]$Command,
    [Parameter(Mandatory = $true)][string]$OutputPath,
    [int]$BaudRate = 1500000,
    [int]$PromptWaitSeconds = 3,
    [int]$CommandWaitSeconds = 2,
    [int]$DrainSeconds = 3
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$serial = New-Object System.IO.Ports.SerialPort(
    $Port, $BaudRate, [System.IO.Ports.Parity]::None, 8, [System.IO.Ports.StopBits]::One
)
$serial.Handshake = [System.IO.Ports.Handshake]::None
$serial.ReadTimeout = 500
$builder = New-Object System.Text.StringBuilder

function Read-Available {
    $available = $serial.BytesToRead
    if ($available -gt 0) {
        $buffer = New-Object byte[] $available
        $read = $serial.Read($buffer, 0, $available)
        $text = [System.Text.Encoding]::ASCII.GetString($buffer, 0, $read)
        [void]$builder.Append($text)
        [Console]::Write($text)
    }
}

function Wait-Read {
    param([int]$Seconds)
    $deadline = [DateTime]::UtcNow.AddSeconds($Seconds)
    while ([DateTime]::UtcNow -lt $deadline) {
        Read-Available
        Start-Sleep -Milliseconds 100
    }
}

function Send-Line {
    param([string]$Line)
    $serial.Write($Line + "`r")
    $serial.BaseStream.Flush()
}

try {
    $serial.Open()
    Send-Line ''
    Wait-Read 1
    Send-Line $User
    Wait-Read $PromptWaitSeconds
    Send-Line $Password
    Wait-Read $PromptWaitSeconds

    foreach ($cmd in $Command) {
        Send-Line $cmd
        Wait-Read $CommandWaitSeconds
    }

    Send-Line 'exit'
    Wait-Read $DrainSeconds
}
finally {
    if ($serial.IsOpen) {
        $serial.Close()
    }
    $serial.Dispose()
}

$outputDir = Split-Path -Parent $OutputPath
if ($outputDir) {
    New-Item -ItemType Directory -Force -Path $outputDir | Out-Null
}
[System.IO.File]::WriteAllText(
    $OutputPath, $builder.ToString(), (New-Object System.Text.UTF8Encoding($false))
)
