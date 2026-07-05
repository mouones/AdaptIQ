<#
AdaptIQ full audit runner — continues on failure and writes audit.json + summary.md

Run from P_F_E project root:
  powershell -ExecutionPolicy Bypass -File .\scripts\run_full_audit.ps1 -Install

Fast rerun (services already running):
  powershell -ExecutionPolicy Bypass -File .\scripts\run_full_audit.ps1 -SkipDocker -Install

Optional:
  -SkipDocker      Do not start postgres/redis
  -SkipFrontend    Skip frontend lint/build/e2e
  -SkipPostman     Skip Newman collection
  -SkipE2E          Skip Playwright
  -SkipLive         Skip live validation Python scripts
#>

param(
  [switch]$Install,
  [switch]$SkipDocker,
  [switch]$SkipFrontend,
  [switch]$SkipPostman,
  [switch]$SkipE2E,
  [switch]$SkipLive,
  [string]$ApiBase = "http://localhost:8000",
  [int]$BackendWaitSeconds = 90
)

$ErrorActionPreference = "Continue"

$Root = Split-Path $PSScriptRoot -Parent
$Backend = Join-Path $Root "backend"
$Frontend = Join-Path $Root "frontend"
$RunStamp = Get-Date -Format "yyyyMMdd_HHmmss"
$RunDir = Join-Path $Root "generated\validation_runs\$RunStamp"
New-Item -ItemType Directory -Force -Path $RunDir | Out-Null

$Summary = New-Object System.Collections.Generic.List[string]
$Audit = New-Object System.Collections.Generic.List[object]
$Failures = 0
$Passes = 0
$BackendProcess = $null

function Add-AuditEntry {
  param(
    [string]$Name,
    [string]$Status,
    [double]$ElapsedSeconds,
    [string]$LogPath = "",
    [string]$ErrorMessage = ""
  )
  $script:Audit.Add([ordered]@{
      step = $Name
      status = $Status
      duration_seconds = $ElapsedSeconds
      log = $LogPath
      error = $ErrorMessage
    }) | Out-Null
}

function Invoke-Step {
  param(
    [string]$Name,
    [scriptblock]$Block
  )

  Write-Host "`n=== $Name ===" -ForegroundColor Cyan
  $start = Get-Date
  $logPath = ""
  try {
    & $Block
    $elapsed = [math]::Round(((Get-Date) - $start).TotalSeconds, 2)
    $Summary.Add("PASS | $Name | ${elapsed}s") | Out-Null
    Add-AuditEntry -Name $Name -Status "PASS" -ElapsedSeconds $elapsed
    Write-Host "PASS: $Name (${elapsed}s)" -ForegroundColor Green
    $script:Passes += 1
  }
  catch {
    $elapsed = [math]::Round(((Get-Date) - $start).TotalSeconds, 2)
    $msg = $_.Exception.Message
    $Summary.Add("FAIL | $Name | ${elapsed}s | $msg") | Out-Null
    Add-AuditEntry -Name $Name -Status "FAIL" -ElapsedSeconds $elapsed -ErrorMessage $msg
    Write-Host "FAIL: $Name" -ForegroundColor Red
    Write-Host $msg -ForegroundColor Red
    $script:Failures += 1
  }
}

function Invoke-CommandLogged {
  param(
    [string]$Name,
    [string]$WorkingDirectory,
    [string]$Exe,
    [string[]]$Args
  )

  $log = Join-Path $RunDir (($Name -replace '[^a-zA-Z0-9_-]', '_') + ".log")
  Push-Location $WorkingDirectory
  try {
    Write-Host "$Exe $($Args -join ' ')"
    & $Exe @Args *>&1 | Tee-Object -FilePath $log
    if ($LASTEXITCODE -ne 0) {
      throw "$Name exited with code $LASTEXITCODE. See $log"
    }
    return $log
  }
  finally {
    Pop-Location
  }
}

function Get-PythonExe {
  if (Test-Path (Join-Path $Backend ".venv\Scripts\python.exe")) {
    return (Join-Path $Backend ".venv\Scripts\python.exe")
  }
  if (Get-Command python -ErrorAction SilentlyContinue) { return "python" }
  if (Get-Command py -ErrorAction SilentlyContinue) { return "py" }
  throw "Python was not found. Install Python or create backend\.venv."
}

function Test-HttpOk {
  param([string]$Url)
  try {
    $resp = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3
    return ($resp.StatusCode -ge 200 -and $resp.StatusCode -lt 500)
  }
  catch {
    return $false
  }
}

function Wait-Backend {
  param([string]$Url, [int]$TimeoutSeconds)
  $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
  while ((Get-Date) -lt $deadline) {
    if (Test-HttpOk "$Url/api/system/health") { return $true }
    Start-Sleep -Seconds 2
  }
  return $false
}

try {
  $Summary.Add("AdaptIQ full audit run: $RunStamp") | Out-Null
  $Summary.Add("Root: $Root") | Out-Null
  $Summary.Add("API base: $ApiBase") | Out-Null

  Invoke-Step "Preflight: check folders" {
    if (!(Test-Path $Backend)) { throw "Missing backend folder: $Backend" }
    if (!(Test-Path $Frontend)) { Write-Host "Frontend folder not found; frontend steps may be skipped." -ForegroundColor Yellow }
  }

  if (!$SkipDocker) {
    Invoke-Step "Start Docker services: postgres + redis" {
      if (Test-Path (Join-Path $Root "docker-compose.yml")) {
        if (Get-Command docker -ErrorAction SilentlyContinue) {
          Invoke-CommandLogged "docker_compose_up" $Root "docker" @("compose", "up", "-d", "postgres", "redis")
        }
        else {
          throw "Docker not found. Use -SkipDocker if services are already running."
        }
      }
      else {
        Write-Host "docker-compose.yml not found; skipping Docker startup." -ForegroundColor Yellow
      }
    }
  }

  Invoke-Step "Backend dependencies" {
    $py = Get-PythonExe
    if ($Install) {
      if (!(Test-Path (Join-Path $Backend ".venv"))) {
        Invoke-CommandLogged "create_backend_venv" $Backend $py @("-m", "venv", ".venv")
      }
      $venvPy = Join-Path $Backend ".venv\Scripts\python.exe"
      Invoke-CommandLogged "pip_install_backend" $Backend $venvPy @("-m", "pip", "install", "-r", "requirements.txt")
      if (Test-Path (Join-Path $Backend "requirements-dev.txt")) {
        Invoke-CommandLogged "pip_install_backend_dev" $Backend $venvPy @("-m", "pip", "install", "-r", "requirements-dev.txt")
      }
    }
    else {
      Write-Host "Install skipped. Use -Install to install backend + dev requirements." -ForegroundColor Yellow
    }
  }

  Invoke-Step "Backend pytest suite" {
    $py = Get-PythonExe
    $junit = Join-Path $RunDir "backend_pytest_junit.xml"
    Invoke-CommandLogged "backend_pytest" $Backend $py @("-m", "pytest", "-q", "--tb=short", "--junitxml", $junit)
  }

  Invoke-Step "Start backend and verify health" {
    if (Test-HttpOk "$ApiBase/api/system/health") {
      Write-Host "Backend already healthy at $ApiBase" -ForegroundColor Green
    }
    else {
      $py = Get-PythonExe
      $backendLog = Join-Path $RunDir "backend_server.log"
      $backendErr = Join-Path $RunDir "backend_server.err.log"
      $script:BackendProcess = Start-Process -FilePath $py -ArgumentList "main.py" -WorkingDirectory $Backend -RedirectStandardOutput $backendLog -RedirectStandardError $backendErr -PassThru -WindowStyle Hidden
      if (!(Wait-Backend $ApiBase $BackendWaitSeconds)) {
        throw "Backend did not become healthy within $BackendWaitSeconds seconds. See $backendLog and $backendErr"
      }
    }
  }

  if (!$SkipLive) {
    $liveScripts = @(
      "scripts\live_validation\e2e_full.py",
      "scripts\live_validation\challenge_deep.py",
      "scripts\live_validation\custom_room_live.py",
      "scripts\live_validation\challenge_room_live.py"
    )
    foreach ($rel in $liveScripts) {
      $scriptPath = Join-Path $Backend $rel
      $stepName = "Live validation: $(Split-Path $rel -Leaf)"
      if (!(Test-Path $scriptPath)) {
        Invoke-Step $stepName { throw "Missing script: $scriptPath" }
        continue
      }
      Invoke-Step $stepName {
        $py = Get-PythonExe
        Invoke-CommandLogged ("live_" + (Split-Path $rel -LeafBase)) $Backend $py @($rel)
      }
    }
  }

  if (!$SkipPostman) {
    Invoke-Step "Postman/Newman API validation" {
      if (!(Get-Command npx -ErrorAction SilentlyContinue)) {
        throw "npx not found. Install Node.js or use -SkipPostman."
      }
      $collection = Join-Path $Root "docs\api\AdaptIQ_Complete_Postman.json"
      if (!(Test-Path $collection)) {
        throw "Postman collection not found at $collection"
      }
      Write-Host "Using collection: $collection"
      $jsonReport = Join-Path $RunDir "newman_report.json"
      Invoke-CommandLogged "postman_newman" $Root "npx" @(
        "--yes", "newman", "run", $collection,
        "--env-var", "baseUrl=$ApiBase",
        "-r", "cli,json",
        "--reporter-json-export", $jsonReport
      )
    }
  }

  if ((Test-Path $Frontend) -and !$SkipFrontend) {
    Invoke-Step "Frontend lint" {
      if (!(Get-Command npm -ErrorAction SilentlyContinue)) {
        throw "npm not found. Install Node.js or use -SkipFrontend."
      }
      if ($Install) {
        Invoke-CommandLogged "frontend_npm_install" $Frontend "npm" @("install")
      }
      Invoke-CommandLogged "frontend_lint" $Frontend "npm" @("run", "lint")
    }

    Invoke-Step "Frontend build" {
      Invoke-CommandLogged "frontend_build" $Frontend "npm" @("run", "build")
    }

    if (!$SkipE2E) {
      Invoke-Step "Playwright E2E tests" {
        if (Test-Path (Join-Path $Frontend "playwright.config.ts")) {
          Invoke-CommandLogged "playwright_tests" $Frontend "npx" @("playwright", "test")
        }
        else {
          throw "No playwright.config.ts found."
        }
      }
    }
  }
}
finally {
  if ($BackendProcess -and !$BackendProcess.HasExited) {
    Write-Host "Stopping backend process $($BackendProcess.Id)"
    Stop-Process -Id $BackendProcess.Id -Force
  }

  $summaryPath = Join-Path $RunDir "summary.txt"
  $Summary | Set-Content -Path $summaryPath -Encoding UTF8

  $mdPath = Join-Path $RunDir "summary.md"
  $md = @()
  $md += "# AdaptIQ full audit summary"
  $md += ""
  $md += "- Run: $RunStamp"
  $md += "- API: $ApiBase"
  $md += "- Passed: $Passes"
  $md += "- Failed: $Failures"
  $md += "- Output folder: $RunDir"
  $md += ""
  $md += "## Results"
  foreach ($line in $Summary) { $md += "- $line" }
  $md | Set-Content -Path $mdPath -Encoding UTF8

  $auditPath = Join-Path $RunDir "audit.json"
  $auditPayload = [ordered]@{
    run = $RunStamp
    api_base = $ApiBase
    root = $Root
    passed = $Passes
    failed = $Failures
    output_dir = $RunDir
    steps = @($Audit)
  }
  ($auditPayload | ConvertTo-Json -Depth 6) | Set-Content -Path $auditPath -Encoding UTF8

  Write-Host "`nSummary written to: $summaryPath"
  Write-Host "Markdown summary: $mdPath"
  Write-Host "Audit JSON: $auditPath"

  if ($Failures -gt 0) {
    Write-Host "`nAudit finished with $Failures failed step(s), $Passes passed." -ForegroundColor Red
    exit 1
  }
  else {
    Write-Host "`nAudit finished successfully ($Passes steps)." -ForegroundColor Green
    exit 0
  }
}
