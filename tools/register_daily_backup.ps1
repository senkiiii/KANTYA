param(
    [Parameter(Mandatory=$true)][string]$KeyFile,
    [Parameter(Mandatory=$true)][string]$Storage,
    [Parameter(Mandatory=$true)][string]$Log,
    [string]$Database,
    [string]$Config,
    [string]$At = '02:00',
    [string]$TaskName = 'KANTYA Daily Backup'
)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
if (-not $Database) { $Database = Join-Path $root 'kantya.db' }
if (-not $Config) { $Config = Join-Path $root '.env' }
$Database = (Resolve-Path -LiteralPath $Database).Path
$Config = (Resolve-Path -LiteralPath $Config).Path
$KeyFile = (Resolve-Path -LiteralPath $KeyFile).Path
$Storage = (Resolve-Path -LiteralPath $Storage).Path
$Log = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Log)
$python = (Get-Command python.exe -ErrorAction Stop).Source
$tool = Join-Path $root 'tools\backup_admin.py'
$account = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$arguments = '"{0}" --key-file "{1}" --log "{2}" backup --db "{3}" --config "{4}" --storage "{5}"' -f $tool, $KeyFile, $Log, $Database, $Config, $Storage
$action = New-ScheduledTaskAction -Execute $python -Argument $arguments -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -Daily -At ([datetime]::ParseExact($At,'HH:mm',[Globalization.CultureInfo]::InvariantCulture))
$principal = New-ScheduledTaskPrincipal -UserId $account -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings
