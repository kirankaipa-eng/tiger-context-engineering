"""Where the Microsoft Sales & Returns sample is: `scripts/pbi_sample.py` writes build/pbi-sample (the
.pbix as published, and its TMDL with the one measure the tests expect added). Not the local GitLab
demo's clone (build/pbi-models): its model changes as the demo runs. Tests that need it skip without it."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GET_IT = "run `uv run python scripts/pbi_sample.py` to download it"

SAMPLE_MODEL = ROOT / "build" / "pbi-sample" / "models" / "sales-returns"
SAMPLE_PBIX = SAMPLE_MODEL / "sales-returns.pbix"
SAMPLE_TMDL = SAMPLE_MODEL / "sales-returns.SemanticModel"
