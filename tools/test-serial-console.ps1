$ErrorActionPreference = 'Stop'

$scriptPath = Join-Path $PSScriptRoot 'serial-console.ps1'
if (-not (Test-Path -LiteralPath $scriptPath)) {
    throw 'serial-console.ps1 is missing'
}

$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    $scriptPath,
    [ref]$tokens,
    [ref]$errors
)

if ($errors.Count -ne 0) {
    throw "serial-console.ps1 has parser errors: $($errors.Message -join '; ')"
}

$parameterNames = @($ast.ParamBlock.Parameters.Name.VariablePath.UserPath)
foreach ($requiredName in @('Port', 'Baud')) {
    if ($parameterNames -notcontains $requiredName) {
        throw "serial-console.ps1 is missing parameter: $requiredName"
    }
}

$source = Get-Content -Raw -LiteralPath $scriptPath
foreach ($requiredPattern in @(
    'System.IO.Ports.SerialPort',
    '[Console]::KeyAvailable',
    'ReadExisting()',
    'Control, Q'
)) {
    if (-not $source.Contains($requiredPattern)) {
        throw "serial-console.ps1 is missing behavior marker: $requiredPattern"
    }
}

'PASS: serial-console.ps1 interface and syntax'
