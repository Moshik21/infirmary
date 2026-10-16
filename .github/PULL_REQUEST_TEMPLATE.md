## What and why

See [CONTRIBUTING.md](../CONTRIBUTING.md) and the [code of conduct](../CODE_OF_CONDUCT.md).

## Checklist
- [ ] `python3 scripts/test --denylist <denylist> --denylist <vendor-words>` passes (both private name lists, see CONTRIBUTING)
- [ ] `scripts/merged-check` says `merged-check: ok` against current `origin/main` (run it right before hand-off, and again if main moved before the merge)
- [ ] standard library only; no tool, person, host, address or home path names anywhere in the tree (`scripts/self-audit` enforces it)
- [ ] a benign verdict still needs positive evidence
- [ ] docs updated (verdicts, evidence, sources, CHANGELOG)
