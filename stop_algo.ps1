# Stops the Dhan algo server (python app.py in this folder).
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -like "*app.py*" -and $_.ExecutablePath -like "$root*" } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
