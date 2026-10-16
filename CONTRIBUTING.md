# Contributing

Infirmary is a small, pre-1.0 hobby tool, open-sourced because the problems it addresses were real for its author: it explains why a long-running agent lane is quiet (`stall-explain`) and detects the snags a fleet of such lanes runs into (`infirmary`). It is advice-only by default; live repair is an operator's per-home switch and has never been run on a live fleet by the project. It is MIT licensed, see [LICENSE](LICENSE).

**Scope.** Fixes to a wrong verdict or a wrong finding, new checks and incident rules that rest on positive evidence, adapters for other tools, documentation and portability fixes are welcome. Things that turn it into a monitor, an alert router or a restarter, that call a model, or that add third-party dependencies are out of scope.

**How to propose a change.** Open an issue first for anything non-trivial, so the idea can be agreed before you spend time on it. Typos and small fixes can go straight to a pull request. Keep pull requests small and about one thing. Please read the [code of conduct](CODE_OF_CONDUCT.md); questions go through [SUPPORT.md](SUPPORT.md) and vulnerabilities through [SECURITY.md](SECURITY.md).

**Response time.** This is maintained in spare time: issues and pull requests are answered on a best-effort basis, with no promise of when.

**The honesty rule.** The tool never says more than it saw. A benign verdict needs positive evidence, absence of evidence is `unknown`, an unreadable source is said to be unreadable, and the documentation states what has and has not been run (for example, that live repair has never run on a live fleet). A change must keep that true, and its text must not claim more than its tests and fixtures show. See the Honesty section of the [README](README.md).

The house rules below keep the tool small, safe and easy to trust.

1. **Python 3.8+, standard library only.** No third-party packages, no build step, no model call at run time.
2. **A generic core and optional adapters.** Everything a specific tool needs lives in an adapter outside
   the core (a directory listed in `adapter_paths`; the only adapter in this repository, `examples/adapter/files-home/`, is generic); the core never names a tool, a person, a host, an address or a home path.
   `scripts/self-audit` enforces it.
3. **Config-driven.** A new threshold gets a name in `src/infirmary/core/config.py` DEFAULTS, a line in
   docs/verdicts.md saying what it is and where the number came from, and works with `--set`.
4. **Positive evidence only.** A benign verdict needs something that was seen. Absence of evidence is
   `unknown`, never green. If you add a rule, say in docs/verdicts.md what it needs to have seen.
5. **Advice-only never acts, and live repair (the operator's `infirmary live on`, per home, never a default or a setting) acts only through owners.** Nothing here may start, stop, delete or change
   anything outside the tool's own state directory, except an owner's own command run by a repair action (an argv array
   from code, never a shell string, never from configuration). See docs/repairs.md.
6. **Output.** Plain text first, one line per verdict, `--json` for machines, stable exit codes, no
   colour that carries meaning.

## Before you send a change (definition of done)

A change is ready when all three pass: the self-audit, the full suite and `scripts/merged-check`.

```
python3 scripts/test --denylist /path/outside/the/repo/denylist.txt --denylist /path/outside/the/repo/vendor-words.txt
```

That runs the self-audit, the fixture replays and a smoke run. Run it on Linux or WSL2, not on a Mac: on macOS it has written real LaunchAgent plists into
the user's `~/Library/LaunchAgents` and failed checks (see the Platforms section of the README). Passing on your own base is not the whole definition of done: lanes that
each pass alone can fail once both are merged, and there is no hosted CI to notice. **Run `scripts/merged-check` against current
`origin/main` right before you hand off, and again if main moved before the merge.**

```
scripts/merged-check [--head REF_OR_SHA] [--base origin/main] [--pr N] [--denylist FILE ...]
```

It fetches the base, merges the head onto it in a throwaway scratch repository outside your tree (your branch and working tree are never
touched; a conflict fails with the file list), runs `python3 -I scripts/self-audit` and `python3 scripts/test` there (the denylists from
`--denylist`, repeated, or `$MERGED_CHECK_DENYLIST`), prints one line, `merged-check: ok` or `merged-check: FAILED <step>: <first finding>`, and exits
non-zero on any failure. `--pr N` looks the head up with `gh`; `--test-cmd` is for a repository whose suite is another command.
Tests here are behavioural and go
through the public commands only; there are no unit tests of internals and please do not add them. A
new verdict or rule comes with an invented alarm in `examples/replay/build_synthetic.py` and an
expected verdict in `examples/replay/synthetic.expected.json`.

### The private name lists

`scripts/self-audit` has only generic patterns built in (secrets, e-mail addresses, home paths, IP addresses,
the names of private networks). Names that must not appear in the product are kept in plain-text files
**outside** the repository and given with `--denylist FILE`, which can be repeated:

```
python3 scripts/self-audit --denylist /path/outside/the/repo/denylist.txt --denylist /path/outside/the/repo/vendor-words.txt
```

- `denylist.txt` holds your own names: machines, hosts, accounts, projects.
- `vendor-words.txt` holds the names of the tools and vendors this product is built around but must not
  mention, and lives beside the denylist.

Both files have the same format: one word or phrase per line, no patterns, matched case-insensitively on
word boundaries; blank lines and lines starting with `#` are ignored. `scripts/test`, `scripts/merged-check`
and `scripts/build-package` take a repeated `--denylist` too and hand every file to the audit: always pass both,
the denylist and `vendor-words.txt` (for `merged-check`, `$MERGED_CHECK_DENYLIST` may name both, separated by `:`
on Linux and macOS, `;` on Windows).

## Adding a check to the Infirmary

A check is a function `fn(ctx)` returning `ok(detail)`, `unknown(detail)`, `skip(detail)` or `found(findings, detail)`
(`src/infirmary/doctor/registry.py`), registered in a `SPECS` list. Rules, enforced by review and by the fixtures:

- **Read-only, always.** Read files, `/proc`, and git read verbs. Start programs only through `core.execute`
  (argv arrays, a timeout). A dry run is attached to a finding (`finding.how`) and shown; it runs only under `--dry-runs`.
- **`ok` needs something seen.** An unreadable source is `unknown`; an inapplicable check is `skip` with a reason.
- **A finding names its kind, its key, its evidence and its owner**, and says in `retires_with` which upstream change would
  make it unnecessary. Add a `for_s` for the kind if it can be transient.
- **Fixtures.** A new check comes with a scenario in the matching `fixture.py` (generic) or the adapter's
  `*_fixture.py` (see `advice_fixture.py` in `examples/adapter/files-home/fixtures/`): the snag it must find *and* a look-alike it must leave alone. Do not add unit tests.

## Adding an incident rule

A rule for a failure that has actually happened is a check of the incident family ([docs/incidents.md](docs/incidents.md)): register it with
`registry.Spec(..., family="incident")`, build its findings with `finding.card(what, next_step, since=, blocking=, risk=, records=)` (plain words,
the records with their times, and ONE safe next step as text, never a command the Infirmary runs), and answer an input it cannot read with
`registry.unknown(detail, next_step=...)` so the failure to read becomes an unknown row and not silence. A rule whose records date the failure
needs no `for` duration. Its fixtures are a pair: the failure and a healthy look-alike that differs by one fact, in the adapter's
`fixtures/` (see `examples/adapter/files-home/fixtures/advice_fixture.py`); do not add unit tests.

## Adding a repair action

Only in an adapter, and only for a snag that has an owner command. Subclass `infirmary.doctor.repair.Action` and return it
from `hooks(cfg)["actions"]` (docs/repairs.md, "Adding an action"). Rules:

- **Tier 1 only when the owner proves its target safe** and nothing touches a lane's worktree or branch, a session or a pool
  slot; everything else is tier 2 (a plan). When in doubt, tier 2. On macOS any repair that ends in a signal is tier 2.
- **`assess` proves the targets are what the check found**, not just what the owner printed: an empty list, a list above the
  bound, a target the Infirmary did not find, or one that changed since the check (pid, start time, command) is refused.
- **Fixtures, through the public command**: the repair verified, and at least one refusing twin in which the owner's repair
  command provably never ran (a marker the shim would leave). Do not add unit tests.

## Adding an adapter

Start from `examples/adapter/files-home/` (a complete adapter over plain files, with its fixtures): copy the folder, keep it in
`adapter_paths` and install it with
`infirmary install --home HOME --adapter NAME --advice-only --set 'adapter_paths=["<checkout>/examples/adapter"]'`. Copy the contract from
docs/sources.md, put `source.py` and a README in a `<name>/` directory of your own (listed in `adapter_paths`; one in the example folder's shape, in `examples/adapter/<name>/`, has its
`fixtures/*_fixture.py` run by `scripts/test`), and ship a redacted replay fixture with hand labels if you have recorded alarms. For the Infirmary, add `doctor.py` with
`hooks(cfg)` returning `{"checks": [Spec, ...], "disk_inventory": fn, "clobber_roots": fn, "dry_runs": {...}}`
and a `*_fixture.py` (see `advice_fixture.py` in `examples/adapter/files-home/fixtures/`); repairs add `"actions": [Action, ...]` and
`fixtures/repair_fixture.py`. Adapters for a specific tool are never packaged into
the distributable archive.

## Packaging

Two builds are supported; this project does not prefer one.

- **The zip**: `python3 scripts/build-package --denylist /path/outside/the/repo/denylist.txt --denylist /path/outside/the/repo/vendor-words.txt` writes `dist/<name>-<version>.zip` (the commands,
  the package, docs, examples and the community files, never the adapters). It runs the self-audit first and refuses on any finding.
- **A wheel or sdist**: `pyproject.toml` holds metadata and package data (the demo replay and the PowerShell probes; no dependencies, Python 3.8+, MIT, a console entry for each command). The version is
  read from `VERSION` in `src/infirmary/__init__.py`, the single place it is set; change it there and nowhere else. Build with any PEP 517 front end,
  for example `python3 -m build`, or `uv build`. To check a build, install the wheel into a throwaway virtualenv and run `infirmary --help`
  and `stall-explain --version`, then run `stall-explain --demo` and `infirmary check --only 23` from that venv, and list the probes from the installed package:
  `python -c "import importlib.resources as r; d = r.files('infirmary.doctor') / 'windows'; assert all((d / n).is_file() for n in ('platform.ps1', 'probe.ps1'))"`.
  `stall-explain --demo` needs the packaged demo replay; check 23 opens the packaged `windows/*.ps1` probes only where there is a Windows side (WSL or
  Windows with `powershell.exe`, or a Windows host in `doctor.hosts`), so on plain Linux or macOS it passes without them and the `python -c` line is what
  proves they are there. None of this shows in `--help` or `--version`. The wheel carries the commands, the package, the demo replay
  and the two PowerShell probes; the docs and the other example files ship in the zip. The wheel holds no adapter, and the zip holds only the generic example adapter.
  `infirmary install` needs a checkout, not the wheel: the entry it schedules runs the checkout's `bin/infirmary`, which the wheel does not hold.
