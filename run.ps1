<#
Docs Health - Windows helper.

  .\run.ps1 setup      # create venv, install backend + frontend deps, create .env files
  .\run.ps1 initdb     # create tables + demo data (uses DATABASE_URL from backend\.env)
  .\run.ps1 test       # backend test suite
  .\run.ps1 demo       # run every sample email through the workflow and print outcomes
  .\run.ps1 api        # start the API in this terminal (default :8000)
  .\run.ps1 web        # build the portal and start the Node.js server in this terminal (default :3000)
  .\run.ps1 dev        # Vite dev server on :5173 (hot reload; the API must be running)

  .\run.ps1 deploy     # build, then start API + portal in the background (logs\*.log)
  .\run.ps1 status     # show the deployed processes and health
  .\run.ps1 stop       # stop the deployed processes

Ports: -ApiPort 8000 -WebPort 3000 (for example: .\run.ps1 deploy -ApiPort 8030 -WebPort 5181)
#>
param(
  [Parameter(Position = 0)][string]$Command = "help",
  [int]$ApiPort = 8000,
  [int]$WebPort = 3000
)
$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$Backend = Join-Path $Root "backend"
$Frontend = Join-Path $Root "frontend"
$Logs = Join-Path $Root "logs"
$Py = Join-Path $Backend ".venv\Scripts\python.exe"
$ApiPid = Join-Path $Logs "api.pid"
$WebPid = Join-Path $Logs "web.pid"

function Ensure-Venv { if (-not (Test-Path $Py)) { throw "Run '.\run.ps1 setup' first." } }

function Stop-Deployed {
  foreach ($file in @($ApiPid, $WebPid)) {
    if (Test-Path $file) {
      $id = [int](Get-Content $file)
      # /T: the venv python.exe and cmd.exe wrappers have child processes.
      if (Get-Process -Id $id -ErrorAction SilentlyContinue) { & taskkill.exe /PID $id /T /F | Out-Null; Write-Host "Stopped PID $id" }
      Remove-Item $file -Force
    }
  }
}

# Start a fully detached background process (no inherited console/pipe handles),
# so the service outlives this script and the terminal that ran it.
function Start-Detached([string]$CommandLine, [string]$WorkingDirectory) {
  $r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
    CommandLine = "cmd.exe /c $CommandLine"; CurrentDirectory = $WorkingDirectory
  }
  if ($r.ReturnValue -ne 0) { throw "Failed to start: $CommandLine (code $($r.ReturnValue))" }
  return $r.ProcessId
}

function Test-PortFree([int]$Port) {
  -not (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
}

function Wait-Http([string]$Url, [int]$Seconds = 30) {
  $deadline = (Get-Date).AddSeconds($Seconds)
  while ((Get-Date) -lt $deadline) {
    try { $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3; if ($r.StatusCode -eq 200) { return $true } } catch { }
    Start-Sleep -Milliseconds 500
  }
  return $false
}

switch ($Command) {
  "setup" {
    Push-Location $Backend
    if (Get-Command uv -ErrorAction SilentlyContinue) { uv venv .venv; uv pip install --python .venv -e ".[dev]" }
    else { py -3 -m venv .venv; & $Py -m pip install -e ".[dev]" }
    if (-not (Test-Path .env)) {
      $secret = & $Py -c "import secrets; print(secrets.token_urlsafe(48))"
      (Get-Content .env.example) -replace '^JWT_SECRET=.*', "JWT_SECRET=$secret" | Set-Content .env -Encoding utf8
      Write-Host "Created backend\.env. Set DATABASE_URL (see README) before '.\run.ps1 initdb'."
    }
    Pop-Location
    Push-Location $Frontend
    npm install
    if (-not (Test-Path .env)) { Copy-Item .env.example .env }
    Pop-Location
  }
  "initdb" { Ensure-Venv; Push-Location $Backend; & $Py -m scripts.init_db --demo; Pop-Location }
  "test"   { Ensure-Venv; Push-Location $Backend; & $Py -m pytest -q; Pop-Location }
  "demo"   { Ensure-Venv; Push-Location $Backend; & $Py -m scripts.run_demo; Pop-Location }
  "api"    { Ensure-Venv; Push-Location $Backend; & $Py -m uvicorn app.main:app --host 127.0.0.1 --port $ApiPort; Pop-Location }
  "web"    {
    Push-Location $Frontend
    npm run build
    if ($?) { $env:PORT = "$WebPort"; $env:API_URL = "http://127.0.0.1:$ApiPort"; node server.js }
    Pop-Location
  }
  "dev"    { Push-Location $Frontend; $env:API_URL = "http://127.0.0.1:$ApiPort"; npm run dev; Pop-Location }

  "deploy" {
    Ensure-Venv
    New-Item -ItemType Directory -Force $Logs | Out-Null
    Stop-Deployed
    foreach ($p in @($ApiPort, $WebPort)) { if (-not (Test-PortFree $p)) { throw "Port $p is already in use. Pick another with -ApiPort / -WebPort." } }

    Write-Host "Building portal..."
    Push-Location $Frontend; npm run build; $ok = $?; Pop-Location
    if (-not $ok) { throw "Frontend build failed" }

    $apiLog = Join-Path $Logs "api.log"
    $webLog = Join-Path $Logs "web.log"
    Start-Detached "`"`"$Py`" -m uvicorn app.main:app --host 127.0.0.1 --port $ApiPort >> `"$apiLog`" 2>&1`"" $Backend |
      Set-Content $ApiPid
    Start-Detached "`"set PORT=$WebPort&& set API_URL=http://127.0.0.1:$ApiPort&& node server.js >> `"$webLog`" 2>&1`"" $Frontend |
      Set-Content $WebPid

    if (-not (Wait-Http "http://127.0.0.1:$ApiPort/api/health")) { Stop-Deployed; throw "API failed to start - see logs\api.log" }
    if (-not (Wait-Http "http://127.0.0.1:$WebPort/api/health")) { Stop-Deployed; throw "Portal failed to start - see logs\web.log" }
    Write-Host "Deployed: portal http://localhost:$WebPort  (API http://127.0.0.1:$ApiPort, docs /docs)"
  }
  "status" {
    foreach ($pair in @(@("API", $ApiPid), @("Portal", $WebPid))) {
      $name, $file = $pair
      if ((Test-Path $file) -and (Get-Process -Id ([int](Get-Content $file)) -ErrorAction SilentlyContinue)) {
        Write-Host "$name running (PID $(Get-Content $file))"
      } else { Write-Host "$name not running" }
    }
  }
  "stop"   { Stop-Deployed }
  default  { Write-Host "Usage: .\run.ps1 setup|initdb|test|demo|api|web|dev|deploy|status|stop [-ApiPort 8000] [-WebPort 3000]" }
}
