# Troubleshooting

| You see | Cause and fix |
| --- | --- |
| `stall-explain: no adapter 'x' (looked in ...)` | `--set adapter=x` needs `x/source.py` in a directory listed in `adapter_paths` |
| an `adapter` row in `check` with state `error` (exit 50), or `incidents: not read: adapter X not found` in `advise` | the adapter named by `adapter` is in none of the directories listed in the row (the entries of `adapter_paths`, then an `adapters/` directory under the checkout, if you made one), or it failed to load (the row says which). Fix the name or `adapter_paths`; until then the adapter's checks do not run, and that is not a pass |
| `the files source needs files.lanes ...` | no lanes file is configured; `--set files.lanes=PATH`, or try `--demo` |
| `--set x.y: unknown setting 'y'` | the name is not in config DEFAULTS (typos are errors, not silently kept); `adapter_config.*` is free-form |
| `no state directory at ...` | an adapter could not find its data; check its README for the setting (for example `adapter_config.home`) |
| everything is `unknown` with `evidence incomplete` | a reader failed or timed out; see `sources` in `--json`; the cap is `hard_cap_ms` |
| `running-long [host-unverified]` | the job's host has no fresh beat or canary evidence; set the adapter's beat directory, or accept the qualifier |
| `unknown [stale-declaration]` | the lane's last declaration (a wait, or done, failed, blocked, needs-decision) is older than the machine's last boot or the lane's newest agent session (or the source could not date those), so it is not evidence of a wait or of a supervisor-owed move; look at the lane |
| `unknown`: "the lane map at the alarm's moment cannot be reconstructed" | the lane's record is gone or was replaced by a relaunch, and the ledger does not show it cleaned up by then; it is never read as `torn-down` |
| `thinking [busy-attested]` | the only evidence is the watcher's record that a doorbell was deferred because the agent was busy; no activity sample corroborates it |
| CPU and process-tree fields empty | no `/proc` on this OS; verdicts from files still work |
| `stall-explain is off (kill switch)` | `infirmary on`, or unset `INFIRMARY` |
| a TOML config is rejected | TOML needs Python 3.11+ (`tomllib`); use JSON on older Pythons |
| a check says `unknown` | it could not read what it needs (no `/proc` and no `ps`, or no memory reading on this OS); the detail says which. It is never reported as fine |
| the first `infirmary check` on a new or small machine is `CRITICAL` (exit 30) with `disk-low` and a `drain` flag, though nothing is wrong | the free-space defaults are first cuts from one fleet's recorded incidents (`doctor.disk.warn_gb` 40, `crit_gb` 15, and 15% and 7% of the volume), so a small disk, a RAM-backed `/tmp` or a home on a tmpfs reads as critical (for example "home 6.9 GB free of 12 GB"). The `drain` flag only records the word; nothing is stopped. Set thresholds that fit the machine, for example `--set doctor.disk.warn_gb=2 --set doctor.disk.crit_gb=1` (or in `infirmary.json`), and run `check` again; [catalogue.md](catalogue.md) lists the thresholds |
| a check says `skip` | nothing to check, with the reason (no hosts configured, no adapter, no sockets); configure it or ignore it |
| `hung-host` finds nothing though a host is down | the host is not in `doctor.hosts`; the Infirmary only probes hosts you list |
| every finding shows "watching" | `doctor.hysteresis` is on and the finding has not been seen for its `for_s` yet; run `check` again later (or `--watch 60`), or set `doctor.hysteresis=false` |
| `ssh-check` reports an additional check, and steers or pushes to that machine are not arriving | ssh to it stops at a sign-in prompt only the operator can finish; the finding carries the URL. Complete it in a browser; [ssh-check.md](ssh-check.md) explains the check period that decides how soon it asks again |
| `ssh-check` says `unknown` | no alias answered, so a prompt could not be ruled out; `hung-host` has the reason for a host listed in `doctor.hosts` |
| `ssh-check` says `skip` | no alias to try: list some in `doctor.ssh_check.aliases`, `doctor.hosts`, or turn on `doctor.ssh_check.from_ssh_config` |
| a Windows host reports `unknown` or an ssh login failure | the key is not usable non-interactively (`BatchMode`); the finding shows the first error line ssh printed |
| `wsl-vm`, `windows-capabilities` or `windows-leaks` say `unknown` | the Windows host did not answer the probe (`hung-host` has the reason), or an older Infirmary's probe answered without the fields (`v` missing); neither is reported as fine |
| `firmware-virtualization` and the other platform checks (23 to 27) say `unknown` | from a WSL guest: `powershell.exe` did not start (Windows interop off: see `wsl-interop`; or not on `PATH` and not under `/mnt/c`: set `doctor.windows.platform.powershell`); for a host in `doctor.hosts`: it did not answer, or an older Infirmary's probe answered without the platform block |
| `firmware-virtualization` is quiet although `VirtualizationFirmwareEnabled` is false | by design: with a hypervisor running Windows reports that flag as false; a running hypervisor means virtualization was on at boot |
| `pending-reboot` is quiet although files wait to be renamed | softer signs (file renames, a computer rename, an update executable) are listed in the detail and `reboot_detail.weak`, never as a pending restart: installers leave them all the time. Only the Windows Update and servicing keys count, as in Harbor's own Windows beat |
| `windows-capabilities` shows facts but no drift | the host has no `expect` block in `doctor.hosts`, so nothing is declared to drift from; `flags.capability_drift` stays `null` |
| a browser or node left behind is not reported by `windows-leaks` | by design a parentless node or chrome is only listed when its command line or path holds a `doctor.windows.leaks.farm_markers` entry (the Infirmary never lists a person's own browser); add your farm's directory as a marker |
| `how` shows a dry-run command but no output | by design: the Infirmary shows the owner's dry run and runs it only with `--dry-runs` |
| `mode must be off or advise` / `live repair is not a configuration setting` | `mode` takes only `off` or `advise`; live repair is turned on per home by the operator with `infirmary live on`, never by configuration or `INFIRMARY_MODE` |
| `doctor.repair.actions.X must be "plan" or "off"` | configuration can hold an action back, never promote or add one |
| a repair is `refused` with `circuit breaker: N findings are open` | many snags at once; look at them first. The floor is `doctor.repair.breaker_open_findings` |
| `circuit breaker: ACTION failed or did not clear its finding N time(s) in a row` | its last runs did not fix anything; read the `repair` rows in `actions.jsonl`, then `infirmary reset ACTION` |
| `rate limit: ...` | the action already ran within the hour; the next check after the hour tries again |
| `repair keeps recurring` | the same finding was repaired 3 times in 24 h; fix the cause, then it is repaired again once the old runs age out |
| a repair is `not-cleared` | the owner exited 0 but the check still finds the snag; nothing is retried until the rate limit allows, and two in a row trip the breaker |
| `approve` says the plan is `stale` | the findings, the argv or the owner's list changed since the plan was written; the next `check` or `plan` writes a fresh one |
| `approve` says another repair holds the lock | one risky action at a time; wait for the other to finish |
| the Infirmary only reports `advised` | live repair is off for this home (the default; `infirmary live` says so and why), or the run used `--no-write`. `infirmary live on` is the operator's switch; a record written for another host, adapter or home does not count here |
| `refused`: `live repair is off for this home` / `was turned off` | `infirmary live off` was run (or the record was removed) before or during the repair; nothing ran |
