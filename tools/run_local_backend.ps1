# Run the website, administration, downloads and ranking protocol on one port.
[CmdletBinding()]
param(
    [ValidateRange(1, 65535)][int]$Port = 8070,
    [string]$AdminToken = $env:DIB_SITE_ADMIN_TOKEN,
    [switch]$LaunchClient,
    [switch]$OpenBrowser
)
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Python environment is missing. Run build.ps1 first.' }
$dataDir = Join-Path $projectRoot 'website\data'
New-Item -ItemType Directory -Path $dataDir -Force | Out-Null
$outLog = Join-Path $dataDir 'backend.log'
$errorLog = Join-Path $dataDir 'backend-error.log'
$origin = "http://127.0.0.1:$Port"
$portCheck = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $Port)
try { $portCheck.Start() } catch { throw "Port $Port is already in use. Choose another -Port or stop the existing backend." }
finally { $portCheck.Stop() }
$previousToken = $env:DIB_SITE_ADMIN_TOKEN
try {
    if ($AdminToken) { $env:DIB_SITE_ADMIN_TOKEN = $AdminToken }
    $backend = Start-Process -FilePath $python -ArgumentList @('-u', '-m', 'website.server', '--host', '127.0.0.1', '--port', "$Port") -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput $outLog -RedirectStandardError $errorLog -PassThru
} finally { $env:DIB_SITE_ADMIN_TOKEN = $previousToken }
$ready = $false
for ($attempt = 0; $attempt -lt 40; $attempt++) {
    $backend.Refresh()
    if ($backend.HasExited) { throw "Backend exited. See $errorLog" }
    try {
        $health = Invoke-RestMethod -Uri "$origin/healthz" -TimeoutSec 1
        if ($health.ok -and $health.website -and $health.ranking) { $ready = $true; break }
    } catch { Start-Sleep -Milliseconds 250 }
}
if (-not $ready) {
    Stop-Process -Id $backend.Id -Force -ErrorAction SilentlyContinue
    throw "Backend did not become ready. See $errorLog"
}
[System.IO.File]::WriteAllText((Join-Path $dataDir 'backend.pid'), [string]$backend.Id)
if ($LaunchClient) {
    $exe = Get-ChildItem -LiteralPath (Join-Path $projectRoot 'dist') -Filter 'DesktopIconBilliards-*-win-x64.exe' | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $exe) { throw 'No desktop executable found in dist. The backend is running.' }
    $previousRank = $env:DIB_RANK_URL
    $previousWebsite = $env:DIB_WEBSITE_URL
    try {
        $env:DIB_RANK_URL = $origin
        $env:DIB_WEBSITE_URL = $origin
        Start-Process -FilePath $exe.FullName -WorkingDirectory $exe.DirectoryName -WindowStyle Hidden | Out-Null
    } finally {
        $env:DIB_RANK_URL = $previousRank
        $env:DIB_WEBSITE_URL = $previousWebsite
    }
}
if ($OpenBrowser) { Start-Process "$origin/admin" }
[pscustomobject]@{
    BackendPid = $backend.Id
    WebsiteUrl = "$origin/"
    AdminUrl = "$origin/admin"
    RankingUrl = $origin
    TokenFile = if ($AdminToken) { 'DIB_SITE_ADMIN_TOKEN supplied' } else { Join-Path $dataDir 'admin-token.txt' }
    LogFile = $outLog
}
