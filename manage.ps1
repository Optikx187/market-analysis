[Diagnostics.CodeAnalysis.SuppressMessageAttribute(
    "PSUseShouldProcessForStateChangingFunctions",
    "",
    Justification = "Internal CLI functions execute only after explicit subcommands."
)]
param(
    [Parameter(Position = 0)]
    [string]$Command = "help",
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$CommandArgs
)

$ErrorActionPreference = "Stop"
$RootDir = $PSScriptRoot
$EnvFile = Join-Path $RootDir ".env"
$EnvExample = Join-Path $RootDir ".env.example"
$RunningOnWindows = $env:OS -eq "Windows_NT"
$Utf8NoBom = New-Object System.Text.UTF8Encoding -ArgumentList $false
$VolumeSpecs = @(
    @{ Service = "data-ingestion"; Target = "/app/data"; File = "data-ingestion-db.tgz" },
    @{ Service = "quant-engine"; Target = "/app/data"; File = "quant-engine-db.tgz" },
    @{ Service = "portfolio-engine"; Target = "/app/data"; File = "portfolio-db.tgz" }
)

Set-Location $RootDir

function Fail([string]$Message) {
    [Console]::Error.WriteLine("Error: $Message")
    exit 1
}

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)][string]$File,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )
    & $File @Arguments
    if ($LASTEXITCODE -ne 0) {
        Fail "'$File $($Arguments -join ' ')' failed with exit code $LASTEXITCODE."
    }
}

function Invoke-Compose([string[]]$Arguments) {
    Invoke-Checked -File "docker" -Arguments (@("compose") + $Arguments)
}

function Get-ComposeOutput([string[]]$Arguments) {
    $output = & docker compose @Arguments
    if ($LASTEXITCODE -ne 0) {
        Fail "'docker compose $($Arguments -join ' ')' failed with exit code $LASTEXITCODE."
    }
    return $output
}

function Show-Usage {
    @"
Market Analysis operator commands

Usage:
  .\manage.ps1 <command> [options]

Commands:
  install   Create a secure .env, validate Compose, build, and start
  start     Start the application
  stop      Stop the application without deleting persistent data
  restart   Restart the application
  status    Show service status and the configured dashboard URL
  logs      Stream or print service logs
  backup    Stop services briefly and archive all persistent volumes
  restore   Restore persistent volumes from a backup directory
  upgrade   Back up, fast-forward Git, rebuild, and verify
  verify    Validate Compose and service readiness
  help      Show this help

Run ".\manage.ps1 <command> --help" for command-specific options.
"@
}

function Show-CommandHelp([string]$Name) {
    switch ($Name) {
        "install" {
            @"
Usage: .\manage.ps1 install [--base-url URL] [--bind-address ADDRESS] [--no-start]

Creates .env from .env.example when needed, generates required secrets without
printing them, validates Docker Compose, and starts the application.
"@
        }
        "start" {
            "Usage: .\manage.ps1 start [--build]"
        }
        "stop" {
            "Usage: .\manage.ps1 stop`n`nStops containers and networks without deleting named data volumes."
        }
        "restart" {
            "Usage: .\manage.ps1 restart [--build]"
        }
        "status" {
            "Usage: .\manage.ps1 status"
        }
        "logs" {
            "Usage: .\manage.ps1 logs [SERVICE] [--follow] [--tail LINES]"
        }
        "backup" {
            "Usage: .\manage.ps1 backup [DIRECTORY]"
        }
        "restore" {
            "Usage: .\manage.ps1 restore DIRECTORY [--no-start]"
        }
        "upgrade" {
            "Usage: .\manage.ps1 upgrade"
        }
        "verify" {
            "Usage: .\manage.ps1 verify [--config-only]"
        }
        default {
            Fail "Unknown command '$Name'. Run '.\manage.ps1 help'."
        }
    }
}

function Assert-Command([string]$Name) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        Fail "'$Name' is required but was not found in PATH."
    }
}

function Invoke-Preflight {
    Assert-Command "git"
    Assert-Command "docker"
    & docker info *> $null
    if ($LASTEXITCODE -ne 0) {
        Fail "Docker is installed but the daemon is unavailable. Start Docker Desktop."
    }
    & docker compose version *> $null
    if ($LASTEXITCODE -ne 0) {
        Fail "Docker Compose v2 is required. Install a current Docker Desktop."
    }
}

function Assert-EnvFile {
    if (-not (Test-Path $EnvFile -PathType Leaf)) {
        Fail ".env is missing. Run '.\manage.ps1 install' first."
    }
}

function Get-EnvValue([string]$Key) {
    if (-not (Test-Path $EnvFile -PathType Leaf)) {
        return ""
    }
    foreach ($line in [IO.File]::ReadAllLines($EnvFile)) {
        if ($line.StartsWith("$Key=")) {
            return $line.Substring($Key.Length + 1)
        }
    }
    return ""
}

function Set-EnvValue([string]$Key, [string]$Value) {
    $lines = [Collections.Generic.List[string]]::new()
    $replaced = $false
    foreach ($line in [IO.File]::ReadAllLines($EnvFile)) {
        if ($line.StartsWith("$Key=")) {
            $lines.Add("$Key=$Value")
            $replaced = $true
        }
        else {
            $lines.Add($line)
        }
    }
    if (-not $replaced) {
        $lines.Add("$Key=$Value")
    }
    [IO.File]::WriteAllLines($EnvFile, $lines, $Utf8NoBom)
}

function Protect-EnvFile {
    if ($RunningOnWindows) {
        & icacls $EnvFile /inheritance:r /grant:r "$($env:USERNAME):(F)" *> $null
        if ($LASTEXITCODE -ne 0) {
            Fail "Unable to restrict the .env ACL to the current Windows user."
        }
    }
    else {
        Invoke-Checked -File "chmod" -Arguments @("600", $EnvFile)
    }
}

function Assert-BaseUrl([string]$Url) {
    $parsed = $null
    if (-not [Uri]::TryCreate($Url, [UriKind]::Absolute, [ref]$parsed) -or
        $parsed.Scheme -notin @("http", "https")) {
        Fail "Base URL must be an absolute http:// or https:// URL."
    }
}

function Initialize-Secret {
    $code = "import base64, os, secrets; print('CREDENTIAL_ENCRYPTION_KEYS=' + base64.urlsafe_b64encode(os.urandom(32)).decode()); print('INTERNAL_SERVICE_TOKEN=' + secrets.token_urlsafe(32)); print('SETTINGS_OPERATOR_TOKEN=' + secrets.token_urlsafe(32)); print('JWT_SECRET=' + secrets.token_hex(32)); print('LIVE_OPERATOR_TOKEN=' + secrets.token_urlsafe(32))"
    $generated = & docker run --rm python:3.12-slim python -c $code
    if ($LASTEXITCODE -ne 0) {
        Fail "Unable to generate deployment secrets with Docker."
    }
    foreach ($line in $generated) {
        $parts = $line -split "=", 2
        if ($parts.Count -eq 2 -and [string]::IsNullOrEmpty((Get-EnvValue $parts[0]))) {
            Set-EnvValue -Key $parts[0] -Value $parts[1]
        }
    }
}

function Initialize-Env([string]$BaseUrl, [string]$BindAddress) {
    if (-not (Test-Path $EnvFile -PathType Leaf)) {
        if (-not (Test-Path $EnvExample -PathType Leaf)) {
            Fail ".env.example is missing."
        }
        Copy-Item $EnvExample $EnvFile
    }
    Protect-EnvFile

    if (-not [string]::IsNullOrEmpty($BaseUrl)) {
        Assert-BaseUrl $BaseUrl
        Set-EnvValue -Key "PUBLIC_BASE_URL" -Value $BaseUrl
    }
    elseif ([string]::IsNullOrEmpty((Get-EnvValue "PUBLIC_BASE_URL"))) {
        Set-EnvValue -Key "PUBLIC_BASE_URL" -Value "http://localhost:$(Get-EnvValue 'FRONTEND_PORT')"
    }

    if (-not [string]::IsNullOrEmpty($BindAddress)) {
        Set-EnvValue -Key "HOST_BIND_ADDRESS" -Value $BindAddress
    }
    elseif ([string]::IsNullOrEmpty((Get-EnvValue "HOST_BIND_ADDRESS"))) {
        Set-EnvValue -Key "HOST_BIND_ADDRESS" -Value "0.0.0.0"
    }

    Initialize-Secret
    Protect-EnvFile
}

function Test-ComposeConfig {
    Assert-EnvFile
    & docker compose config --quiet
    if ($LASTEXITCODE -ne 0) {
        Fail "Docker Compose configuration is invalid. Review the error above and .env."
    }
}

function Test-ServiceReady([string]$Service, [int]$Port) {
    if ($Service -eq "frontend") {
        & docker compose exec -T frontend wget -q -O /dev/null http://127.0.0.1/ *> $null
    }
    else {
        $code = "import httpx; httpx.get('http://localhost:$Port/health', timeout=5).raise_for_status()"
        & docker compose exec -T $Service python -c $code *> $null
    }
    return $LASTEXITCODE -eq 0
}

function Wait-ForServiceReadiness {
    Write-Output "Waiting for service readiness..."
    for ($attempt = 1; $attempt -le 60; $attempt++) {
        $ready = (Test-ServiceReady "portfolio-engine" 8002) -and
            (Test-ServiceReady "data-ingestion" 8000) -and
            (Test-ServiceReady "notification-gateway" 8003) -and
            (Test-ServiceReady "quant-engine" 8001) -and
            (Test-ServiceReady "frontend" 80)
        if ($ready) {
            Write-Output "All services are ready."
            return
        }
        Start-Sleep -Seconds 2
    }
    & docker compose ps
    Fail "Services did not become ready within 120 seconds. Run '.\manage.ps1 logs' for details."
}

function Get-PublicUrl {
    $configured = Get-EnvValue "PUBLIC_BASE_URL"
    if (-not [string]::IsNullOrEmpty($configured)) {
        return $configured
    }
    return "http://localhost:$(Get-EnvValue 'FRONTEND_PORT')"
}

function Install-App([string[]]$Arguments) {
    $baseUrl = ""
    $bindAddress = ""
    $noStart = $false
    for ($index = 0; $index -lt $Arguments.Count; $index++) {
        switch ($Arguments[$index]) {
            "--base-url" {
                if ($index + 1 -ge $Arguments.Count) { Fail "--base-url requires a value." }
                $index++
                $baseUrl = $Arguments[$index]
            }
            "--bind-address" {
                if ($index + 1 -ge $Arguments.Count) { Fail "--bind-address requires a value." }
                $index++
                $bindAddress = $Arguments[$index]
            }
            "--no-start" { $noStart = $true }
            default { Fail "Unknown install option '$($Arguments[$index])'." }
        }
    }

    Invoke-Preflight
    Initialize-Env -BaseUrl $baseUrl -BindAddress $bindAddress
    Test-ComposeConfig
    if ($noStart) {
        Write-Output "Installation configuration is ready. Secrets were written to .env without being displayed."
        return
    }
    Invoke-Compose @("up", "--build", "-d")
    Wait-ForServiceReadiness
    Write-Output "Dashboard: $(Get-PublicUrl)"
}

function Start-App([string[]]$Arguments) {
    $build = $false
    foreach ($argument in $Arguments) {
        if ($argument -eq "--build") {
            $build = $true
        }
        else {
            Fail "Unknown start option '$argument'."
        }
    }
    Invoke-Preflight
    Test-ComposeConfig
    if ($build) {
        Invoke-Compose @("up", "--build", "-d")
    }
    else {
        Invoke-Compose @("up", "-d")
    }
    Wait-ForServiceReadiness
    Write-Output "Dashboard: $(Get-PublicUrl)"
}

function Stop-App {
    Invoke-Preflight
    Assert-EnvFile
    Invoke-Compose @("down")
    Write-Output "Application stopped. Persistent volumes were preserved."
}

function Show-Status {
    Invoke-Preflight
    Assert-EnvFile
    Invoke-Compose @("ps")
    Write-Output "Dashboard: $(Get-PublicUrl)"
}

function Show-Log([string[]]$Arguments) {
    $service = ""
    $follow = $false
    $tailLines = "200"
    for ($index = 0; $index -lt $Arguments.Count; $index++) {
        switch ($Arguments[$index]) {
            { $_ -in @("--follow", "-f") } { $follow = $true }
            "--tail" {
                if ($index + 1 -ge $Arguments.Count) { Fail "--tail requires a line count." }
                $index++
                $tailLines = $Arguments[$index]
            }
            { $_.StartsWith("-") } { Fail "Unknown logs option '$($_)'." }
            default {
                if (-not [string]::IsNullOrEmpty($service)) { Fail "Only one service may be specified." }
                $service = $_
            }
        }
    }
    Invoke-Preflight
    Assert-EnvFile
    $composeArgs = @("logs", "--tail", $tailLines)
    if ($follow) { $composeArgs += "--follow" }
    if (-not [string]::IsNullOrEmpty($service)) { $composeArgs += $service }
    Invoke-Compose $composeArgs
}

function Get-VolumeName([string]$Service, [string]$Target) {
    $containerId = (Get-ComposeOutput @("ps", "-aq", $Service) | Select-Object -First 1)
    if ([string]::IsNullOrWhiteSpace($containerId)) {
        & docker compose create $Service *> $null
        if ($LASTEXITCODE -ne 0) { Fail "Unable to create the $Service container." }
        $containerId = (Get-ComposeOutput @("ps", "-aq", $Service) | Select-Object -First 1)
    }
    if ([string]::IsNullOrWhiteSpace($containerId)) {
        Fail "Unable to create or locate the $Service container."
    }
    $format = "{{range .Mounts}}{{if eq .Destination `"$Target`"}}{{.Name}}{{end}}{{end}}"
    $volume = & docker inspect $containerId --format $format
    if ($LASTEXITCODE -ne 0) { Fail "Unable to inspect the $Service data volume." }
    return ("$volume").Trim()
}

function Export-Volume([string]$Volume, [string]$BackupDir, [string]$FileName) {
    $code = "import sys, tarfile; archive=tarfile.open('/backup/' + sys.argv[1], 'w:gz'); archive.add('/source', arcname='.'); archive.close()"
    Invoke-Checked -File "docker" -Arguments @(
        "run", "--rm",
        "--volume", "${Volume}:/source:ro",
        "--volume", "${BackupDir}:/backup",
        "python:3.12-slim", "python", "-c", $code, $FileName
    )
}

function Import-Volume([string]$Volume, [string]$BackupDir, [string]$FileName) {
    $code = "import pathlib, shutil, sys, tarfile; root=pathlib.Path('/restore'); [shutil.rmtree(item) if item.is_dir() else item.unlink() for item in root.iterdir()]; archive=tarfile.open('/backup/' + sys.argv[1]); archive.extractall('/restore', filter='data'); archive.close()"
    Invoke-Checked -File "docker" -Arguments @(
        "run", "--rm",
        "--volume", "${Volume}:/restore",
        "--volume", "${BackupDir}:/backup:ro",
        "python:3.12-slim", "python", "-c", $code, $FileName
    )
}

function Backup-App([string[]]$Arguments) {
    if ($Arguments.Count -gt 1) { Fail "Usage: .\manage.ps1 backup [DIRECTORY]" }
    $backupDir = if ($Arguments.Count -eq 1) {
        $Arguments[0]
    }
    else {
        Join-Path $RootDir "backups/$([DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ'))"
    }
    Invoke-Preflight
    Test-ComposeConfig
    New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
    $backupDir = (Resolve-Path $backupDir).Path

    $running = Get-ComposeOutput @("ps", "--status", "running", "-q")
    $hadRunning = -not [string]::IsNullOrWhiteSpace("$running")
    & docker compose create data-ingestion quant-engine portfolio-engine *> $null
    if ($LASTEXITCODE -ne 0) { Fail "Unable to prepare data containers for backup." }
    Invoke-Compose @("stop")

    try {
        foreach ($spec in $VolumeSpecs) {
            $volume = Get-VolumeName -Service $spec.Service -Target $spec.Target
            if ([string]::IsNullOrWhiteSpace($volume)) {
                Fail "Unable to resolve the $($spec.Service) data volume."
            }
            Export-Volume -Volume $volume -BackupDir $backupDir -FileName $spec.File
        }
        $commit = & git rev-parse HEAD
        if ($LASTEXITCODE -ne 0) { $commit = "unknown" }
        $manifest = @(
            "created_at=$([DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ'))",
            "git_commit=$commit"
        )
        [IO.File]::WriteAllLines(
            (Join-Path $backupDir "manifest.txt"),
            $manifest,
            $Utf8NoBom
        )
    }
    finally {
        if ($hadRunning) {
            Invoke-Compose @("start")
            Wait-ForServiceReadiness
        }
    }
    Write-Output "Backup created: $backupDir"
}

function Restore-App([string[]]$Arguments) {
    if ($Arguments.Count -lt 1 -or $Arguments.Count -gt 2) {
        Fail "Usage: .\manage.ps1 restore DIRECTORY [--no-start]"
    }
    $backupDir = $Arguments[0]
    $noStart = $Arguments.Count -eq 2 -and $Arguments[1] -eq "--no-start"
    if ($Arguments.Count -eq 2 -and -not $noStart) {
        Fail "Usage: .\manage.ps1 restore DIRECTORY [--no-start]"
    }
    if (-not (Test-Path $backupDir -PathType Container)) {
        Fail "Backup directory '$backupDir' does not exist."
    }
    $backupDir = (Resolve-Path $backupDir).Path

    Invoke-Preflight
    Test-ComposeConfig
    foreach ($spec in $VolumeSpecs) {
        if (-not (Test-Path (Join-Path $backupDir $spec.File) -PathType Leaf)) {
            Fail "Backup is incomplete: missing $($spec.File)."
        }
    }

    Invoke-Compose @("down")
    & docker compose create data-ingestion quant-engine portfolio-engine *> $null
    if ($LASTEXITCODE -ne 0) { Fail "Unable to prepare data containers for restore." }
    foreach ($spec in $VolumeSpecs) {
        $volume = Get-VolumeName -Service $spec.Service -Target $spec.Target
        Import-Volume -Volume $volume -BackupDir $backupDir -FileName $spec.File
    }

    if ($noStart) {
        Write-Output "Backup restored. Services remain stopped."
        return
    }
    Invoke-Compose @("up", "-d")
    Wait-ForServiceReadiness
    Write-Output "Backup restored and verified."
}

function Update-App([string[]]$Arguments) {
    if ($Arguments.Count -ne 0) { Fail "Usage: .\manage.ps1 upgrade" }
    Invoke-Preflight
    Test-ComposeConfig
    $status = & git status --porcelain
    if ($LASTEXITCODE -ne 0) { Fail "Unable to inspect the Git worktree." }
    if (-not [string]::IsNullOrWhiteSpace("$status")) {
        Fail "Git worktree has local changes. Commit or move them before upgrading."
    }
    Backup-App @()
    Invoke-Checked -File "git" -Arguments @("pull", "--ff-only")
    Invoke-Compose @("build", "--pull")
    Invoke-Compose @("up", "-d")
    Wait-ForServiceReadiness
    Write-Output "Upgrade completed. Dashboard: $(Get-PublicUrl)"
}

function Test-App([string[]]$Arguments) {
    $configOnly = $false
    if ($Arguments.Count -eq 1 -and $Arguments[0] -eq "--config-only") {
        $configOnly = $true
    }
    elseif ($Arguments.Count -ne 0) {
        Fail "Usage: .\manage.ps1 verify [--config-only]"
    }
    Invoke-Preflight
    Test-ComposeConfig
    if (-not $configOnly) {
        Wait-ForServiceReadiness
    }
    Write-Output "Verification passed."
}

if ($CommandArgs.Count -gt 0 -and $CommandArgs[0] -in @("--help", "-h")) {
    Show-CommandHelp $Command
    exit 0
}

switch ($Command) {
    { $_ -in @("help", "--help", "-h") } { Show-Usage }
    "install" { Install-App $CommandArgs }
    "start" { Start-App $CommandArgs }
    "stop" {
        if ($CommandArgs.Count -ne 0) { Fail "Usage: .\manage.ps1 stop" }
        Stop-App
    }
    "restart" {
        Stop-App
        Start-App $CommandArgs
    }
    "status" {
        if ($CommandArgs.Count -ne 0) { Fail "Usage: .\manage.ps1 status" }
        Show-Status
    }
    "logs" { Show-Log $CommandArgs }
    "backup" { Backup-App $CommandArgs }
    "restore" { Restore-App $CommandArgs }
    "upgrade" { Update-App $CommandArgs }
    "verify" { Test-App $CommandArgs }
    default { Fail "Unknown command '$Command'. Run '.\manage.ps1 help'." }
}
