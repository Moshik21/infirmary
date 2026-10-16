# ssh additional checks (catalogue 28)

Some ssh servers will not let a login through until a person signs in again. Tailscale SSH does this with its **check** mode: the first
connection of a source to a destination, and then again once the *check period* has run out, prints a prompt and waits.

```
# Tailscale SSH requires an additional check.
# To authenticate, visit: https://login.example.invalid/a/0123456789ab
```

An interactive user opens the URL, signs in with the identity provider, and the login goes on. A **non-interactive** client (a lane's steer to
a mate on another machine, a `git push` over ssh, a remote job channel, any `ssh -o BatchMode=yes`) cannot. It does not fail with an error
that anything would notice: it waits, and what it was to deliver queues behind it. The first sign is usually a steer nobody answered.

## What the Infirmary does

`infirmary check` (check id `ssh-check`) makes **one** non-interactive attempt per ssh alias it knows and sorts the answer:

| Answer | Meaning | Reported as |
| --- | --- | --- |
| `ok` | the no-op command `echo` ran | nothing; counted in the detail line |
| `check` | the server printed an additional-check prompt (recognised by its message and the login URL after it) | a **finding** `ssh-check-required`, `WARNING`, a `decision` card: **the operator's to clear**; it names the URL and what waits behind it |
| `auth` | login refused, or the host key is not known (`BatchMode` never accepts one) | the detail line only |
| `unreachable` | nothing answered in time, connection refused or closed | the detail line only (`hung-host` is the check that reports a host that does not answer) |

Only a prompt is a finding: it is the one answer that only the operator can clear and that fails silently. An alias that is down or refuses
its key is not a decision, and a laptop that sleeps would otherwise raise a card every night. When no alias answered at all the check says
`unknown`, not fine, because a prompt could not be ruled out.

The attempt is `ssh -o BatchMode=yes -o ConnectTimeout=N -o ControlMaster=no -o ControlPath=none -o LogLevel=INFO ALIAS echo infirmary-ok`, ended by a hard
timeout (`doctor.ssh_timeout_s` plus 8 seconds). The aliases of `doctor.hosts` (and their guests) are tried through the probe `hung-host` makes
anyway, so no host is connected to twice; a host that only waits for a check is **not** reported hung.

**What it never does.** It never opens or fetches the URL, never completes the check, and never changes ssh config, keys, known hosts, Tailscale
settings or the policy file. The URL is shown to a person and nothing else. It comes from the remote side, so only one plain printable
`http(s)` token after the message is kept. Each attempt makes the server open a pending check of its own that nobody finishes; that costs nothing but a log line there.

## Which aliases

```
doctor.hosts[].ssh and .guest.ssh     tried by default (doctor.ssh_check.from_hosts)
doctor.ssh_check.aliases              ["mate-box", "build-box"]: more aliases, by name
doctor.ssh_check.from_ssh_config      true: every plain `Host` name in the ssh config (wildcards and `!` entries skipped, Include followed)
doctor.ssh_check.ssh_config           the file to read (default ~/.ssh/config)
doctor.ssh_check.exclude              ["git-forge"]: never tried (a forge, a jump host, anything that is not a fleet machine)
doctor.ssh_check.max_aliases          24: the cap; the rest are named in the detail line, not tried
```

An alias is a plain name (letters, digits, `. _ @ : + -`, never a leading dash): it becomes an argument of ssh, so anything else is a
configuration error. Turn `from_ssh_config` on only for a config whose hosts are all yours to connect to: every alias gets a connection.
`doctor.json` has `local.ssh-check.aliases{alias: {state, url_seen}}` ([findings.md](findings.md)).

## What it blocks

Every non-interactive use of the alias, from the machine the Infirmary runs on, until the operator completes the check:

- steers: a message sent to a lane, mate or supervisor on that machine over ssh;
- pushes and pulls over ssh (`git push`, `git fetch`, `rsync`, `scp`);
- remote job and canary channels, and the Infirmary's own probes of that host (which then say so, not "hung").

A check is tied to who connects to what (source, destination, ssh user), so the other direction (a steer *from* the remote machine) has its own state. Expect to clear it once per direction and host that matters.
The recommendation in the finding is to finish the check at the URL, to treat anything sent meanwhile as **not delivered**, and to decide
whether the check period suits the hosts that lanes depend on.

## The check period (operator guidance)

In Tailscale's policy file (the access-control file edited in the admin console, or kept in git), SSH access is a list under `"ssh"`. A rule
whose `action` is `"check"` makes the user re-authenticate; `checkPeriod` says for how long one sign-in is good:

```json
"ssh": [
  {
    "action": "check",
    "checkPeriod": "12h",
    "src": ["group:operators"],
    "dst": ["tag:lane-hosts"],
    "users": ["autogroup:nonroot"]
  }
]
```

- `checkPeriod` is optional and only valid with `"action": "check"`. The default is **12 hours**: after a sign-in the same source can open new
  sessions to that destination as that user for 12 hours, then the next connection stops at the prompt again. Values are durations such as
  `"30m"`, `"20h"`, `"168h"`; the documented range is a short minimum (minutes) up to 168 hours (one week), and `"always"` asks on every connection (never right for
  an unattended client). Check the current Tailscale documentation for the exact limits before relying on one.
- `"action": "accept"` asks nothing. Hosts that unattended clients must reach at any hour (a lane host that gets steers overnight) are the
  usual case for a longer period or for `accept`: a 12 hour period means a steer sent the morning after the operator last signed in stalls.
- Choosing is the operator's decision, because a longer period is a weaker second factor. The Infirmary does not edit the policy, and nothing here
  recommends a value for you; it only tells you when the period ran out on a host something depends on.
- Not everything is covered by the period: a changed key, a policy edit, a node that was logged out or expired may ask again sooner.

Other ssh servers with a similar interactive step (a one-time-code prompt, a banner that waits) are not recognised by the current message
match; they show as `unreachable` (timed out) or `auth`. The recogniser is one small function (`infirmary/doctor/sshclass.py`).
