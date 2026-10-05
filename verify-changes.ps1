param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$CommandArgs
)

$ErrorActionPreference = "Stop"
$PythonCommand = Get-Command "python3" -ErrorAction SilentlyContinue
if (-not $PythonCommand) {
    $PythonCommand = Get-Command "python" -ErrorAction SilentlyContinue
}
if (-not $PythonCommand) {
    throw "Python 3 is required to run changed-service verification."
}

& $PythonCommand.Source `
    (Join-Path $PSScriptRoot "scripts/verify_changes.py") `
    @CommandArgs
exit $LASTEXITCODE
