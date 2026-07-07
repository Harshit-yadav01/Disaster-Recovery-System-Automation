# ============================================================================
#  HPE DR Automation - Jumpbox setup & environment report
#  Run this ON the labrat jumpbox (Windows). It will:
#    1. Report jumpbox environment details
#    2. Check prerequisites (Git, Python) and network reachability
#    3. Clone (or hard-refresh) the GitHub repo so ALL files match remote
#    4. Create the Python venv and install dependencies
#    5. Configure .env for the array (prompts for the password, hidden)
#    6. Run the read-only connectivity test against the array
#
#  Usage (in PowerShell on the jumpbox):
#    powershell -ExecutionPolicy Bypass -File .\Setup-Jumpbox.ps1
# ============================================================================

$ErrorActionPreference = 'Stop'

# ---- Settings --------------------------------------------------------------
$RepoUrl    = 'https://github.com/Harshit-yadav01/Disaster-Recovery-System-Automation.git'
# Primary = source/production array; Recovery = target/DR array.
# Leave $RecoveryIp blank to be prompted at runtime (or to skip the 2nd array).
# Run identify_arrays.py on the jumpbox to confirm which array is truly the
# replication source; swap these two values if needed.
$PrimaryIp  = '10.64.122.99'
$RecoveryIp = '10.64.154.190'
$ArrayPort  = 443
$ArrayUser  = '3paradm'
$InstallDir = Join-Path $HOME 'Desktop'
$RepoDir    = Join-Path $InstallDir 'Disaster-Recovery-System-Automation'
$BackendDir = Join-Path $RepoDir 'backend'

function Section($t) { Write-Host "`n==== $t ====" -ForegroundColor Cyan }
function Ok($t)      { Write-Host "  [OK]   $t" -ForegroundColor Green }
function Warn($t)    { Write-Host "  [WARN] $t" -ForegroundColor Yellow }
function Fail($t)    { Write-Host "  [FAIL] $t" -ForegroundColor Red }

# ---- 1) Jumpbox environment report ----------------------------------------
Section 'JUMPBOX ENVIRONMENT'
Write-Host "  Hostname : $env:COMPUTERNAME"
Write-Host "  Logged in: $env:USERDOMAIN\$env:USERNAME"
try {
    $os = Get-CimInstance Win32_OperatingSystem
    Write-Host "  OS       : $($os.Caption) (Build $($os.BuildNumber))"
} catch { Warn "Could not read OS info" }
Write-Host "  IPv4 addresses:"
Get-NetIPAddress -AddressFamily IPv4 |
    Where-Object { $_.IPAddress -ne '127.0.0.1' } |
    ForEach-Object { Write-Host "     $($_.IPAddress)  ($($_.InterfaceAlias))" }

# ---- 2) Prerequisites ------------------------------------------------------
Section 'PREREQUISITES'
$hasGit = $false
try { $gv = (git --version) 2>&1; if ($LASTEXITCODE -eq 0) { Ok "Git: $gv"; $hasGit = $true } } catch {}
if (-not $hasGit) { Fail 'Git not found - install Git for Windows: https://git-scm.com/download/win' }

$Py = $null
foreach ($cand in @('python', 'py', 'python3')) {
    try {
        $pv = (& $cand --version) 2>&1
        if ($LASTEXITCODE -eq 0) { Ok "Python: $pv (command: $cand)"; $Py = $cand; break }
    } catch {}
}
if (-not $Py) { Fail 'Python not found - install Python 3.11+: https://www.python.org/downloads/windows/' }

# ---- 3) Network reachability ----------------------------------------------
Section 'NETWORK REACHABILITY'
$gh = Test-NetConnection github.com -Port 443 -WarningAction SilentlyContinue
if ($gh.TcpTestSucceeded) { Ok 'github.com:443 reachable (can clone)' }
else { Warn 'github.com not reachable - jumpbox may be isolated; you may need a proxy or to copy the repo over RDP' }

$arr = Test-NetConnection $PrimaryIp -Port $ArrayPort -WarningAction SilentlyContinue
if ($arr.TcpTestSucceeded) { Ok "Primary array $PrimaryIp`:$ArrayPort reachable (TCP open)" }
else { Fail "Primary array $PrimaryIp`:$ArrayPort NOT reachable - fix networking before the app can read data" }

if (-not $RecoveryIp) {
    $RecoveryIp = (Read-Host '  Enter the RECOVERY (target) array IP (blank = single-array mode)').Trim()
}
if ($RecoveryIp) {
    $rec = Test-NetConnection $RecoveryIp -Port $ArrayPort -WarningAction SilentlyContinue
    if ($rec.TcpTestSucceeded) { Ok "Recovery array $RecoveryIp`:$ArrayPort reachable (TCP open)" }
    else { Warn "Recovery array $RecoveryIp`:$ArrayPort NOT reachable - dashboard will show recovery site as Unreachable" }
} else {
    Warn 'No recovery array given - running in single-array mode.'
}

# Stop if prerequisites are missing
if (-not $hasGit -or -not $Py) { Fail 'Missing prerequisites - install the tools above and re-run.'; return }

# ---- 4) Get code from GitHub (ensure ALL files match remote) --------------
Section 'GET CODE FROM GITHUB'
if (Test-Path (Join-Path $RepoDir '.git')) {
    Write-Host '  Repo already present - refreshing to match GitHub exactly...'
    Set-Location $RepoDir
    git fetch --all --prune
    git checkout main
    # Mirror remote exactly. Note: discards local edits to TRACKED files.
    # Your .env is git-ignored, so it is NOT touched.
    git reset --hard origin/main
} else {
    Write-Host "  Cloning $RepoUrl ..."
    Set-Location $InstallDir
    git clone $RepoUrl
    Set-Location $RepoDir
}

# Verify the working tree matches the remote
git fetch origin -q
$local  = (git rev-parse HEAD).Trim()
$remote = (git rev-parse origin/main).Trim()
$count  = (git ls-files | Measure-Object).Count
if ($local -eq $remote) { Ok "Working tree matches origin/main ($count tracked files, HEAD $($local.Substring(0,7)))" }
else { Warn "Local HEAD ($($local.Substring(0,7))) != origin/main ($($remote.Substring(0,7)))" }
$dirty = git status --porcelain
if ($dirty) { Warn "Uncommitted differences present:`n$dirty" } else { Ok 'No missing or modified tracked files.' }

# ---- 5) Python venv + dependencies ----------------------------------------
Section 'PYTHON ENVIRONMENT'
Set-Location $BackendDir
if (-not (Test-Path '.venv')) { & $Py -m venv .venv }
$VenvPy = Join-Path $BackendDir '.venv\Scripts\python.exe'
& $VenvPy -m pip install --upgrade pip
& $VenvPy -m pip install -r requirements.txt
Ok 'Dependencies installed.'

# ---- 6) Configure .env -----------------------------------------------------
Section 'CONFIGURATION (.env)'
if (-not (Test-Path '.env')) { Copy-Item .env.example .env; Ok 'Created .env from template' }

# Set non-secret values line-by-line (safe against special characters)
$envLines = Get-Content .env | ForEach-Object {
    if ($_ -match '^STORAGE_PROVIDER=')              { 'STORAGE_PROVIDER=alletra' }
    elseif ($_ -match '^ALLETRA_PRIMARY_BASE_URL=')  { "ALLETRA_PRIMARY_BASE_URL=$PrimaryIp" }
    elseif ($_ -match '^ALLETRA_RECOVERY_BASE_URL=') { "ALLETRA_RECOVERY_BASE_URL=$RecoveryIp" }
    elseif ($_ -match '^ALLETRA_USERNAME=')          { "ALLETRA_USERNAME=$ArrayUser" }
    else { $_ }
}
$envLines | Set-Content .env
if ($RecoveryIp) { Ok "Set provider=alletra, primary=$PrimaryIp, recovery=$RecoveryIp, user=$ArrayUser" }
else             { Ok "Set provider=alletra, primary=$PrimaryIp (single-array), user=$ArrayUser" }

Write-Host '  Enter the array password for 3paradm (input hidden; leave blank to edit .env manually later):' -ForegroundColor Yellow
$sec = Read-Host 'Array password' -AsSecureString
if ($sec.Length -gt 0) {
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
    $pw   = [Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr)
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    (Get-Content .env | ForEach-Object {
        if ($_ -match '^ALLETRA_PASSWORD=') { "ALLETRA_PASSWORD=$pw" } else { $_ }
    }) | Set-Content .env
    Ok 'Password written to .env (local only, git-ignored).'
} else {
    Warn 'No password entered - edit .env and set ALLETRA_PASSWORD before starting the app.'
}

# ---- 7) Connectivity test --------------------------------------------------
Section 'ARRAY CONNECTIVITY TEST'
& $VenvPy _conn_test.py

# ---- 8) Next steps ---------------------------------------------------------
Section 'START THE APP'
Write-Host '  If the test above printed system name + capacity, start the server:' -ForegroundColor Cyan
Write-Host "     cd `"$BackendDir`""
Write-Host '     .\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000'
Write-Host '  Then open  http://127.0.0.1:8000/  and log in with  admin / admin'
Write-Host ''
