$ErrorActionPreference='SilentlyContinue'
$o=Get-CimInstance Win32_OperatingSystem
$p=@(Get-CimInstance Win32_Process)
$now=Get-Date
$c=$now.AddHours(-__HOURS__)
$old=@($p|?{$_.CreationDate -lt $c})
$vm=@($p|?{$_.Name -like 'vmmem*'})
$w=@();$f="$env:USERPROFILE\.wslconfig"
if(Test-Path $f){$w=@(Get-Content $f|?{$_ -match '^\s*(memory|swap|processors)\s*='}|%{"$_"})}
$m=Get-CimInstance Win32_PerfFormattedData_PerfOS_Memory
$cpus=[int]$env:NUMBER_OF_PROCESSORS
$byId=@{};$kids=@{}
foreach($x in $p){$byId[[int]$x.ProcessId]=$x;$k=[int]$x.ParentProcessId;if(-not $kids.ContainsKey($k)){$kids[$k]=New-Object System.Collections.ArrayList};[void]$kids[$k].Add($x)}
# the WSL VM seen from Windows: CPU over a short sample (read-only, wsl.exe is never called)
$vmcpu=$null
if($vm.Count -gt 0){
  $vm1=@(Get-CimInstance Win32_Process -Filter "Name like 'vmmem%'")
  $sw=[Diagnostics.Stopwatch]::StartNew()
  $t0=0.0;foreach($x in $vm1){$t0+=[double]$x.KernelModeTime+[double]$x.UserModeTime}
  Start-Sleep -Milliseconds 700
  $vm2=@(Get-CimInstance Win32_Process -Filter "Name like 'vmmem%'")
  $t1=0.0;foreach($x in $vm2){$t1+=[double]$x.KernelModeTime+[double]$x.UserModeTime}
  $sec=$sw.Elapsed.TotalSeconds
  if($cpus -gt 0 -and $sec -gt 0 -and $vm2.Count -eq $vm1.Count){$vmcpu=[math]::Round((($t1-$t0)/1e7)/$sec/$cpus*100,1)}
}
$svc=Get-Service WSLService
# processes with no live parent: servers and browsers a cut-off job left behind (parent gone, or its pid reused by a younger process)
$names=__LEAK_NAMES__
$marks=__MARKERS__
$sessn=__SESSION_NAMES__
$minAge=$now.AddMinutes(-__MIN_AGE_MIN__)
$sessAge=$now.AddHours(-__SESSION_HOURS__)
$rows=New-Object System.Collections.ArrayList
$sess=@{}
foreach($x in $p){
  $n=([string]$x.Name).ToLower()
  $isLeak=$names -contains $n
  $isSess=$sessn -contains $n
  if(-not ($isLeak -or $isSess)){continue}
  if($x.ProcessId -le 4){continue}
  $par=$byId[[int]$x.ParentProcessId]
  $st='alive'
  if($null -eq $par){$st='dead'}elseif($par.CreationDate -gt $x.CreationDate){$st='recycled'}
  if($st -eq 'alive'){continue}
  if($isSess -and $x.CreationDate -lt $sessAge){
    if(-not $sess.ContainsKey($n)){$sess[$n]=@{n=0;mb=0}}
    $sess[$n].n=$sess[$n].n+1;$sess[$n].mb=$sess[$n].mb+[int]($x.WorkingSetSize/1MB)
  }
  if(-not $isLeak -or $x.CreationDate -gt $minAge){continue}
  $cl=[string]$x.CommandLine
  $tn=0;$tmb=0;$stack=New-Object System.Collections.Stack;$stack.Push([int]$x.ProcessId);$guard=0
  while($stack.Count -gt 0 -and $guard -lt 2000){$guard++;$id=$stack.Pop();if($kids.ContainsKey($id)){foreach($ch in $kids[$id]){if($ch.ProcessId -ne $id){$tn++;$tmb+=[int]($ch.WorkingSetSize/1MB);$stack.Push([int]$ch.ProcessId)}}}}
  $flags=@([regex]::Matches($cl,'(?<=\s)--[A-Za-z0-9-]{2,40}')|%{$_.Value.ToLower()}|Select-Object -Unique|Select-Object -First 8)
  $hay=($cl+' '+[string]$x.ExecutablePath).ToLower()
  $hit=@($marks|?{$hay.Contains($_.ToLower())})
  $sha=''
  if($cl){$sha=([BitConverter]::ToString([Security.Cryptography.SHA1]::Create().ComputeHash([Text.Encoding]::UTF8.GetBytes($cl))) -replace '-','').Substring(0,10).ToLower()}
  [void]$rows.Add([pscustomobject]@{pid=[int]$x.ProcessId;name=$n;born=$x.CreationDate.ToUniversalTime().ToString('s');age_min=[int]($now-$x.CreationDate).TotalMinutes;ws_mb=[int]($x.WorkingSetSize/1MB);parent=$st;ppid=[int]$x.ParentProcessId;flags=$flags;marks=$hit;cmd_sha=$sha;tree_n=$tn;tree_mb=$tmb})
}
$top=@($rows|Sort-Object ws_mb -Descending|Select-Object -First 40)
$sessOut=@();foreach($k in $sess.Keys){$sessOut+=[pscustomobject]@{name=$k;n=$sess[$k].n;mb=$sess[$k].mb}}
# what a job arriving over ssh gets: the login shell, the GPU, the tools by file version (nothing is run)
$ds=(Get-ItemProperty 'HKLM:\SOFTWARE\OpenSSH').DefaultShell
$pp=$byId[[int]$PID];$launch=''
if($pp){$q=$byId[[int]$pp.ParentProcessId];if($q){$launch=[string]$q.Name}}
$gpu=@(Get-CimInstance Win32_VideoController|%{[pscustomobject]@{name=[string]$_.Name}})
$pref=@();$pk=Get-ItemProperty 'HKCU:\Software\Microsoft\DirectX\UserGpuPreferences'
if($pk){foreach($pr in $pk.PSObject.Properties){if($pr.Name -notlike 'PS*'){$pref+=[pscustomobject]@{app=[IO.Path]::GetFileName([string]$pr.Name).ToLower();value=[string]$pr.Value}}}}
$tools=@{}
foreach($t in __TOOLS__){
  $path=$null
  $cm=Get-Command ($t+'.exe') -CommandType Application
  if($cm){$path=@($cm)[0].Source}
  if(-not $path){foreach($rk in @('HKLM:','HKCU:')){$ap=(Get-ItemProperty ($rk+'\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\'+$t+'.exe'))."(default)";if($ap -and (Test-Path $ap)){$path=$ap;break}}}
  if($path){$v=(Get-Item $path).VersionInfo;$ver=[string]$(if($v.ProductVersion){$v.ProductVersion}else{$v.FileVersion});if(-not $ver -and $path -like '*\WindowsApps\*'){$tools[$t]=$null}else{$tools[$t]=$ver}}else{$tools[$t]=$null}
}
$desk=@($p|?{$_.Name -eq 'explorer.exe' -and $_.SessionId -gt 0}).Count
[pscustomobject]@{v=2;host=$env:COMPUTERNAME;os=$o.Caption;total_mb=[int]($o.TotalVisibleMemorySize/1024);avail_mb=[int]$m.AvailableMBytes;commit_pct=[int]$m.PercentCommittedBytesInUse;procs=$p.Count;old_bash=@($old|?{$_.Name -eq 'bash.exe'}).Count;old_sshd=@($old|?{$_.Name -eq 'sshd.exe'}).Count;old_powershell=@($old|?{$_.Name -eq 'powershell.exe'}).Count;vmmem_mb=[int](($vm|Measure-Object WorkingSetSize -Sum).Sum/1MB);vmmem_n=$vm.Count;wslconfig=$w;uptime_h=[math]::Round(((Get-Date)-$o.LastBootUpTime).TotalHours,1);cpus=$cpus;vmmem_cpu_pct=$vmcpu;wsl_service=[string]$svc.Status;caps=[pscustomobject]@{default_shell=[string]$ds;launched_by=$launch;ps_version=$PSVersionTable.PSVersion.ToString();gpu=$gpu;gpu_pref=$pref;tools=$tools;desktop_sessions=$desk};leaks=[pscustomobject]@{scanned=$p.Count;total=$rows.Count;rows=$top;sessions=$sessOut}}|ConvertTo-Json -Compress -Depth 6|%{'INFIRMARY '+$_}
