param(
    [string]$Port = 'COM22',
    [int]$Baud = 1500000
)

$ErrorActionPreference = 'Stop'
$serial = [System.IO.Ports.SerialPort]::new(
    $Port,
    $Baud,
    [System.IO.Ports.Parity]::None,
    8,
    [System.IO.Ports.StopBits]::One
)
$serial.Handshake = [System.IO.Ports.Handshake]::None
$serial.DtrEnable = $false
$serial.RtsEnable = $false
$serial.Encoding = [System.Text.Encoding]::UTF8

$previousControlCMode = [Console]::TreatControlCAsInput

try {
    $serial.Open()
    [Console]::TreatControlCAsInput = $true
    [Console]::WriteLine("EAIDK-310 serial console: $Port @ $Baud 8N1")
    [Console]::WriteLine('Input is echoed by the board. Exit: Control, Q')
    [Console]::WriteLine('--------------------------------------------------')

    while ($true) {
        if ($serial.BytesToRead -gt 0) {
            [Console]::Write($serial.ReadExisting())
        }

        while ([Console]::KeyAvailable) {
            $key = [Console]::ReadKey($true)
            $controlPressed =
                ($key.Modifiers -band [ConsoleModifiers]::Control) -ne 0

            if ($controlPressed -and $key.Key -eq [ConsoleKey]::Q) {
                return
            }

            if ($key.Key -eq [ConsoleKey]::Enter) {
                $serial.Write([char]10)
            }
            elseif ($key.KeyChar -ne [char]0) {
                $serial.Write($key.KeyChar.ToString())
            }
        }

        Start-Sleep -Milliseconds 10
    }
}
finally {
    [Console]::TreatControlCAsInput = $previousControlCMode
    if ($serial.IsOpen) {
        $serial.Close()
    }
    $serial.Dispose()
}
