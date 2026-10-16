# Migration: Fleet Doctor is now Infirmary

The tool was called Fleet Doctor (command `fleet-doctor`, package `fleetdoc`). It is now **Infirmary**. There is no forwarding shim:
the old command, module, variable and path names stop working when a clone is updated past this change. Anything that runs from a
clone of this repository (a crontab line, a LaunchAgent, a Task Scheduler action) points at `bin/fleet-doctor`, which no longer
exists after the update, so it fails every run until it is installed again. Do the steps below once per install, **in this order**.

## Old to new

| Kind | Old | New |
|---|---|---|
| Command | `fleet-doctor` (`bin/fleet-doctor`, `bin/fleet-doctor.cmd`) | `infirmary` (`bin/infirmary`, `bin/infirmary.cmd`) |
| Python package | `fleetdoc` (`src/fleetdoc/`, `python -m fleetdoc`) | `infirmary` (`src/infirmary/`, `python -m infirmary`) |
| Kill switch variable | `FLEET_DOCTOR=off` | `INFIRMARY=off` |
| Other variables | `FLEETDOC_CONFIG`, `FLEETDOC_STATE`, `FLEETDOC_MODE`, `FLEETDOC_DEBUG`, `FLEETDOC_LIVE` | `INFIRMARY_CONFIG`, `INFIRMARY_STATE`, `INFIRMARY_MODE`, `INFIRMARY_DEBUG`, `INFIRMARY_LIVE` |
| Fixture and probe variables | `FLEETDOC_CAPTURE`, `FLEETDOC_MAC_DIR`, `FLEETDOC_PROBES`, `FLEETDOC_PLATFORM`, `FLEETDOC_PLATFORM_ANSWER`, `FLEETDOC_SSH_LOG`, `FLEETDOC_PORT_CLOSED`, `FLEETDOC_PORT_SILENT`, `FLEETDOC_PORT_BANNER`, `FLEETDOC_FM_CLASSIFIER_HOME` | the same names with `INFIRMARY_` |
| Config file | `fleetdoc.json` (`./`, or in an install's directory) | `infirmary.json` |
| User config | `~/.config/fleetdoc/config.json` | `~/.config/infirmary/config.json` |
| Default state directory | `~/.local/state/fleetdoc` (macOS: `~/Library/Application Support/fleetdoc`; Windows: `%LOCALAPPDATA%\fleetdoc`) | the same with `infirmary` |
| Reference adapter's install directory | `<home>/state/fleet-doctor/` (fallback `<home>/fleet-doctor/`) | `<home>/state/infirmary/` (fallback `<home>/infirmary/`) |
| Crontab marker | `# fleet-doctor advice <dir>` | `# infirmary advice <dir>` |
| macOS LaunchAgent | label `org.fleetdoc.advice.<hash>`, file `~/Library/LaunchAgents/org.fleetdoc.advice.<hash>.plist` | label `org.infirmary.advice.<hash>`, file `org.infirmary.advice.<hash>.plist` |
| Status card | tool id `fleet-doctor`, file `fleet-doctor.json`, title "Fleet Doctor" | `infirmary`, `infirmary.json`, "Infirmary" |
| Schema ids | `fleetdoc.verdict`, `.evidence`, `.replay`, `.doctor`, `.finding`, `.plan`, `.advice`, `.install`, `.live-repair`, `.incident`, `.incidents`, `.judged`, `.judged.report` | `infirmary.` plus the same suffix (versions unchanged) |
| Windows probe line, ssh check marker | `FLEETDOC {json}`, `fleetdoc-ok` | `INFIRMARY {json}`, `infirmary-ok` |
| Package archive | `dist/fleet-doctor-<version>.zip` | `dist/infirmary-<version>.zip` |
| Wheel or sdist (`pyproject.toml`) | distribution `fleet-doctor`, console entry `fleet-doctor`, import `fleetdoc` | distribution `infirmary`, console entry `infirmary`, import `infirmary` (`stall-explain` keeps its name) |

Config keys are unchanged (`doctor.*`, `advice.*` including `advice.judged` and `advice.budget`, `adapter_config.*` including `agent_gone_after_s`, `knowledge_mirror` and `dashboard_view`,
`mode`, `host`, `beat`). Only the file and directory names, the schema ids and the variables moved. The state files that hold records keep their
names and move with the state directory: `incidents.jsonl` (the incident rows), `incidents.json` (the open-incident snapshot) and `judged.jsonl`
(the judged ledger), plus `advice-cost.json` (the advise run's back-off ledger, which has no schema id and so carries over unchanged). Their old rows carry the old schema ids. New code still reads the old `incidents.jsonl` rows for their ids, so an incident
already recorded is not written twice; it skips old `judged.jsonl` rows (`infirmary judged` names the count and the reason, `not a judged ledger row
(schema infirmary.judged 1)`), so the judged counts start over from the update. Nothing is deleted.

The unknown cards add no file and no key: `unknowns[]` is a field of `doctor.json` (`infirmary.doctor 1.7`, which is written fresh under the new id), and
their card ids (`unknown:<check>:<class>`) are new. A board that showed the old tool's cards sees them under the new tool id `infirmary` (see the status card row above).

## Linux or WSL: the advice cron

The old entry is one crontab line of this shape, run from a clone of this repository, every ten minutes by default (`--every N` sets another cadence: an install that ran every two minutes has `*/2` in place of `*/10`, and the rest of the line is the same shape):

```
*/10 * * * * <python> <clone>/bin/fleet-doctor advise --quiet --config <home>/state/fleet-doctor/fleetdoc.json > <home>/state/fleet-doctor/advise.log 2>&1 # fleet-doctor advice <home>/state/fleet-doctor
```

The new line, written by `infirmary install`, is:

```
*/10 * * * * <python> <clone>/bin/infirmary advise --quiet --config <home>/state/infirmary/infirmary.json > <home>/state/infirmary/advise.log 2>&1 # infirmary advice <home>/state/infirmary
```

New code does not recognise the old marker, so it neither replaces nor removes the old line. Steps:

1. **Before updating the clone**, with the old command: `fleet-doctor uninstall --home <home> --keep-files`. This removes the
   old crontab line (every other line stays) and keeps the old state directory.
2. Update the clone to this change.
3. Move the state: `mv <home>/state/fleet-doctor <home>/state/infirmary`, then `rm <home>/state/infirmary/fleetdoc.json`
   (the install writes `infirmary.json` fresh; pass any `--config FILE` and `--set` options again).
4. Install again: `infirmary install --home <home> --adapter <adapter> --advice-only` (same options as before). The first advice runs at once.
5. **Verify, one command:** `infirmary installed --home <home>`. It exits 0 only when the new line is in the crontab and the advice is fresh.

If step 1 was missed and the old line is still there, delete the single crontab line ending in `# fleet-doctor advice <dir>` by hand
(`crontab -e`); until then it fails on every run (every ten minutes, or every two for a two-minute install) because `bin/fleet-doctor` is gone.

Live repair is **off** after the move: the old `live-repair.json` and the plans carry old schema ids and are not read. An operator who
had it on runs `infirmary live on --config <home>/state/infirmary/infirmary.json` again. Nothing else is lost; the breaker counts start over.

## macOS

Same order. Old command first: `fleet-doctor uninstall --home <home> --keep-files` boots out the
`org.fleetdoc.advice.<hash>` LaunchAgent and removes `~/Library/LaunchAgents/org.fleetdoc.advice.<hash>.plist`. After updating, move the
state directory as above, run `infirmary install` (same `--every`; the new plist is written with the same keys, the new label and the new paths), and verify with `infirmary installed --home <home>`. A hand-made entry of your own
that calls `fleet-doctor` changes to `infirmary` and the config path to `infirmary.json`.

## Windows

The install docs define no scheduler entry on Windows; if a Task Scheduler task runs the tool, change its action from
`<clone>\bin\fleet-doctor.cmd advise --quiet --config <dir>\fleetdoc.json` to `<clone>\bin\infirmary.cmd advise --quiet --config <dir>\infirmary.json`,
and move `%LOCALAPPDATA%\fleetdoc` to `%LOCALAPPDATA%\infirmary`. Verify with `infirmary status`. The Windows probe script is sent
with each check, so nothing is installed on a host; a probe copy kept by hand keeps printing the old line and is read as `unknown` until replaced.

## Other things that name the old tool

- Shell profiles, wrapper scripts and units that set `FLEET_DOCTOR=off` or any `FLEETDOC_*`: rename the variable.
- A dashboard that keys its tile on the card tool id `fleet-doctor`: it sees `infirmary` as a new tool; the old tile goes stale.
- Anything that reads files by schema id (`fleetdoc.*`): the ids are `infirmary.*` now; recorded files with the old ids are not read as the new schemas.
