# Starts the Dhan algo server (if not running), sets paper capital, opens dashboard.
$ErrorActionPreference = "SilentlyContinue"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root
$base = "http://127.0.0.1:5000"
$capital = 150000

function Test-Up { try { Invoke-RestMethod "$base/api/status" -TimeoutSec 3 | Out-Null; $true } catch { $false } }

if (-not (Test-Up)) {
    New-Item -ItemType Directory -Force "$root\logs" | Out-Null
    $log = "$root\logs\server_$(Get-Date -Format yyyyMMdd).log"
    $env:PYTHONIOENCODING = "utf-8"
    Start-Process -FilePath "$root\.venv\Scripts\python.exe" -ArgumentList "-X", "utf8", "app.py" `
        -WorkingDirectory $root -WindowStyle Minimized `
        -RedirectStandardOutput $log -RedirectStandardError "$log.err"
    for ($i = 0; $i -lt 60 -and -not (Test-Up); $i++) { Start-Sleep 2 }
}

if (Test-Up) {
    Invoke-RestMethod "$base/api/set-paper-capital" -Method Post -ContentType 'application/json' -Body (@{amount=$capital} | ConvertTo-Json) | Out-Null
    if ($args -notcontains "-nobrowser") { Start-Process $base }
} else {
    Add-Type -AssemblyName PresentationFramework
    [System.Windows.MessageBox]::Show("Dhan Algo server failed to start. Check logs folder.") | Out-Null
}
