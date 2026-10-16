# Security policy

## Reporting a vulnerability

Report a suspected vulnerability privately through GitHub's private vulnerability reporting: open the repository's **Security**
tab, choose **Report a vulnerability**, and fill in the form. Please do not open a public issue or pull request for it. There is no
e-mail address for reports.

Include the command you ran, the output (with secrets removed), the version, and your OS and Python version.

This is a spare-time, pre-1.0 project. No response time is guaranteed; reports are read and answered on a best-effort basis.

## Supported versions

Only the latest release is supported. A fix lands in the next release; older releases are not patched.

## In scope

- Anything that makes `stall-explain` or `infirmary` write outside their own state directory,
  run a command that was not named in the code, or read or print a secret.
- A verdict that hides a real problem by construction (a benign verdict without positive evidence).
- A path-handling or parsing flaw reachable from a lane file, a status log, a replay file or a config.

## Out of scope

- Findings that need an attacker who can already write the configured state directory, the lane
  files or the user's config (they can already do everything the tool does).
- Denial of service by giving the tool a very large replay file.

## What the tool promises

See docs/safety.md. In short: advice-only (the default) never acts, live repair is off until the operator turns it on for a home with `infirmary live on` and nothing else can, there is no network traffic except a Lookout
push you configure, secrets are never read from or put on a command line, and the only thing written
is `<state dir>/actions.jsonl`, `push.seq` and the `off` switch file (plus, for the Infirmary, its own state files, and `live-repair.json`, which only the operator's `infirmary live on|off` writes).
Live repair has never been run on a live fleet by the project; it has only run against synthetic homes and shim owners.
