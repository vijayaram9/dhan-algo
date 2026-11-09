# Creates Desktop + Start Menu launch buttons and a daily 08:45 auto-start task.
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$ws = New-Object -ComObject WScript.Shell
$targets = @([Environment]::GetFolderPath("Desktop"), "$env:APPDATA\Microsoft\Windows\Start Menu\Programs")
foreach ($dir in $targets) {
    foreach ($s in @(@("Start Dhan Algo","start_algo.ps1","shell32.dll,137"), @("Stop Dhan Algo","stop_algo.ps1","shell32.dll,131"))) {
        $lnk = $ws.CreateShortcut("$dir\$($s[0]).lnk")
        $lnk.TargetPath = "powershell.exe"
        $lnk.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$root\$($s[1])`""
        $lnk.WorkingDirectory = $root
        $lnk.IconLocation = "$env:SystemRoot\System32\$($s[2])"
        $lnk.Save()
    }
}
$action  = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$root\start_algo.ps1`" -nobrowser" -WorkingDirectory $root
$trigger = @((New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 08:45), (New-ScheduledTaskTrigger -AtLogOn))
$set     = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit 0
# Fallback (no admin needed): auto-start at Windows logon via Startup folder
$st = $ws.CreateShortcut("$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\Dhan Algo AutoStart.lnk")
$st.TargetPath = "powershell.exe"
$st.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$root\start_algo.ps1`" -nobrowser"
$st.WorkingDirectory = $root
$st.Save()
"Desktop/Start Menu buttons + Startup auto-launch installed."
try {
    Register-ScheduledTask -TaskName "DhanAlgoAutoStart" -Action $action -Trigger $trigger -Settings $set -Force -ErrorAction Stop | Out-Null
    "Scheduled task 'DhanAlgoAutoStart' (Mon-Fri 08:45) installed."
} catch { "Scheduled task skipped (run this script as Administrator to add the 08:45 weekday schedule)." }
