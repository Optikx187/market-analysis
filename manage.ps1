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
$BackupTool = Join-Path $RootDir "scripts/backup_manifest.py"
$RunningOnWindows = $env:OS -eq "Windows_NT"
$Utf8NoBom = New-Object System.Text.UTF8Encoding -ArgumentList $false
$VolumeSpecs = @(
    @{ Service = "data-ingestion"; Target = "/app/data"; File = "data-ingestion-db.tgz" },
    @{ Service = "quant-engine"; Target = "/app/data"; File = "quant-engine-db.tgz" },
    @{ Service = "portfolio-engine"; Target = "/app/data"; File = "portfolio-db.tgz" }
)

Set-Location $RootDir

function Fail([string]$Message) {
    throw $Message
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
  verify-backup  Validate backup checksums, schemas, and record counts
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
Usage: .\manage.ps1 install [--base-url URL] [--bind-address ADDRESS] [--enable-auth] [--no-start]

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
        "verify-backup" {
            "Usage: .\manage.ps1 verify-backup DIRECTORY"
        }
        "restore" {
            "Usage: .\manage.ps1 restore DIRECTORY [--no-start] [--project-name NAME] [--allow-legacy]"
        }
        "upgrade" {
            "Usage: .\manage.ps1 upgrade [--allow-backup-failure]"
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

function Protect-Path([string]$Path, [bool]$Directory) {
    if ($RunningOnWindows) {
        try {
            $identity = [Security.Principal.WindowsIdentity]::GetCurrent().User
            $acl = if ($Directory) {
                [Security.AccessControl.DirectorySecurity]::new()
            }
            else {
                [Security.AccessControl.FileSecurity]::new()
            }
            $acl.SetOwner($identity)
            $acl.SetAccessRuleProtection($true, $false)
            $rule = if ($Directory) {
                [Security.AccessControl.FileSystemAccessRule]::new(
                    $identity,
                    [Security.AccessControl.FileSystemRights]::FullControl,
                    [Security.AccessControl.InheritanceFlags]"ContainerInherit,ObjectInherit",
                    [Security.AccessControl.PropagationFlags]::None,
                    [Security.AccessControl.AccessControlType]::Allow
                )
            }
            else {
                [Security.AccessControl.FileSystemAccessRule]::new(
                    $identity,
                    [Security.AccessControl.FileSystemRights]::FullControl,
                    [Security.AccessControl.AccessControlType]::Allow
                )
            }
            $acl.AddAccessRule($rule)
            Set-Acl -LiteralPath $Path -AclObject $acl
        }
        catch {
            Fail "Unable to restrict '$Path' to the current Windows user."
        }
    }
    else {
        $mode = if ($Directory) { "700" } else { "600" }
        Invoke-Checked -File "chmod" -Arguments @($mode, $Path)
    }
}

function Protect-EnvFile {
    Protect-Path -Path $EnvFile -Directory $false
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

function Initialize-Env([string]$BaseUrl, [string]$BindAddress, [bool]$EnableAuth) {
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

    if (-not [string]::IsNullOrEmpty($BindAddress)) {
        Set-EnvValue -Key "HOST_BIND_ADDRESS" -Value $BindAddress
    }
    elseif ([string]::IsNullOrEmpty((Get-EnvValue "HOST_BIND_ADDRESS"))) {
        Set-EnvValue -Key "HOST_BIND_ADDRESS" -Value "127.0.0.1"
    }

    if ($EnableAuth) {
        Set-EnvValue -Key "AUTH_ENABLED" -Value "true"
    }

    Initialize-Secret
    Protect-EnvFile
}

function Test-ComposeConfig {
    Assert-EnvFile
    $bindAddress = Get-EnvValue "HOST_BIND_ADDRESS"
    if ([string]::IsNullOrEmpty($bindAddress)) {
        $bindAddress = "127.0.0.1"
    }
    $authEnabled = Get-EnvValue "AUTH_ENABLED"
    if ($bindAddress -ne "127.0.0.1" -and $authEnabled -ne "true") {
        Fail "AUTH_ENABLED=true is required when HOST_BIND_ADDRESS is not 127.0.0.1. Re-run install with --enable-auth."
    }
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
    $enableAuth = $false
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
            "--enable-auth" { $enableAuth = $true }
            "--no-start" { $noStart = $true }
            default { Fail "Unknown install option '$($Arguments[$index])'." }
        }
    }

    Invoke-Preflight
    Initialize-Env -BaseUrl $baseUrl -BindAddress $bindAddress -EnableAuth $enableAuth
    Test-ComposeConfig
    if ($noStart) {
        Write-Output "Installation configuration is ready. Secrets were written to .env without being displayed."
        return
    }
    Invoke-Compose -Arguments @("up", "--build", "-d")
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
        Invoke-Compose -Arguments @("up", "--build", "-d")
    }
    else {
        Invoke-Compose -Arguments @("up", "-d")
    }
    Wait-ForServiceReadiness
    Write-Output "Dashboard: $(Get-PublicUrl)"
}

function Stop-App {
    Invoke-Preflight
    Assert-EnvFile
    Invoke-Compose -Arguments @("down")
    Write-Output "Application stopped. Persistent volumes were preserved."
}

function Show-Status {
    Invoke-Preflight
    Assert-EnvFile
    Invoke-Compose -Arguments @("ps")
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
    Invoke-Compose -Arguments $composeArgs
}

function Get-VolumeName([string]$Service, [string]$Target) {
    $containerId = (Get-ComposeOutput -Arguments @("ps", "-aq", $Service) | Select-Object -First 1)
    if ([string]::IsNullOrWhiteSpace($containerId)) {
        & docker compose up --no-start --no-deps $Service *> $null
        if ($LASTEXITCODE -ne 0) { Fail "Unable to create the $Service container." }
        $containerId = (Get-ComposeOutput -Arguments @("ps", "-aq", $Service) | Select-Object -First 1)
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
    $code = "import os, sys, tarfile; os.umask(0o077); archive=tarfile.open('/backup/' + sys.argv[1], 'w:gz'); archive.add('/source', arcname='.'); archive.close()"
    Invoke-Checked -File "docker" -Arguments @(
        "run", "--rm",
        "--volume", "${Volume}:/source:ro",
        "--volume", "${BackupDir}:/backup",
        "python:3.12-slim", "python", "-c", $code, $FileName
    )
    Protect-Path -Path (Join-Path $BackupDir $FileName) -Directory $false
}

function Test-Archive([string]$BackupDir, [string]$FileName) {
    $code = "import pathlib, sys, tarfile; archive=tarfile.open('/backup/' + sys.argv[1]); members=archive.getmembers(); invalid=[m.name for m in members if pathlib.PurePosixPath(m.name).is_absolute() or '..' in pathlib.PurePosixPath(m.name).parts or m.issym() or m.islnk() or m.isdev() or m.isfifo()]; invalid and sys.exit('unsafe archive member: ' + invalid[0]); [(lambda stream: [None for _ in iter(lambda: stream.read(1024 * 1024), b'')])(archive.extractfile(m)) for m in members if m.isfile()]; archive.close()"
    Invoke-Checked -File "docker" -Arguments @(
        "run", "--rm",
        "--volume", "${BackupDir}:/backup:ro",
        "python:3.12-slim", "python", "-c", $code, $FileName
    )
}

function Expand-ArchiveToVolume([string]$Volume, [string]$BackupDir, [string]$FileName) {
    $code = "import pathlib, shutil, sys, tarfile; root=pathlib.Path('/restore'); [shutil.rmtree(item) if item.is_dir() else item.unlink() for item in root.iterdir()]; archive=tarfile.open('/backup/' + sys.argv[1]); archive.extractall('/restore', filter='data'); archive.close()"
    Invoke-Checked -File "docker" -Arguments @(
        "run", "--rm",
        "--volume", "${Volume}:/restore",
        "--volume", "${BackupDir}:/backup:ro",
        "python:3.12-slim", "python", "-c", $code, $FileName
    )
}

function Copy-Volume([string]$Source, [string]$Destination) {
    $code = "import pathlib, shutil; source=pathlib.Path('/source'); destination=pathlib.Path('/destination'); [shutil.rmtree(item) if item.is_dir() else item.unlink() for item in destination.iterdir()]; [shutil.copytree(item, destination / item.name, symlinks=False) if item.is_dir() else shutil.copy2(item, destination / item.name) for item in source.iterdir()]"
    Invoke-Checked -File "docker" -Arguments @(
        "run", "--rm",
        "--volume", "${Source}:/source:ro",
        "--volume", "${Destination}:/destination",
        "python:3.12-slim", "python", "-c", $code
    )
}

function Invoke-BackupTool {
    param(
        [Parameter(Mandatory = $true)][string]$BackupDir,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [string]$Volume = ""
    )
    if (-not (Test-Path $BackupTool -PathType Leaf)) {
        Fail "Backup metadata tool is missing: $BackupTool"
    }
    $dockerArguments = @(
        "run", "--rm",
        "--volume", "${BackupTool}:/tool.py:ro",
        "--volume", "${BackupDir}:/backup:ro"
    )
    if (-not [string]::IsNullOrWhiteSpace($Volume)) {
        $dockerArguments += @("--volume", "${Volume}:/volume:ro")
    }
    $dockerArguments += @("python:3.12-slim", "python", "/tool.py")
    $dockerArguments += $Arguments
    $output = & docker @dockerArguments
    if ($LASTEXITCODE -ne 0) {
        Fail "Backup metadata verification failed."
    }
    return $output
}

function New-BackupManifest([string]$BackupDir) {
    $commit = (& git rev-parse HEAD 2>$null | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($commit)) {
        $commit = "unknown"
    }
    $version = (& git describe --always --dirty --tags 2>$null | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($version)) {
        $version = $commit
    }
    $composeProject = $env:COMPOSE_PROJECT_NAME
    if ([string]::IsNullOrWhiteSpace($composeProject)) {
        $composeProject = Split-Path $RootDir -Leaf
    }
    $encryptionArgument = if (
        [string]::IsNullOrWhiteSpace((Get-EnvValue "CREDENTIAL_ENCRYPTION_KEYS"))
    ) {
        "--no-encryption-key-configured"
    }
    else {
        "--encryption-key-configured"
    }
    $manifest = Invoke-BackupTool -BackupDir $BackupDir -Arguments @(
        "create",
        "--backup-dir", "/backup",
        "--git-commit", $commit,
        "--app-version", $version,
        "--compose-project", $composeProject,
        $encryptionArgument
    )
    [IO.File]::WriteAllText(
        (Join-Path $BackupDir "manifest.json"),
        (($manifest -join [Environment]::NewLine) + [Environment]::NewLine),
        $Utf8NoBom
    )
    Protect-Path -Path (Join-Path $BackupDir "manifest.json") -Directory $false
}

function Test-BackupManifest([string]$BackupDir) {
    Invoke-BackupTool -BackupDir $BackupDir -Arguments @(
        "verify", "--backup-dir", "/backup"
    )
}

function Test-RestoredVolume(
    [string]$Volume,
    [string]$BackupDir,
    [string]$Service
) {
    Invoke-BackupTool -BackupDir $BackupDir -Volume $Volume -Arguments @(
        "verify-volume",
        "--backup-dir", "/backup",
        "--volume-root", "/volume",
        "--service", $Service
    )
}

function Assert-ProjectName([string]$ProjectName) {
    if ($ProjectName -notmatch "^[a-z0-9][a-z0-9_-]*$") {
        Fail "Compose project name must use lowercase letters, digits, hyphens, or underscores."
    }
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
    Protect-Path -Path $backupDir -Directory $true

    $runningServices = @(
        Get-ComposeOutput -Arguments @("ps", "--services", "--status", "running") |
            Where-Object { -not [string]::IsNullOrWhiteSpace("$_") }
    )
    if ($runningServices.Count -gt 0) {
        Invoke-Compose -Arguments (@("stop") + $runningServices)
    }

    try {
        foreach ($spec in $VolumeSpecs) {
            $volume = Get-VolumeName -Service $spec.Service -Target $spec.Target
            if ([string]::IsNullOrWhiteSpace($volume)) {
                Fail "Unable to resolve the $($spec.Service) data volume."
            }
            Export-Volume -Volume $volume -BackupDir $backupDir -FileName $spec.File
        }
        New-BackupManifest -BackupDir $backupDir
        Test-BackupManifest -BackupDir $backupDir
    }
    finally {
        if ($runningServices.Count -gt 0) {
            Invoke-Compose -Arguments (@("start") + $runningServices)
        }
    }
    Write-Output "Backup created and verified: $backupDir"
    Write-Output "Encryption keys are not included. Retain CREDENTIAL_ENCRYPTION_KEYS separately."
}

function Test-BackupApp([string[]]$Arguments) {
    if ($Arguments.Count -ne 1) {
        Fail "Usage: .\manage.ps1 verify-backup DIRECTORY"
    }
    $backupDir = $Arguments[0]
    if (-not (Test-Path $backupDir -PathType Container)) {
        Fail "Backup directory '$backupDir' does not exist."
    }
    $backupDir = (Resolve-Path $backupDir).Path
    Invoke-Preflight
    Test-BackupManifest -BackupDir $backupDir
}

function Restore-App([string[]]$Arguments) {
    if ($Arguments.Count -lt 1) {
        Fail "Usage: .\manage.ps1 restore DIRECTORY [--no-start] [--project-name NAME] [--allow-legacy]"
    }
    $backupDir = $Arguments[0]
    $noStart = $false
    $allowLegacy = $false
    $projectName = ""
    for ($index = 1; $index -lt $Arguments.Count; $index++) {
        switch ($Arguments[$index]) {
            "--no-start" {
                $noStart = $true
            }
            "--allow-legacy" {
                $allowLegacy = $true
            }
            "--project-name" {
                if ($index + 1 -ge $Arguments.Count) {
                    Fail "--project-name requires a value."
                }
                $index++
                $projectName = $Arguments[$index]
            }
            default {
                Fail "Usage: .\manage.ps1 restore DIRECTORY [--no-start] [--project-name NAME] [--allow-legacy]"
            }
        }
    }
    if (-not (Test-Path $backupDir -PathType Container)) {
        Fail "Backup directory '$backupDir' does not exist."
    }
    $backupDir = (Resolve-Path $backupDir).Path
    if (-not [string]::IsNullOrWhiteSpace($projectName)) {
        $sourceProject = $env:COMPOSE_PROJECT_NAME
        if ([string]::IsNullOrWhiteSpace($sourceProject)) {
            $sourceProject = Get-EnvValue "COMPOSE_PROJECT_NAME"
        }
        if ([string]::IsNullOrWhiteSpace($sourceProject)) {
            $sourceProject = Split-Path $RootDir -Leaf
        }
        Assert-ProjectName -ProjectName $projectName
        if ($projectName -eq $sourceProject) {
            Fail "--project-name must differ from the active Compose project '$sourceProject'."
        }
        if (-not $noStart) {
            Fail "--project-name requires --no-start so isolated restores cannot conflict with the running deployment."
        }
        $env:COMPOSE_PROJECT_NAME = $projectName
    }

    Invoke-Preflight
    Test-ComposeConfig
    $verifiedManifest = $false
    if (Test-Path (Join-Path $backupDir "manifest.json") -PathType Leaf) {
        Test-BackupManifest -BackupDir $backupDir
        $verifiedManifest = $true
    }
    elseif ($allowLegacy) {
        Write-Output "Warning: restoring a legacy backup without checksum or record-count reconciliation."
    }
    else {
        Fail "Backup is missing manifest.json. Use --allow-legacy only for a trusted older backup."
    }
    foreach ($spec in $VolumeSpecs) {
        if (-not (Test-Path (Join-Path $backupDir $spec.File) -PathType Leaf)) {
            Fail "Backup is incomplete: missing $($spec.File)."
        }
        Test-Archive -BackupDir $backupDir -FileName $spec.File
    }

    $runningServices = @(
        Get-ComposeOutput -Arguments @("ps", "--services", "--status", "running") |
            Where-Object { -not [string]::IsNullOrWhiteSpace("$_") }
    )
    $restoreId = "market-analysis-restore-$([DateTime]::UtcNow.ToString('yyyyMMddHHmmss'))-$PID"
    $liveVolumes = [Collections.Generic.List[string]]::new()
    $stageVolumes = [Collections.Generic.List[string]]::new()
    $rollbackVolumes = [Collections.Generic.List[string]]::new()
    $rollbackReadyCount = 0
    $servicesStopped = $false
    $restoreSucceeded = $false

    try {
        for ($index = 0; $index -lt $VolumeSpecs.Count; $index++) {
            $spec = $VolumeSpecs[$index]
            $liveVolumes.Add((Get-VolumeName -Service $spec.Service -Target $spec.Target))
            $stageVolume = "$restoreId-$index-stage"
            Invoke-Checked -File "docker" -Arguments @("volume", "create", $stageVolume)
            $stageVolumes.Add($stageVolume)
            Expand-ArchiveToVolume -Volume $stageVolume -BackupDir $backupDir -FileName $spec.File
            if ($verifiedManifest) {
                Test-RestoredVolume `
                    -Volume $stageVolume `
                    -BackupDir $backupDir `
                    -Service $spec.Service
            }
            $rollbackVolume = "$restoreId-$index-rollback"
            Invoke-Checked -File "docker" -Arguments @("volume", "create", $rollbackVolume)
            $rollbackVolumes.Add($rollbackVolume)
        }
        if ($runningServices.Count -gt 0) {
            Invoke-Compose -Arguments (@("stop") + $runningServices)
        }
        $servicesStopped = $true
        for ($index = 0; $index -lt $liveVolumes.Count; $index++) {
            Copy-Volume -Source $liveVolumes[$index] -Destination $rollbackVolumes[$index]
            $rollbackReadyCount++
        }
        for ($index = 0; $index -lt $liveVolumes.Count; $index++) {
            Copy-Volume -Source $stageVolumes[$index] -Destination $liveVolumes[$index]
        }
        $restoreSucceeded = $true
    }
    finally {
        if (-not $restoreSucceeded -and $servicesStopped) {
            for ($index = 0; $index -lt $rollbackReadyCount; $index++) {
                try {
                    Copy-Volume -Source $rollbackVolumes[$index] -Destination $liveVolumes[$index]
                }
                catch {
                    Write-Verbose "Unable to restore rollback volume $index."
                }
            }
            if ($runningServices.Count -gt 0) {
                try {
                    Invoke-Compose -Arguments (@("start") + $runningServices)
                }
                catch {
                    Write-Verbose "Unable to restart the previously running services."
                }
            }
        }
        foreach ($volume in @($stageVolumes) + @($rollbackVolumes)) {
            try {
                & docker volume rm -f $volume *> $null
            }
            catch {
                Write-Verbose "Unable to remove temporary volume $volume."
            }
        }
    }

    if ($noStart) {
        if (-not [string]::IsNullOrWhiteSpace($projectName)) {
            Write-Output "Backup restored into isolated Compose project '$projectName'. Services remain stopped."
        }
        else {
            Write-Output "Backup restored. Services remain stopped."
        }
        return
    }
    Invoke-Compose -Arguments @("up", "-d")
    Wait-ForServiceReadiness
    Write-Output "Backup restored and verified."
}

function Update-App([string[]]$Arguments) {
    $allowBackupFailure = $false
    if ($Arguments.Count -eq 1 -and $Arguments[0] -eq "--allow-backup-failure") {
        $allowBackupFailure = $true
    }
    elseif ($Arguments.Count -ne 0) {
        Fail "Usage: .\manage.ps1 upgrade [--allow-backup-failure]"
    }
    Invoke-Preflight
    Test-ComposeConfig
    $status = & git status --porcelain
    if ($LASTEXITCODE -ne 0) { Fail "Unable to inspect the Git worktree." }
    if (-not [string]::IsNullOrWhiteSpace("$status")) {
        Fail "Git worktree has local changes. Commit or move them before upgrading."
    }
    try {
        Backup-App @()
    }
    catch {
        if (-not $allowBackupFailure) {
            Fail "Upgrade stopped because the pre-upgrade backup failed. $($_.Exception.Message)"
        }
        Write-Output "Warning: continuing upgrade after backup failure by explicit operator override."
    }
    Invoke-Checked -File "git" -Arguments @("pull", "--ff-only")
    Invoke-Compose -Arguments @("build", "--pull")
    Invoke-Compose -Arguments @("up", "-d")
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

try {
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
        "verify-backup" { Test-BackupApp $CommandArgs }
        "restore" { Restore-App $CommandArgs }
        "upgrade" { Update-App $CommandArgs }
        "verify" { Test-App $CommandArgs }
        default { Fail "Unknown command '$Command'. Run '.\manage.ps1 help'." }
    }
}
catch {
    [Console]::Error.WriteLine("Error: $($_.Exception.Message)")
    exit 1
}
