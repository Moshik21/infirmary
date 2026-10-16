# The Windows platform under the lanes (catalogue 23 to 27)

A machine that runs lanes in WSL 2 depends on things below WSL that nobody looks at until they break: virtualization switched on in
the firmware, the Windows hypervisor starting at boot, and Windows Update restarting the machine when it decides to. Five advice-only
checks read them, and one of them hands a pending restart to a pool scheduler.

**The incident they come from.** At 04:32 one morning Windows Update restarted a lane machine to finish a batch of driver updates
(platform "System" drivers among them). The machine came back with Intel virtualization switched off in its firmware. The hypervisor
failed to launch at boot (System log, Hyper-V-Hypervisor event 41: "Hypervisor launch failed; Either VMX not present or not enabled in
BIOS"), WSL could not start (`HCS_E_HYPERV_NOT_INSTALLED`), and every lane on the machine was down for about four hours, until a person
restarted into the firmware setup and switched virtualization back on. Nothing was wrong with WSL, the lanes or the network.

## The checks

| # | Check id | Finding kinds | Detects |
| ---: | --- | --- | --- |
| 23 | `firmware-virtualization` | `firmware-virtualization-off`, `hypervisor-not-running` | no hypervisor is running and the processor reports virtualization (Intel VT-x / AMD SVM) disabled in firmware, while a WSL 2 guest is expected (critical; sets `drain`). Twin: virtualization is on in firmware but the hypervisor still did not start (the Virtual Machine Platform feature or the boot's hypervisor launch type is off) |
| 24 | `hypervisor-launch` | `hypervisor-launch-failed`, `hypervisor-launch-failed-recently` | Hyper-V-Hypervisor event 41 at the current boot (critical; sets `drain`), or at an earlier boot within `hypervisor_recent_h` while it runs again now (warning). Either way the finding names the restart that led to the first failed boot and the driver or firmware updates around it |
| 25 | `pending-reboot` | `reboot-pending` | the Windows Update `RebootRequired` or servicing `RebootPending` key is set (warning), with the active hours it may restart outside of and the driver or firmware updates installed since the last boot that take effect at that restart. The same fact goes to `doctor.json` in the shape a Harbor beat carries (below) |
| 26 | `update-restarts` | `update-restart`, `unexpected-restart` | a restart Windows Update or servicing asked for (User32 event 1074 from its programs, or an operating-system planned reason code) within `restart_recent_h`: when, inside or outside active hours, the driver and firmware updates it came with, and whether the boot after it lost the hypervisor. Unexpected restarts (EventLog 6008, Kernel-Power 41) are their own finding |
| 27 | `update-policy` | none: information only | the active hours and who set them, whether drivers come through Windows Update (`ExcludeWUDriversInQualityUpdate`, the device installation setting), deferral and pause, the automatic-update option and whether it restarts while someone is signed in |

**When a WSL 2 guest is expected.** On a WSL guest itself, always. For a Windows host in `doctor.hosts`: when it has a `guest`, or when
its `expect` block says `"wsl2": true` (or `false`), else when a WSL 2 distribution is registered for the probing account. On Windows itself:
when a WSL 2 distribution is registered, or as `doctor.windows.platform.wsl_expected` says. When it cannot be told, the check says `unknown`.

**Look-alikes that are not findings.** With the hypervisor running, Windows reports `VirtualizationFirmwareEnabled` as false (the root
partition sees a virtualized processor); a running hypervisor means virtualization was on at boot, so that is healthy. A restart from the
power menu (`StartMenuExperienceHost.exe`) or by `shutdown.exe` is not Windows Update's. Softer signs of a pending restart (files waiting
to be replaced, a computer rename) are reported in the detail and in `reboot_detail.weak`, never as a pending restart: installers leave
them behind all the time. A Windows Update restart older than `restart_recent_h` is history.

## Where it reads from

- **This machine's own Windows side** (`doctor.windows.platform.local`, `auto` by default): in a WSL guest the Infirmary asks
  `powershell.exe` through Windows interop (on `PATH`, else Windows' own under `/mnt/c`; `doctor.windows.platform.powershell` names
  another); run natively on Windows it asks `powershell`. If interop is off the checks say `unknown` and point at `wsl-interop`. Starting
  PowerShell from WSL costs a few seconds, so these checks run beside the local ones under the remote budget, not the 2 s local budget.
- **Each Windows host in `doctor.hosts`**: the platform script rides on the one ssh probe `hung-host` already sends, so a host is still
  asked once per tick. An older Infirmary's probe answers without the block and the checks say `unknown` for that host.
- When a host in `doctor.hosts` carries the same id as this machine's own Windows side (`local_id`, else this host's id), it is judged
  once, from the local read.

A WSL guest cannot see its own outage: when the hypervisor is gone there is no guest to run the Infirmary in. Catch it from another machine
that lists the Windows host in `doctor.hosts` (with its `guest`), by running the Infirmary natively on the Windows host
(`python bin\infirmary check --only 23,24,25,26,27`), or afterwards from the guest, where `hypervisor-launch` and `update-restarts`
report what happened.

What the script reads, read-only and without elevation: `Win32_ComputerSystem.HypervisorPresent`,
`Win32_Processor.VirtualizationFirmwareEnabled`, `Win32_DeviceGuard`, the `VirtualMachinePlatform` optional feature, the count of
registered WSL distributions per version (no names), the System event log for the last `lookback_h` hours (Hyper-V-Hypervisor 1 and 41,
Kernel-Boot 124, Kernel-General 12, User32 1074 with only the program's file name, the reason, its code and the kind, EventLog 6008,
Kernel-Power 41, WindowsUpdateClient 19 and 20 without Store apps and antivirus definitions), the two restart keys and three softer signs,
and named values of the Windows Update settings and policies. It changes no firmware, boot, policy, service or scheduled-task setting,
installs nothing, writes nothing and never calls `wsl.exe`; the test shims refuse a script that holds a verb that could.

Not read: when Windows will restart (it does not publish that to an unelevated reader: the Update Orchestrator's scheduled restart tasks
were not readable from a normal account on the host this was built on), `bcdedit` (needs elevation), and the WSL error text, which WSL prints to whoever started it and does not
write to any event log (checked on a Windows 11 host after the incident: only Hyper-V-Hypervisor 41 and Kernel-Boot 124 were logged).
`HCS_E_HYPERV_NOT_INSTALLED` is still recognised where the Infirmary sees it: in the error of a guest reached over ssh (`wsl-vm` then says
the VM cannot start rather than "stopped").

## The diagnosis recipe

When every lane on a WSL machine dies at once and WSL will not start, run this at the machine, in PowerShell (nothing here changes
anything):

1. **Is a hypervisor running?** `(Get-CimInstance Win32_ComputerSystem).HypervisorPresent`. `True`: virtualization is fine; look
   elsewhere (`wsl-vm`, memory). `False`: go on.
2. **Is virtualization on in firmware?** `(Get-CimInstance Win32_Processor).VirtualizationFirmwareEnabled`, or `systeminfo` under
   "Hyper-V Requirements" ("Virtualization Enabled In Firmware: No"). Only meaningful while no hypervisor runs (step 1).
3. **Did the hypervisor fail at boot?**
   `Get-WinEvent -FilterHashtable @{LogName='System';ProviderName='Microsoft-Windows-Hyper-V-Hypervisor';Id=41} -MaxEvents 5`.
   Event 41 at the last boot confirms it; event 1 ("Hypervisor successfully started") is the healthy boot.
4. **What restarted the machine just before?**
   `Get-WinEvent -FilterHashtable @{LogName='System';ProviderName='User32';Id=1074} -MaxEvents 5 | Format-List TimeCreated,Message`.
   `MoUsoCoreWorker.exe` or `TrustedInstaller.exe` with "Operating System: ... (Planned)" is Windows Update.
5. **What did that restart install?**
   `Get-WinEvent -FilterHashtable @{LogName='System';ProviderName='Microsoft-Windows-WindowsUpdateClient';Id=19,20} -MaxEvents 30 | Format-Table TimeCreated,Id,Message -Wrap`.
   Driver updates ("... System Driver Update", "... Firmware ...") installed in the minutes around the restart are the usual cause:
   a firmware or platform driver update can reset firmware settings.
6. **Fix, as a person at the machine:** record which lanes the machine held, restart into the firmware setup (UEFI/BIOS), enable Intel
   Virtualization Technology (VT-x) or AMD SVM, save and boot. `HypervisorPresent` is `True` again, Hyper-V-Hypervisor logs event 1,
   WSL starts; relaunch the lanes.
7. **Prevent the next one** (the owner's policy call, see below).

The checks do steps 1 to 5 on every tick and put the chain into one finding; step 6 and 7 stay with a person.

## Preventing the next one: the policy, as information

`update-policy` reports what the machine is set to; it changes nothing. On a machine that runs lanes around the clock:

- **Active hours** cannot cover a whole day (Windows allows at most 18 hours), so a restart will land outside them: at 04:32 in the
  incident. Decide the window yourself: drain the machine before it (`pending-reboot` and Harbor below) and restart deliberately.
- **Driver updates through Windows Update** are the route a firmware or platform driver arrives by. The policy
  `ExcludeWUDriversInQualityUpdate` (Group Policy: Windows Update, "Do not include drivers with Windows Updates") keeps them out; take
  them by hand with a person at the machine who can reach the firmware setup.
- **Deferral and pause** buy time, not safety: the restart still comes, later.

## Hand-off to Harbor (pending restart)

Harbor (a pool scheduler for agent lanes) drains a host before a pending restart. It reads the restart from a `harbor.beat/1` record as
`reboot: {pending, due_epoch, source}`, with `due_epoch` on the host's own clock and `null` when unknown. The Infirmary writes exactly that
object as `windows.<host>.reboot` in `doctor.json` (and in `check --json`): `pending` is `true` when the Windows Update `RebootRequired`
or the servicing `RebootPending` key is set (the two keys Harbor's own Windows beat reads, with the same `source` labels, `Windows Update`
and `Windows servicing`), `false` when neither is, `null` when they could not be read; `due_epoch` is always `null` (Windows does not
publish it). The softer signs are not part of the fact.

This fills one gap: `harbor beat` run inside a WSL guest reads only the guest's `/run/reboot-required`, not the Windows host's keys. A
Infirmary in that guest reads them through interop. To pass the fact on, wrap it in a beat and record it (Harbor is not modified; the Infirmary
calls nothing of it):

```
infirmary check --json --no-write --only pending-reboot | python3 -c '
import json, sys
d = json.load(sys.stdin)
w = d["windows"][d["host"]["id"]]
print(json.dumps({"schema": "harbor.beat/1", "host": "HOST-IN-HOSTS-TOML", "sent_epoch": w["platform"]["now_epoch"],
                  "interval_s": 300, "reboot": w["reboot"]}))' | harbor record -
```

- `host` is a name in Harbor's `hosts.toml`. A Windows restart stops the whole machine, so name the machine's **Windows view**; a beat from
  a WSL view drains only that view (Harbor treats a reboot pending inside a WSL distribution as that distribution's).
- `sent_epoch` is the Windows side's own clock when it was read (`platform.now_epoch`); `interval_s` is how often you send it, so Harbor
  does not call the host late between sends. Any beat also tells Harbor the host is alive, so send it only from a schedule you keep.
- For a Windows host in `doctor.hosts`, use `d["windows"]["<its id>"]`.

## Settings (`doctor.windows.platform`)

| Setting | Default | Meaning |
| --- | --- | --- |
| `local` | `auto` | also read this machine's own Windows side when the Infirmary runs in a WSL guest or on Windows; `off` reads only `doctor.hosts` |
| `local_id` | `null` | the host name its findings carry; `null` is this host's id |
| `powershell` | `null` | the PowerShell to ask; `null` is `powershell.exe` on `PATH`, else Windows' own under `/mnt/c` in a WSL guest |
| `wsl_expected` | `null` | on Windows itself, `true` or `false` overrides "a WSL 2 distribution is registered" |
| `timeout_s` | 30 | the local read is ended after this long |
| `lookback_h` | 72 | how far back the System event log is read |
| `hypervisor_recent_h` / `restart_recent_h` | 24 / 24 | a launch failure, or a Windows Update or unexpected restart, this recent is reported |
| `update_window_h` | 12 | updates installed this long before a restart (or up to 15 minutes after it, while it finishes) count as part of it |

A Windows host's `expect` block takes `"wsl2": true` or `false`. Each check can be switched off with `doctor.disabled`; with all five off
the platform script is not sent at all.
