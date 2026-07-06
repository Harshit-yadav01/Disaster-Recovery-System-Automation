<#
.SYNOPSIS
    Diagnoses the jumpbox environment to figure out why "git clone" cannot
    reach github.com (port 443).

.DESCRIPTION
    Read-only. Makes NO changes. Collects:
      - OS / PowerShell version
      - Whether git is installed
      - DNS resolution for github.com
      - TCP connectivity to github.com:443
      - Configured system / WinHTTP / git / environment proxies
      - Basic HTTPS reachability test

.NOTES
    Run in the jumpbox PowerShell:
        powershell -ExecutionPolicy Bypass -File .\Diagnose-Jumpbox.ps1
#>

$ErrorActionPreference = 'SilentlyContinue'

function Write-Section($title) {
    Write-Host ""
    Write-Host "==================================================" -ForegroundColor Cyan
    Write-Host " $title" -ForegroundColor Cyan
    Write-Host "==================================================" -ForegroundColor Cyan
}

# ---------------------------------------------------------------------------
Write-Section "1. Machine / OS / PowerShell"
Write-Host "ComputerName : $env:COMPUTERNAME"
Write-Host "User         : $env:USERNAME"
Write-Host "OS           : $((Get-CimInstance Win32_OperatingSystem).Caption) ($((Get-CimInstance Win32_OperatingSystem).Version))"
Write-Host "PSVersion    : $($PSVersionTable.PSVersion)"

# ---------------------------------------------------------------------------
Write-Section "2. Is git installed?"
$git = Get-Command git -ErrorAction SilentlyContinue
if ($git) {
    Write-Host "git found at : $($git.Source)" -ForegroundColor Green
    Write-Host "git version  : $(git --version)"
} else {
    Write-Host "git NOT found on PATH." -ForegroundColor Red
    Write-Host "You must install Git for Windows before you can 'git clone'."
    Write-Host "Download: https://git-scm.com/download/win"
}

# ---------------------------------------------------------------------------
Write-Section "3. DNS resolution for github.com"
$dns = Resolve-DnsName github.com -ErrorAction SilentlyContinue
if ($dns) {
    $dns | Where-Object { $_.IPAddress } | ForEach-Object { Write-Host "Resolved -> $($_.IPAddress)" -ForegroundColor Green }
} else {
    Write-Host "DNS resolution FAILED for github.com." -ForegroundColor Red
    Write-Host "This points to a DNS / offline-network problem, not just a proxy."
}

# ---------------------------------------------------------------------------
Write-Section "4. TCP connectivity to github.com:443"
$tcp = Test-NetConnection -ComputerName github.com -Port 443 -WarningAction SilentlyContinue
if ($tcp) {
    Write-Host "RemoteAddress   : $($tcp.RemoteAddress)"
    Write-Host "TcpTestSucceeded: $($tcp.TcpTestSucceeded)" -ForegroundColor $(if ($tcp.TcpTestSucceeded) { 'Green' } else { 'Red' })
    Write-Host "PingSucceeded   : $($tcp.PingSucceeded)"
}
if (-not $tcp.TcpTestSucceeded) {
    Write-Host "Direct outbound HTTPS to github.com is BLOCKED." -ForegroundColor Red
    Write-Host "=> The jumpbox almost certainly requires a PROXY (see next section)."
}

# ---------------------------------------------------------------------------
Write-Section "5. Proxy configuration"

Write-Host "--- WinHTTP (system) proxy ---"
netsh winhttp show proxy

Write-Host ""
Write-Host "--- WinINET (Internet Options / per-user) proxy ---"
$ie = Get-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings' -ErrorAction SilentlyContinue
Write-Host "ProxyEnable  : $($ie.ProxyEnable)"
Write-Host "ProxyServer  : $($ie.ProxyServer)"
Write-Host "AutoConfigURL: $($ie.AutoConfigURL)"

Write-Host ""
Write-Host "--- Environment variables ---"
Write-Host "HTTP_PROXY  : $env:HTTP_PROXY"
Write-Host "HTTPS_PROXY : $env:HTTPS_PROXY"
Write-Host "NO_PROXY    : $env:NO_PROXY"

Write-Host ""
Write-Host "--- git proxy config ---"
if ($git) {
    $ghttp  = git config --global --get http.proxy
    $ghttps = git config --global --get https.proxy
    Write-Host "git http.proxy  : $(if ($ghttp)  { $ghttp }  else { '(not set)' })"
    Write-Host "git https.proxy : $(if ($ghttps) { $ghttps } else { '(not set)' })"
} else {
    Write-Host "(git not installed - skipped)"
}

# ---------------------------------------------------------------------------
Write-Section "6. HTTPS reachability test (via any configured proxy)"
try {
    $resp = Invoke-WebRequest -Uri "https://github.com" -UseBasicParsing -TimeoutSec 20
    Write-Host "HTTPS GET github.com -> HTTP $($resp.StatusCode)" -ForegroundColor Green
} catch {
    Write-Host "HTTPS GET github.com FAILED: $($_.Exception.Message)" -ForegroundColor Red
}

# ---------------------------------------------------------------------------
Write-Section "SUMMARY / NEXT STEPS"
if ($tcp.TcpTestSucceeded) {
    Write-Host "* Network path to github.com:443 is OPEN. If git still fails, check git's own proxy settings." -ForegroundColor Green
} else {
    Write-Host "* Network path to github.com:443 is CLOSED (blocked or needs a proxy)." -ForegroundColor Yellow
    Write-Host "  1) Ask your network/IT team for the corporate proxy host:port."
    Write-Host "  2) Then configure git to use it, e.g.:"
    Write-Host '        git config --global http.proxy  http://PROXY_HOST:PORT'
    Write-Host '        git config --global https.proxy http://PROXY_HOST:PORT'
    Write-Host "  3) Retry: git clone https://github.com/<org>/<repo>.git"
    Write-Host ""
    Write-Host "  If there is NO proxy and internet is fully blocked, git-over-https"
    Write-Host "  will not work from this jumpbox by any means."
}
Write-Host ""
