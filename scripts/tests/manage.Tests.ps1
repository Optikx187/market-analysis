$ErrorActionPreference = "Stop"
$SourceRoot = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path
$TempRoot = Join-Path ([IO.Path]::GetTempPath()) "manage-ps-$([Guid]::NewGuid())"
$BinDir = Join-Path $TempRoot "bin"
$CommandLog = Join-Path $TempRoot "commands.log"
$Commands = @(
    "install", "start", "stop", "restart", "status", "logs",
    "backup", "verify-backup", "restore", "upgrade", "verify"
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
    New-Item -ItemType Directory -Path (Join-Path $TempRoot "scripts") | Out-Null
    Copy-Item `
        (Join-Path $SourceRoot "scripts/backup_manifest.py") `
        (Join-Path $TempRoot "scripts/backup_manifest.py")

    @'
#!/bin/sh
printf '%s\n' "$*" >> "$MOCK_LOG"
if [ "$1" = "compose" ]; then
  printf 'compose-project=%s\n' "${COMPOSE_PROJECT_NAME:-default}" >> "$MOCK_LOG"
fi
if [ "$1" = "compose" ] && [ "$2" = "ps" ] && [ "$3" = "--services" ]; then
  printf 'data-ingestion\nportfolio-engine\n'
fi
if [ "$1" = "compose" ] && [ "$2" = "ps" ] && [ "$3" = "-aq" ]; then
  printf '%s-container\n' "$4"
fi
if [ "$1" = "inspect" ]; then
  printf '%s\n' "${2%-container}-volume"
fi
if [ "$1" = "run" ] && [ "${MOCK_ARCHIVE_FAIL:-0}" = "1" ] &&
   printf '%s\n' "$*" | grep -q 'quant-engine-db.tgz'; then
  exit 1
fi
if [ "$1" = "run" ] && printf '%s\n' "$*" | grep -q '/tool.py create'; then
  printf '%s\n' '{"format_version":1,"volumes":[]}'
  exit 0
fi
if [ "$1" = "run" ] && [ "$2" = "--rm" ] && [ "$3" = "python:3.12-slim" ]; then
  cat <<'EOF'
CREDENTIAL_ENCRYPTION_KEYS=mock-fernet-secret
INTERNAL_SERVICE_TOKEN=mock-internal-secret
SETTINGS_OPERATOR_TOKEN=mock-settings-secret
JWT_SECRET=mock-jwt-secret
LIVE_OPERATOR_TOKEN=mock-live-secret
EOF
fi
backup_dir=""
previous=""
for argument in "$@"; do
  if [ "$previous" = "--volume" ] && printf '%s\n' "$argument" | grep -q ':/backup$'; then
    backup_dir="${argument%:/backup}"
  fi
  previous="$argument"
done
eval "last_argument=\${$#}"
if [ -n "$backup_dir" ] && printf '%s\n' "$*" | grep -q "tarfile.open('/backup/'"; then
  : > "$backup_dir/$last_argument"
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
        "--enable-auth"
    )
    Assert-True ($install.ExitCode -eq 0) "PowerShell install failed: $($install.Output)"
    Assert-True (-not $install.Output.Contains("mock-fernet-secret")) "Secret was printed."
    $envContents = Get-Content (Join-Path $TempRoot ".env") -Raw
    Assert-True ($envContents.Contains("PUBLIC_BASE_URL=https://market.lab.example")) "Base URL was not stored."
    Assert-True ($envContents.Contains("HOST_BIND_ADDRESS=192.0.2.10")) "Bind address was not stored."
    Assert-True ($envContents.Contains("AUTH_ENABLED=true")) "Authentication was not enabled."
    Assert-True ($envContents.Contains("CREDENTIAL_ENCRYPTION_KEYS=mock-fernet-secret")) "Secret was not generated."
    $commandLogContents = Get-Content $CommandLog -Raw
    Assert-True ($commandLogContents.Contains("compose up --build -d")) "Install did not start Compose."

    $backupDir = Join-Path $TempRoot "backup"
    $backup = Invoke-Manage @("backup", $backupDir)
    Assert-True ($backup.ExitCode -eq 0) "PowerShell backup failed: $($backup.Output)"
    $commandLogContents = Get-Content $CommandLog -Raw
    Assert-True (
        $commandLogContents.Contains("compose stop data-ingestion portfolio-engine")
    ) "Backup did not stop exactly the running services."
    Assert-True (
        $commandLogContents.Contains("compose start data-ingestion portfolio-engine")
    ) "Backup did not restart exactly the running services."
    Assert-True (
        Test-Path (Join-Path $backupDir "manifest.json") -PathType Leaf
    ) "Backup manifest was not created."
    Assert-True (
        $backup.Output.Contains("Encryption keys are not included")
    ) "Backup omitted the encryption-key recovery warning."

    Set-Content -Path $CommandLog -Value "" -Encoding utf8NoBOM
    $env:MOCK_ARCHIVE_FAIL = "1"
    $failedBackup = Invoke-Manage @("backup", (Join-Path $TempRoot "failed-backup"))
    Remove-Item Env:MOCK_ARCHIVE_FAIL
    Assert-True ($failedBackup.ExitCode -ne 0) "Failed backup unexpectedly succeeded."
    $commandLogContents = Get-Content $CommandLog -Raw
    Assert-True (
        $commandLogContents.Contains("compose start data-ingestion portfolio-engine")
    ) "Failed backup did not restart the previously running services."

    $isolatedBackupDir = Join-Path $TempRoot "isolated-backup"
    New-Item -ItemType Directory -Path $isolatedBackupDir | Out-Null
    foreach ($file in @(
        "data-ingestion-db.tgz",
        "quant-engine-db.tgz",
        "portfolio-db.tgz",
        "manifest.json"
    )) {
        New-Item -ItemType File -Path (Join-Path $isolatedBackupDir $file) | Out-Null
    }
    $isolatedRestore = Invoke-Manage @(
        "restore",
        $isolatedBackupDir,
        "--project-name", "restore-check",
        "--no-start"
    )
    Assert-True (
        $isolatedRestore.ExitCode -eq 0
    ) "Isolated restore failed: $($isolatedRestore.Output)"
    Assert-True (
        $isolatedRestore.Output.Contains("isolated Compose project 'restore-check'")
    ) "Isolated restore did not identify its target project."
    $commandLogContents = Get-Content $CommandLog -Raw
    Assert-True (
        $commandLogContents.Contains("compose-project=restore-check")
    ) "Isolated restore did not use its requested Compose project."

    $legacyBackupDir = Join-Path $TempRoot "legacy-backup"
    New-Item -ItemType Directory -Path $legacyBackupDir | Out-Null
    foreach ($file in @(
        "data-ingestion-db.tgz",
        "quant-engine-db.tgz",
        "portfolio-db.tgz"
    )) {
        New-Item -ItemType File -Path (Join-Path $legacyBackupDir $file) | Out-Null
    }
    $legacyRefused = Invoke-Manage @("restore", $legacyBackupDir, "--no-start")
    Assert-True (
        $legacyRefused.ExitCode -ne 0
    ) "Restore accepted a legacy backup without explicit override."
    $legacyAllowed = Invoke-Manage @(
        "restore", $legacyBackupDir, "--no-start", "--allow-legacy"
    )
    Assert-True (
        $legacyAllowed.ExitCode -eq 0
    ) "Restore rejected the explicit legacy-backup override."
    Assert-True (
        $legacyAllowed.Output.Contains("legacy backup")
    ) "Legacy restore omitted its reconciliation warning."

    Set-Content -Path $CommandLog -Value "" -Encoding utf8NoBOM
    $env:MOCK_ARCHIVE_FAIL = "1"
    $blockedUpgrade = Invoke-Manage @("upgrade")
    Assert-True ($blockedUpgrade.ExitCode -ne 0) "Upgrade ignored a failed backup."
    $commandLogContents = Get-Content $CommandLog -Raw
    Assert-True (
        -not $commandLogContents.Contains("git pull --ff-only")
    ) "Upgrade pulled code after a failed backup."

    Set-Content -Path $CommandLog -Value "" -Encoding utf8NoBOM
    $overriddenUpgrade = Invoke-Manage @("upgrade", "--allow-backup-failure")
    Remove-Item Env:MOCK_ARCHIVE_FAIL
    Assert-True (
        $overriddenUpgrade.ExitCode -eq 0
    ) "Explicit backup-failure override did not continue the upgrade."
    $commandLogContents = Get-Content $CommandLog -Raw
    Assert-True (
        $commandLogContents.Contains("git pull --ff-only")
    ) "Explicit override did not continue to the Git update."

    Write-Output "PowerShell management tests passed"
}
finally {
    Remove-Item $TempRoot -Recurse -Force -ErrorAction SilentlyContinue
}
