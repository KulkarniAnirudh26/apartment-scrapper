<#
.SYNOPSIS
    Register (or remove) the twice-daily apartment-scraper task.

.DESCRIPTION
    Creates one scheduled task, "RentScrape", with two daily triggers. Each run
    executes  run.bat --visualize --email  : scrape, rebuild the charts and
    explorer.html, then email the shortlist.

    Must be run from an ELEVATED PowerShell - registering an S4U task (one that
    runs whether or not you are logged on, without storing your password)
    requires administrator rights.

    Settings are chosen for a laptop. Task Scheduler's defaults refuse to start
    a task on battery and kill a running one when you unplug; both are
    overridden here, otherwise runs would vanish silently.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File schedule.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File schedule.ps1 -RunNow

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File schedule.ps1 -Remove
#>
[CmdletBinding()]
param(
    [string]$TaskName    = "RentScrape",
    [string]$MorningTime = "08:30",
    [string]$EveningTime = "20:30",
    [switch]$RunNow,
    [switch]$Remove
)

$ErrorActionPreference = "Stop"
$here = $PSScriptRoot
if (-not $here) { $here = Split-Path -Parent $MyInvocation.MyCommand.Path }

# --- elevation -------------------------------------------------------------
$id = [Security.Principal.WindowsIdentity]::GetCurrent()
$isAdmin = (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "This needs an elevated PowerShell." -ForegroundColor Red
    Write-Host "  Start menu -> type 'powershell' -> Ctrl+Shift+Enter, then:"
    Write-Host "  powershell -ExecutionPolicy Bypass -File `"$here\schedule.ps1`""
    exit 1
}

# --- remove ----------------------------------------------------------------
if ($Remove) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "Removed scheduled task '$TaskName'." -ForegroundColor Green
    } else {
        Write-Host "No scheduled task named '$TaskName' to remove."
    }
    exit 0
}

# --- sanity ----------------------------------------------------------------
foreach ($f in @("run.bat", "scraper.py", "notify.py", "venv\Scripts\python.exe")) {
    if (-not (Test-Path (Join-Path $here $f))) {
        Write-Host "Missing $f in $here - is this the project folder?" -ForegroundColor Red
        exit 1
    }
}
if (-not (Test-Path (Join-Path $here ".env"))) {
    Write-Host "WARNING: no .env - the run will scrape but cannot email." -ForegroundColor Yellow
}

# --- build -----------------------------------------------------------------
$action = New-ScheduledTaskAction -Execute (Join-Path $here "run.bat") `
                                  -Argument "--visualize --email" `
                                  -WorkingDirectory $here

$triggers = @(
    (New-ScheduledTaskTrigger -Daily -At (Get-Date $MorningTime)),
    (New-ScheduledTaskTrigger -Daily -At (Get-Date $EveningTime))
)

# AllowStartIfOnBatteries + DontStopIfGoingOnBatteries: without BOTH, a laptop
#   silently skips or kills runs whenever it is unplugged.
# StartWhenAvailable: a run missed while the machine was off fires when it wakes.
# WakeToRun: wakes the machine at 08:30/20:30 - but only on AC, because DC wake
#   timers are disabled on this machine (powercfg SUB_SLEEP RTCWAKE).
# RunOnlyIfNetworkAvailable: with no network the scrape would still "succeed"
#   and append ~174 error rows into the append-only rent_history.csv.
# IgnoreNew: never let two scrapes overlap.
# ExecutionTimeLimit 4h: a measured full run took 1h55m (174 sites, ~40s each
#   with the Playwright fallback). 2h would kill it just short of the finish.
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -WakeToRun `
    -RunOnlyIfNetworkAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 4) `
    -RestartCount 2 `
    -RestartInterval (New-TimeSpan -Minutes 15) `
    -Hidden

# S4U: runs whether or not you are logged on, with no password stored anywhere.
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" `
                                        -LogonType S4U -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName `
    -Action $action -Trigger $triggers -Settings $settings -Principal $principal `
    -Description "Scrape 1B1B rents, rebuild charts, email the shortlist. Twice daily." `
    -Force | Out-Null

# --- report ----------------------------------------------------------------
$task = Get-ScheduledTask -TaskName $TaskName
$info = $task | Get-ScheduledTaskInfo
$s = $task.Settings

Write-Host ""
Write-Host "Registered '$TaskName'" -ForegroundColor Green
Write-Host "  runs        : run.bat --visualize --email"
Write-Host "  in          : $here"
Write-Host "  at          : $MorningTime and $EveningTime daily"
Write-Host "  as          : $env:USERDOMAIN\$env:USERNAME (S4U - logged on or not)"
Write-Host "  next run    : $($info.NextRunTime)"
Write-Host ""
Write-Host "  the settings that matter on a laptop:"
Write-Host "    start on battery      : $(-not $s.DisallowStartIfOnBatteries)"
Write-Host "    keep running unplugged: $(-not $s.StopIfGoingOnBatteries)"
Write-Host "    catch up missed runs  : $($s.StartWhenAvailable)"
Write-Host "    wake to run           : $($s.WakeToRun)  (on AC only - DC wake timers are off)"
Write-Host ""
Write-Host "  a run takes 15-45 min. Watch it with:"
Write-Host "    Get-Content `"$here\run.log`" -Wait -Tail 20"
Write-Host "  remove it with:"
Write-Host "    powershell -ExecutionPolicy Bypass -File `"$here\schedule.ps1`" -Remove"

if ($RunNow) {
    Start-ScheduledTask -TaskName $TaskName
    Write-Host ""
    Write-Host "Started a run now." -ForegroundColor Green
}
