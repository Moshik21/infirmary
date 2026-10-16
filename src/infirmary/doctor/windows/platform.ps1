$ErrorActionPreference='SilentlyContinue'
# The Windows platform under the lanes (catalogue 23 to 27), read-only: firmware virtualization and the hypervisor, hypervisor
# launch events, restarts and the updates around them, a pending restart, the update policy. Nothing here changes a setting,
# a service, a scheduled task or a file, and wsl.exe is never called.
function E($t){if($t){[int64]([DateTimeOffset]$t).ToUnixTimeSeconds()}else{$null}}
function V($k,$names){$o=[ordered]@{};$i=Get-ItemProperty $k;if($i){foreach($n in $names){$x=$i.$n;if($null -ne $x){$o[$n]=$x}}};$o}
function W($q){@(Get-WinEvent -FilterHashtable $q -MaxEvents 300)}
$since=(Get-Date).AddHours(-__LOOKBACK_H__)
$evok=[bool](Get-WinEvent -LogName System -MaxEvents 1)
$os=Get-CimInstance Win32_OperatingSystem
$cs=Get-CimInstance Win32_ComputerSystem
$fwv=@(Get-CimInstance Win32_Processor|%{$_.VirtualizationFirmwareEnabled}|?{$null -ne $_})
$fw=$null;if($fwv.Count -gt 0){$fw=[bool]($fwv -contains $true)}
$dg=Get-CimInstance -Namespace root\Microsoft\Windows\DeviceGuard -ClassName Win32_DeviceGuard
$vmp=Get-CimInstance Win32_OptionalFeature -Filter "Name='VirtualMachinePlatform'"
$lx=@(Get-ChildItem 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Lxss'|%{(Get-ItemProperty $_.PSPath).Version})
$svc=Get-Service WSLService
# hypervisor starts (1) and launch failures (41), the virtualization-based security check at boot (Kernel-Boot 124), boots (Kernel-General 12)
$hv=@(W @{LogName='System';ProviderName='Microsoft-Windows-Hyper-V-Hypervisor';Id=1,41;StartTime=$since}|Select-Object -First 60|%{[pscustomobject]@{at=(E $_.TimeCreated);id=$_.Id}})
$vsm=@(W @{LogName='System';ProviderName='Microsoft-Windows-Kernel-Boot';Id=124;StartTime=$since}|Select-Object -First 20|%{E $_.TimeCreated})
$boots=@(W @{LogName='System';ProviderName='Microsoft-Windows-Kernel-General';Id=12;StartTime=$since}|Select-Object -First 30|%{E $_.TimeCreated})
# restarts someone asked for (User32 1074: only the program's file name, the reason, its code and the kind are kept; no user or computer name)
$rs=@(W @{LogName='System';ProviderName='User32';Id=1074;StartTime=$since}|Select-Object -First 30|%{$q=$_.Properties;[pscustomobject]@{at=(E $_.TimeCreated);by=([IO.Path]::GetFileName([string]$q[0].Value) -replace '\s*\(.*\)\s*$','');reason=[string]$q[2].Value;code=[string]$q[3].Value;kind=[string]$q[4].Value}})
# restarts nobody asked for: the previous system stop was unexpected (EventLog 6008), the system rebooted without a clean stop (Kernel-Power 41)
$un=@(W @{LogName='System';ProviderName='EventLog';Id=6008;StartTime=$since}|Select-Object -First 20|%{[pscustomobject]@{at=(E $_.TimeCreated);id=6008}})
$un+=@(W @{LogName='System';ProviderName='Microsoft-Windows-Kernel-Power';Id=41;StartTime=$since}|Select-Object -First 20|%{[pscustomobject]@{at=(E $_.TimeCreated);id=41}})
# updates installed (19) or failed (20); Store apps and the antivirus definitions never restart a machine and are left out
$up=@(W @{LogName='System';ProviderName='Microsoft-Windows-WindowsUpdateClient';Id=19,20;StartTime=$since}|%{$q=$_.Properties;if($_.Id -eq 19){$t=[string]$q[0].Value;$sv=[string]$q[3].Value;$er=$null}else{$t=[string]$q[1].Value;$sv=[string]$q[4].Value;$er='0x{0:X8}' -f ([int64]$q[0].Value -band 4294967295)};if($sv -ne '855e8a7c-ecb4-4ca3-b045-1dfa50104289' -and $t -notmatch 'KB2267602'){[pscustomobject]@{at=(E $_.TimeCreated);id=$_.Id;title=$t.Substring(0,[math]::Min(120,$t.Length));err=$er}}}|Select-Object -First 60)
# a restart pending: the two keys the Windows Update agent and servicing stack set (strong), and softer signs (weak)
$cbs='HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing'
$strong=@();$weak=@()
if(Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired'){$strong+='windows-update'}
if(Test-Path ($cbs+'\RebootPending')){$strong+='servicing'}
if((Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager').PendingFileRenameOperations){$weak+='file_renames'}
if((Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Updates').UpdateExeVolatile){$weak+='update_exe_volatile'}
$cn1=(Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\ComputerName\ActiveComputerName').ComputerName
$cn2=(Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\ComputerName\ComputerName').ComputerName
if($cn1 -and $cn2 -and $cn1 -ne $cn2){$weak+='computer_rename'}
# the update policy, as information: named values only
$ux=V 'HKLM:\SOFTWARE\Microsoft\WindowsUpdate\UX\Settings' @('ActiveHoursStart','ActiveHoursEnd','SmartActiveHoursState','PauseUpdatesExpiryTime','PauseQualityUpdatesEndTime','PauseFeatureUpdatesEndTime')
$po=V 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate' @('SetActiveHours','ActiveHoursStart','ActiveHoursEnd','ExcludeWUDriversInQualityUpdate','DeferQualityUpdates','DeferQualityUpdatesPeriodInDays','DeferFeatureUpdates','DeferFeatureUpdatesPeriodInDays','SetComplianceDeadline','ConfigureDeadlineForQualityUpdates','ConfigureDeadlineGracePeriod','ConfigureDeadlineNoAutoReboot')
$au=V 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU' @('NoAutoUpdate','AUOptions','NoAutoRebootWithLoggedOnUsers','ScheduledInstallDay','ScheduledInstallTime','AlwaysAutoRebootAtScheduledTime')
$dr=V 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\DriverSearching' @('SearchOrderConfig')
$pz=V 'HKLM:\SOFTWARE\Microsoft\WindowsUpdate\UpdatePolicy\Settings' @('PausedQualityStatus','PausedFeatureStatus')
$off=[int][TimeZoneInfo]::Local.GetUtcOffset((Get-Date)).TotalMinutes
[pscustomobject]@{v=1;computer=$env:COMPUTERNAME;now=(E (Get-Date));boot=(E $os.LastBootUpTime);tz_offset_min=$off;
 virt=[pscustomobject]@{hypervisor_present=$cs.HypervisorPresent;firmware_enabled=$fw;vbs_status=$dg.VirtualizationBasedSecurityStatus;vm_platform=$vmp.InstallState};
 distros=[pscustomobject]@{v2=@($lx|?{$_ -eq 2}).Count;v1=@($lx|?{$_ -eq 1}).Count;service=[string]$svc.Status};
 events=[pscustomobject]@{read=$evok;hypervisor=$hv;vsm_failed=$vsm;boots=$boots;restarts=$rs;unexpected=$un;updates=$up};
 reboot=[pscustomobject]@{read=(Test-Path $cbs);strong=$strong;weak=$weak};
 policy=[pscustomobject]@{ux=$ux;policy=$po;au=$au;driver_search=$dr;paused=$pz}}|ConvertTo-Json -Compress -Depth 6|%{'INFIRMARY_PLATFORM '+$_}
