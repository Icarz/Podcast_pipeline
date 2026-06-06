<#
.SYNOPSIS
    Create / delete / show the "Icarus Wings Pipeline" Windows scheduled task.

    Runs  main.py --auto  daily at 7:00 PM local time. main.py decides by weekday
    whether to post, skip (already posted), or no-op (non-rotation day), so a
    single daily trigger is all that's needed.

.USAGE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\setup_schedule.ps1 -Action create
    powershell -NoProfile -ExecutionPolicy Bypass -File .\setup_schedule.ps1 -Action show
    powershell -NoProfile -ExecutionPolicy Bypass -File .\setup_schedule.ps1 -Action delete

    NOTE: schtasks' command-line flags can't set the failure-retry, 1-hour
    execution limit, "ignore new instance", or "Start in" working directory.
    Those live only in the Task Scheduler XML, so 'create' registers the task
    from a generated XML via  schtasks /Create /XML.
#>
[CmdletBinding()]
param(
    [ValidateSet('create', 'delete', 'show')]
    [string]$Action = 'show'
)

$ErrorActionPreference = 'Stop'

# --- Task definition (single source of truth) ------------------------------
$TaskName   = 'Icarus Wings Pipeline'
$ProjectDir = $PSScriptRoot                                   # = project root (this file's folder)
$Python     = Join-Path $ProjectDir 'venv\Scripts\python.exe'
$Arguments  = 'main.py --auto'
$RunHour    = 19                                              # 7:00 PM, local time


function New-TaskXml {
    # Anchor the daily schedule to the next upcoming 19:00 local time.
    $start = Get-Date -Hour $RunHour -Minute 0 -Second 0
    if ($start -lt (Get-Date)) { $start = $start.AddDays(1) }
    $startBoundary = $start.ToString('yyyy-MM-ddTHH:mm:ss')

    @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Icarus Wings podcast pipeline - daily 7PM rotation trigger for "main.py --auto". The script itself decides post/skip/no-op by weekday.</Description>
  </RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>$startBoundary</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByDay>
        <DaysInterval>1</DaysInterval>
      </ScheduleByDay>
    </CalendarTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT1H</ExecutionTimeLimit>
    <Priority>7</Priority>
    <RestartOnFailure>
      <Interval>PT15M</Interval>
      <Count>3</Count>
    </RestartOnFailure>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>$Python</Command>
      <Arguments>$Arguments</Arguments>
      <WorkingDirectory>$ProjectDir</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"@
}


switch ($Action) {
    'create' {
        if (-not (Test-Path $Python)) {
            throw "python.exe not found at: $Python (create the venv first)"
        }
        $xmlPath = Join-Path $env:TEMP 'icarus_wings_task.xml'
        New-TaskXml | Out-File -FilePath $xmlPath -Encoding Unicode -Force
        Write-Host "Registering '$TaskName' (daily ${RunHour}:00, run-only-when-logged-on)..."
        # /XML carries every Setting above; /F overwrites a prior definition so
        # re-running 'create' is idempotent.
        schtasks.exe /Create /TN "$TaskName" /XML "$xmlPath" /F
        Remove-Item $xmlPath -Force -ErrorAction SilentlyContinue

        Write-Host ''
        Write-Host '--- Registered. Status / next run: ---'
        schtasks.exe /Query /TN "$TaskName" /FO LIST /V |
            Select-String -Pattern 'TaskName|Status|Next Run Time|Schedule Type|Start Time|Task To Run|Run As User'
    }
    'delete' {
        schtasks.exe /Delete /TN "$TaskName" /F
    }
    'show' {
        schtasks.exe /Query /TN "$TaskName" /FO LIST /V
    }
}
