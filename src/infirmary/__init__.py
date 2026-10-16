"""infirmary: explain why an agent lane is quiet, and keep a fleet healthy through its owners' own safe repairs."""

VERSION = "0.4.2"

# Schema versions are separate from the tool version: a reader of `infirmary.verdict 1.x`
# keeps working across tool releases.
VERDICT_SCHEMA = "infirmary.verdict"
VERDICT_SCHEMA_VERSION = "1.0"
EVIDENCE_SCHEMA = "infirmary.evidence"
EVIDENCE_SCHEMA_VERSION = "1.0"
REPLAY_SCHEMA = "infirmary.replay"
REPLAY_SCHEMA_VERSION = "1.0"
