# Sharing the host beat

`stall-explain` runs no sampler of its own for host health. It reads the `lookout.beat` file that
`lookout-beat` already writes (`beat.json`, replaced atomically every sample) and uses the file's age on
this machine's own clock, never a comparison of two machines' clocks, to tell a beat that stopped from
one that is slow. `infirmary status` shows the local beat (`beat.path`, default per OS). A source
that watches jobs on other hosts reads their beat copies the same way (the `hosts` block). The Infirmary
(`infirmary check`) adds one process inventory per run (about 20 ms on Linux for 250 processes) and writes
`doctor.json` into its own state directory (set `doctor.out` to put it next to `beat.json`). When the beat is fresh
(`doctor.memory.beat_fresh_s`, 120 s) its `pressure.level` is evidence for the memory check; a beat that stopped is ignored,
and the Infirmary then reads the kernel's own numbers once.
