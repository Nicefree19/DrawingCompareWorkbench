# Structural Evidence Fixture Evaluation v0.1

Purpose:

- lock a source-control-safe fixture matrix for structural drawing evidence
- verify source health semantics for parsed, partial, missing, unsupported, and comparison inputs
- keep false-positive and false-negative observations as rule-catalog inputs

Matrix:

- `docs/structural_evidence_fixture_matrix_v0_1.json`

Execution:

```powershell
python scripts/evaluate_structural_evidence_fixtures.py --json
python scripts/evaluate_structural_evidence_fixtures.py
```

Required gates:

- every case returns the expected status and source health
- evidence count stays within the case cap and global max 30
- compact output has no raw CAD payload keys, secret-like markers, or approval/release wording
- missing and unsupported sources fail closed with human-review issue suggestions
- comparison differences are represented as review evidence only

Current backlog:

- False positive watch: no-change comparison wording must never read as final approval.
- False positive watch: dense generated drawings may include unrelated nearby tags.
- False negative watch: unsupported DXF geometry can hide anchors.
- False negative watch: modern DWG anchors remain blocked until clean-room reader approval.
