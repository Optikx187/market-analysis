$ErrorActionPreference = "Stop"
$SourceRoot = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path
$TempRoot = Join-Path ([IO.Path]::GetTempPath()) "manage-ps-$([Guid]::NewGuid())"
$BinDir = Join-Path $TempRoot "bin"
$CommandLog = Join-Path $TempRoot "commands.log"
$Commands = @(
    "install", "start", "stop", "restart", "status", "logs",
    "backup", "restore", "upgrade", "verify"
)

function Assert-True([bool]$Condition, [string]$Message) {
    if (-not $Condition) {
        throw $Message
    }
}

function Invoke-Manage([string[]]$Arguments) {
    $output = & pwsh -NoLogo -NoProfile -File (Join-Path $TempRoot "manage.ps1") @Arguments 2>&1
    return @{
        ExitCode = $LASTEXITCODE
        Output = ($output -join "`n")
    }
}

try {
    New-Item -ItemType Directory -Path $BinDir -Force | Out-Null
    Copy-Item (Join-Path $SourceRoot "manage.ps1") $TempRoot
    Copy-Item (Join-Path $SourceRoot ".env.example") $TempRoot
    Copy-Item (Join-Path $SourceRoot "docker-compose.yml") $TempRoot

    @'
#!/bin/sh
printf '%s\n' "$*" >> "$MOCK_LOG"
if [ "$1" = "run" ] && [ "$2" = "--rm" ] && [ "$3" = "python:3.12-slim" ]; then
  cat <<'EOF'
CREDENTIAL_ENCRYPTION_KEYS=mock-fernet-secret
INTERNAL_SERVICE_TOKEN=mock-internal-secret
SETTINGS_OPERATOR_TOKEN=mock-settings-secret
JWT_SECRET=mock-jwt-secret
LIVE_OPERATOR_TOKEN=mock-live-secret
EOF
fi
exit 0
'@ | Set-Content -Path (Join-Path $BinDir "docker") -Encoding utf8NoBOM

    @'
#!/bin/sh
printf 'git %s\n' "$*" >> "$MOCK_LOG"
exit 0
'@ | Set-Content -Path (Join-Path $BinDir "git") -Encoding utf8NoBOM

    & chmod +x (Join-Path $BinDir "docker") (Join-Path $BinDir "git")
    if ($LASTEXITCODE -ne 0) { throw "Unable to create test executables." }
    $env:PATH = "$BinDir$([IO.Path]::PathSeparator)$($env:PATH)"
    $env:MOCK_LOG = $CommandLog

    foreach ($command in $Commands) {
        $result = Invoke-Manage @($command, "--help")
        Assert-True ($result.ExitCode -eq 0) "$command --help failed: $($result.Output)"
        Assert-True ($result.Output.Contains("Usage:")) "$command --help omitted usage."
    }

    $install = Invoke-Manage @(
        "install",
        "--base-url", "https://market.lab.example",
        "--bind-address", "192.0.2.10",
        "--no-start"
    )
    Assert-True ($install.ExitCode -eq 0) "PowerShell install failed: $($install.Output)"
    Assert-True (-not $install.Output.Contains("mock-fernet-secret")) "Secret was printed."
    $envContents = Get-Content (Join-Path $TempRoot ".env") -Raw
    Assert-True ($envContents.Contains("PUBLIC_BASE_URL=https://market.lab.example")) "Base URL was not stored."
    Assert-True ($envContents.Contains("HOST_BIND_ADDRESS=192.0.2.10")) "Bind address was not stored."
    Assert-True ($envContents.Contains("CREDENTIAL_ENCRYPTION_KEYS=mock-fernet-secret")) "Secret was not generated."

    Write-Output "PowerShell management tests passed"
}
finally {
    Remove-Item $TempRoot -Recurse -Force -ErrorAction SilentlyContinue
}
