# Recorded outputs

Real outputs the Infirmary's fixture tests replay through fake tool shims, so the parsers and thresholds are
exercised against what the tools really printed. Host names are replaced with stand-ins; numbers are real.

| File | What it is |
| --- | --- |
| `windows-probe.real.txt` | the Windows probe (`src/infirmary/doctor/windows/probe.ps1`) answered by a real Windows 11 host at an ordinary moment |
| `windows-probe.real-low.txt` | the same probe on the same host a few minutes later, when the host had 884 MB of 32 GB available and a 19 GB WSL VM: the near-hang condition the Infirmary exists to catch |
| `windows-probe.v2.real.txt` | the version-2 probe (adds the WSL VM's CPU and service, the process inventory with parent state, capabilities) answered by a real Windows 11 host on 2026-10-09, read-only over ssh; only the host name is replaced: the recording keeps the host's hardware model strings, display adapter and tool and OS versions, and the numbers are real. The `win2-*` answers the fixtures use besides `win2-ok` are derived from it by `fixture.py` (edited numbers and rows) and are written at build time, never stored here |
| `macos-*.txt` | `vm_stat`, `sysctl` and `ps` output written from the documented formats; **not captured on a Mac** (none was reachable), so the macOS parsers are exercised, not proven on hardware |
