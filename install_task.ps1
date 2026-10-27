# Registers the "autofill-forms" scheduled task: starts "main.py watch" hidden (pythonw)
# when you log on, and restarts it a minute later if it crashes.
# Only run this after "py main.py run --dry-run" did the right thing.
#
# Remove it again with:  Unregister-ScheduledTask -TaskName autofill-forms -Confirm:$false
$ErrorActionPreference = 'Stop'

$root = $PSScriptRoot
$pythonw = Join-Path $root '.venv\Scripts\pythonw.exe'
$main = Join-Path $root 'main.py'
if (-not (Test-Path $pythonw)) { throw "No .venv in $root - set it up first (see README.md)." }

$user = "$env:USERDOMAIN\$env:USERNAME"
$action = New-ScheduledTaskAction -Execute $pythonw -Argument "`"$main`" watch" -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName 'autofill-forms' -Action $action -Trigger $trigger -Settings $settings `
    -Principal $principal -Description 'Answers FINKI Moodle midterm/lab forms announced by email.' -Force | Out-Null
Start-ScheduledTask -TaskName 'autofill-forms'
Write-Output "Registered and started. Log: $root\logs\autoform.log"
