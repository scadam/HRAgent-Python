param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if (-not $env:FUNCTION_APP_NAME) {
    throw 'FUNCTION_APP_NAME is not set. Re-run provision to refresh environment outputs.'
}

$funcDir = $env:FUNC_PATH
if ([string]::IsNullOrWhiteSpace($funcDir)) {
    $funcDir = Join-Path (Get-Location) 'devTools/func'
}

$funcExeCandidate = Join-Path $funcDir 'func.exe'
if (Test-Path -LiteralPath $funcExeCandidate) {
    $funcExe = $funcExeCandidate
} elseif (Test-Path -LiteralPath $funcDir) {
    $funcExe = $funcDir
} else {
    throw "Azure Functions Core Tools executable not found at $funcExeCandidate"
}

$arguments = @(
    'azure', 'functionapp', 'publish', $env:FUNCTION_APP_NAME,
    '--python', '--build', 'remote',
    '--subscription', $env:AZURE_SUBSCRIPTION_ID,
    '--resource-group', $env:AZURE_RESOURCE_GROUP_NAME,
    '-y'
)

Write-Host "Publishing $($env:FUNCTION_APP_NAME) using Azure Functions Core Tools ($funcExe)..."
& $funcExe @arguments
if ($LASTEXITCODE -ne 0) {
    throw "Azure Functions Core Tools publish failed with exit code $LASTEXITCODE"
}
