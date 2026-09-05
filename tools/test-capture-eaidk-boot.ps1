#Requires -Version 5.1
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$captureScript = Join-Path $PSScriptRoot 'capture-eaidk-boot.ps1'
$source = Get-Content -Raw -LiteralPath $captureScript

function Assert-True {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) { throw $Message }
}

$checks = 0

Assert-True ($source -match '\[Parameter\(Mandatory = \$true\)\]\[string\]\$Port') 'Port parameter is missing or not mandatory'
$checks++
Assert-True ($source -match '\[int\]\$BaudRate = 1500000') 'BaudRate default is not 1500000'
$checks++
Assert-True ($source -match '\[string\]\$OutputPath') 'OutputPath parameter is missing'
$checks++
Assert-True ($source -match '\[string\]\$SendHex') 'SendHex parameter is missing'
$checks++
Assert-True ($source -match '\[int\]\$TimeoutSeconds = 0') 'TimeoutSeconds parameter is missing or defaulted incorrectly'
$checks++

Assert-True ($source -match 'New-Object System\.IO\.Ports\.SerialPort\(') 'capture does not construct a SerialPort'
$checks++
Assert-True ($source -match '\[System\.IO\.Ports\.Parity\]::None') 'capture does not set no parity'
$checks++
Assert-True ($source -match '\[System\.IO\.Ports\.StopBits\]::One') 'capture does not set one stop bit'
$checks++
Assert-True ($source -match '\[System\.IO\.Ports\.Handshake\]::None') 'capture does not disable handshake'
$checks++

Assert-True ($source -match 'BaseStream\.WriteByte\(\$byte\)') 'SendHex path does not use BaseStream.WriteByte'
Assert-True ($source -match '\[switch\]\$Interactive') 'capture has no opt-in interactive mode'
Assert-True ($source -match '\[Console\]::KeyAvailable') 'interactive capture does not read console keys'
Assert-True ($source -match 'ConsoleKey\]::DownArrow') 'interactive capture does not translate arrow keys'
Assert-True ($source -match 'ConsoleKey\]::Q') 'interactive capture has no Control+Q exit path'
Assert-True ($source -match '\[switch\]\$AutoStopAutoboot') 'capture has no opt-in automatic U-Boot autoboot stop mode'
Assert-True ($source -match 'Hit any key to stop autoboot') 'automatic autoboot stop does not match the real U-Boot prompt'
Assert-True ($source -match '\$autobootStopSent') 'automatic autoboot stop has no one-shot guard'
Assert-True ($source -match 'AUTOSTOP: sent U-Boot break key') 'automatic autoboot stop is not visibly logged'
$checks++
Assert-True ($source -match 'if \(\$PSBoundParameters\.ContainsKey\(.SendHex.\) -and \$SendHex\)') 'SendHex is not gated behind an explicit parameter'
$checks++
Assert-True ($source -match '\[Console\]::Write\(\$text\)') 'capture does not write received bytes to stdout'
$checks++
Assert-True ($source -match '\$writer\.Write\(\$text\)') 'capture does not write received bytes to the log'
$checks++
Assert-True ($source -match 'finally\s*\{') 'capture has no finally block'
$checks++
Assert-True ($source -match '\$serial\.Close\(\)' -and $source -match '\$serial\.Dispose\(\)') 'capture does not close and dispose the serial port in finally'
$checks++

Write-Output "All serial capture contract tests passed. ($checks checks)"
