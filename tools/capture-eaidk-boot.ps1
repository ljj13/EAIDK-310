#Requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Port,
    [int]$BaudRate = 1500000,
    [string]$OutputPath,
    [string]$LogDir,
    [string]$SendHex,
    [int]$TimeoutSeconds = 0,
    [switch]$Interactive,
    [switch]$AutoStopAutoboot
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
if (-not $LogDir) {
    $LogDir = Join-Path $projectRoot 'logs\wireless'
}
New-Item -ItemType Directory -Path $LogDir -Force | Out-Null

if (-not $OutputPath) {
    $OutputPath = Join-Path $LogDir ((Get-Date -Format 'yyyyMMdd-HHmmss') + '-serial.log')
}
$outputPathFull = [System.IO.Path]::GetFullPath($OutputPath)

$serial = New-Object System.IO.Ports.SerialPort(
    $Port, $BaudRate, [System.IO.Ports.Parity]::None, 8, [System.IO.Ports.StopBits]::One
)
$serial.Handshake = [System.IO.Ports.Handshake]::None
$serial.ReadTimeout = 500

$writer = New-Object System.IO.StreamWriter(
    $outputPathFull, $false, (New-Object System.Text.UTF8Encoding($false))
)
$writer.AutoFlush = $true
$previousControlCMode = [Console]::TreatControlCAsInput

try {
    $serial.Open()

    if ($Interactive) {
        [Console]::TreatControlCAsInput = $true
        [Console]::WriteLine('Interactive serial capture enabled. Exit: Control+Q')
    }

    if ($PSBoundParameters.ContainsKey('SendHex') -and $SendHex) {
        $hex = ($SendHex -replace '[^0-9A-Fa-f]', '')
        if (($hex.Length % 2) -ne 0) {
            $hex = '0' + $hex
        }
        for ($i = 0; $i -lt $hex.Length; $i += 2) {
            $byte = [Convert]::ToByte($hex.Substring($i, 2), 16)
            $serial.BaseStream.WriteByte($byte)
        }
        $serial.BaseStream.Flush()
    }

    $deadline = if ($TimeoutSeconds -gt 0) { [DateTime]::UtcNow.AddSeconds($TimeoutSeconds) } else { [DateTime]::MaxValue }
    $stopRequested = $false
    $autobootStopSent = $false
    $receivedTail = ''

    while ([DateTime]::UtcNow -lt $deadline -and -not $stopRequested) {
        $available = $serial.BytesToRead
        if ($available -gt 0) {
            $buffer = New-Object byte[] $available
            $read = $serial.Read($buffer, 0, $available)
            $text = [System.Text.Encoding]::ASCII.GetString($buffer, 0, $read)
            [Console]::Write($text)
            $writer.Write($text)

            if ($AutoStopAutoboot -and -not $autobootStopSent) {
                $receivedTail += $text
                if ($receivedTail -match 'Hit any key to stop autoboot') {
                    $serial.Write(' ')
                    $serial.BaseStream.Flush()
                    $autobootStopSent = $true
                    $marker = "`r`nAUTOSTOP: sent U-Boot break key`r`n"
                    [Console]::Write($marker)
                    $writer.Write($marker)
                }
                elseif ($receivedTail.Length -gt 256) {
                    $receivedTail = $receivedTail.Substring($receivedTail.Length - 256)
                }
            }
        }
        else {
            Start-Sleep -Milliseconds 100
        }

        if ($Interactive) {
            while ([Console]::KeyAvailable) {
                $key = [Console]::ReadKey($true)
                $controlPressed =
                    ($key.Modifiers -band [ConsoleModifiers]::Control) -ne 0
                if ($controlPressed -and $key.Key -eq [ConsoleKey]::Q) {
                    $stopRequested = $true
                    break
                }

                switch ($key.Key) {
                    ([ConsoleKey]::Enter) { $serial.Write("`r"); break }
                    ([ConsoleKey]::Backspace) { $serial.Write([char]8); break }
                    ([ConsoleKey]::UpArrow) { $serial.Write("`e[A"); break }
                    ([ConsoleKey]::DownArrow) { $serial.Write("`e[B"); break }
                    ([ConsoleKey]::RightArrow) { $serial.Write("`e[C"); break }
                    ([ConsoleKey]::LeftArrow) { $serial.Write("`e[D"); break }
                    default {
                        if ($key.KeyChar -ne [char]0) {
                            $serial.Write($key.KeyChar.ToString())
                        }
                    }
                }
                $serial.BaseStream.Flush()
            }
        }
    }
}
finally {
    [Console]::TreatControlCAsInput = $previousControlCMode
    if ($serial.IsOpen) {
        $serial.Close()
    }
    $serial.Dispose()
    $writer.Dispose()
}
