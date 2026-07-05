<# 
AdaptIQ full validation runner (Windows PowerShell)

Run from the project root:
  powershell -ExecutionPolicy Bypass -File .\scripts\run_all_validation.ps1 -Install

Fast rerun without installing dependencies:
  powershell -ExecutionPolicy Bypass -File .\scripts\run_all_validation.ps1

Optional:
  -SkipDocker      Do not start postgres/redis with docker compose
  -SkipFrontend    Skip frontend build/tests
  -SkipPostman     Skip Newman/Postman collection execution
  -SkipE2E         Skip Playwright tests
#>

param(
  [switch]$Install,
  [switch]$SkipDocker,
  [switch]$SkipFrontend,
  [switch]$SkipPostman,
  [switch]$SkipE2E,
  [string]$ApiBase = "http://localhost:8000",
  [int]$BackendWaitSeconds = 75
)

$ErrorActionPreference = "Stop"

$Root = (Get-Location).Path
$Backend = Join-Path $Root "backend"
$Frontend = Join-Path $Root "frontend"
$RunStamp = Get-Date -Format "yyyyMMdd_HHmmss"
$RunDir = Join-Path $Root "generated\validation_runs\$RunStamp"
New-Item -ItemType Directory -Force -Path $RunDir | Out-Null

$Summary = New-Object System.Collections.Generic.List[string]
$Failures = 0
$BackendProcess = $null

function Add-Summary {
  param([string]$Line)
  $script:Summary.Add($Line) | Out-Null
}

function Invoke-Step {
  param(
    [string]$Name,
    [scriptblock]$Block
  )

  Write-Host "`n=== $Name ===" -ForegroundColor Cyan
  $start = Get-Date
  try {
    & $Block
    $elapsed = [math]::Round(((Get-Date) - $start).TotalSeconds, 2)
    Add-Summary "PASS | $Name | ${elapsed}s"
    Write-Host "PASS: $Name (${elapsed}s)" -ForegroundColor Green
  }
  catch {
    $elapsed = [math]::Round(((Get-Date) - $start).TotalSeconds, 2)
    Add-Summary "FAIL | $Name | ${elapsed}s | $($_.Exception.Message)"
    Write-Host "FAIL: $Name" -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
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
  Add-Summary "AdaptIQ validation run: $RunStamp"
  Add-Summary "Root: $Root"
  Add-Summary "API base: $ApiBase"

  Invoke-Step "Preflight: check folders" {
    if (!(Test-Path $Backend)) { throw "Missing backend folder: $Backend" }
    if (!(Test-Path $Frontend)) { Write-Host "Frontend folder not found; frontend steps will be skipped." -ForegroundColor Yellow }
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
    }
    else {
      Write-Host "Install skipped. Use -Install to install backend requirements."
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

  if (!$SkipPostman) {
    Invoke-Step "Postman/Newman API validation" {
      if (!(Get-Command npx -ErrorAction SilentlyContinue)) {
        throw "npx not found. Install Node.js or use -SkipPostman."
      }

      $collections = Get-ChildItem -Path $Root -Recurse -File -Include "*.postman_collection.json","*collection*.json","*postman*.json" |
        Where-Object { $_.FullName -notmatch "\\node_modules\\" -and $_.FullName -notmatch "\\.venv\\" } |
        Sort-Object FullName

      if ($collections.Count -eq 0) {
        throw "No Postman collection found. Export your Postman collection as JSON into the project, then rerun."
      }

      $collection = $collections[0].FullName
      Write-Host "Using collection: $collection"
      $jsonReport = Join-Path $RunDir "newman_report.json"
      Invoke-CommandLogged "postman_newman" $Root "npx" @("newman", "run", $collection, "--env-var", "baseUrl=$ApiBase", "-r", "cli,json", "--reporter-json-export", $jsonReport)
    }
  }

  if ((Test-Path $Frontend) -and !$SkipFrontend) {
    Invoke-Step "Frontend install/build/tests" {
      if (!(Get-Command npm -ErrorAction SilentlyContinue)) {
        throw "npm not found. Install Node.js or use -SkipFrontend."
      }

      if ($Install) {
        Invoke-CommandLogged "frontend_npm_install" $Frontend "npm" @("install")
      }

      Invoke-CommandLogged "frontend_build" $Frontend "npm" @("run", "build")

      $pkgPath = Join-Path $Frontend "package.json"
      $pkg = Get-Content $pkgPath -Raw | ConvertFrom-Json
      if ($pkg.scripts.test) {
        Invoke-CommandLogged "frontend_tests" $Frontend "npm" @("test", "--", "--run")
      }
      else {
        Write-Host "No frontend test script found; skipping npm test." -ForegroundColor Yellow
      }
    }

    if (!$SkipE2E) {
      Invoke-Step "Playwright E2E tests if configured" {
        if (Test-Path (Join-Path $Frontend "playwright.config.ts")) {
          Invoke-CommandLogged "playwright_tests" $Frontend "npx" @("playwright", "test")
        }
        else {
          Write-Host "No playwright.config.ts found; skipping E2E." -ForegroundColor Yellow
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
  $md += "# AdaptIQ validation summary"
  $md += ""
  $md += "- Run: $RunStamp"
  $md += "- API: $ApiBase"
  $md += "- Output folder: $RunDir"
  $md += ""
  $md += "## Results"
  foreach ($line in $Summary) { $md += "- $line" }
  $md | Set-Content -Path $mdPath -Encoding UTF8

  Write-Host "`nSummary written to: $summaryPath"
  Write-Host "Markdown summary: $mdPath"

  if ($Failures -gt 0) {
    Write-Host "`nValidation finished with $Failures failed step(s)." -ForegroundColor Red
    exit 1
  }
  else {
    Write-Host "`nValidation finished successfully." -ForegroundColor Green
    exit 0
  }
}
