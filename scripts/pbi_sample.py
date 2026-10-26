"""The Microsoft Sales & Returns sample, as the Power BI tests and the field guide's walkthrough use it.

    uv run python scripts/pbi_sample.py        # -> build/pbi-sample/models/sales-returns/

Downloads Microsoft's public sample .pbix (checksum checked), then makes the change a model owner
makes in the local GitLab demo (scripts/demo_pbi_gitlab.py): the model saved as TMDL with one
measure added (External Net Sales) and the Sales and Store tables confirmed in warehouse.yaml.
Running it again keeps what is there.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
URL = ("https://github.com/microsoft/powerbi-desktop-samples/raw/main/Sample%20Reports/"
       "Sales%20%26%20Returns%20Sample%20v201912.pbix")
SHA256 = "c0a51fbae6c9e3c3cf0ff0564cc3408dad614449b5b27ec3e47fbaea4ba7f04b"
MEASURE = "\tmeasure 'External Net Sales' = CALCULATE([Net Sales], Store[Type] = \"External\")\n\t\tformatString: \\$#,0\n\n"
WAREHOUSE = """# Databricks tables the model reads, confirmed by the model owner.
catalog: pilot
schema: sales_returns
tables:
  Sales: pilot.sales_returns.sales
  Store: pilot.sales_returns.store
"""


def fetch(dest: Path) -> Path:
    """The sample .pbix at `dest`, downloaded once and checked against its published checksum."""
    if dest.exists() and hashlib.sha256(dest.read_bytes()).hexdigest() == SHA256:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"downloading the Microsoft Sales & Returns sample (6 MB) to {dest}")
    with urllib.request.urlopen(URL, timeout=120) as r:  # noqa: S310 - a fixed public URL
        data = r.read()
    got = hashlib.sha256(data).hexdigest()
    if got != SHA256:
        sys.exit(f"the download's checksum is {got}, expected {SHA256}: Microsoft may have replaced the sample")
    dest.write_bytes(data)
    return dest


def owner_edit(model: Path) -> None:
    """Save the model as TMDL, add External Net Sales, confirm two tables: the demo's merge request."""
    sys.path.insert(0, str(ROOT / "src"))
    from insurance_context.pbimodel import read_pbix, write_tmdl
    if not (model / f"{model.name}.SemanticModel").exists():
        write_tmdl(read_pbix(model / f"{model.name}.pbix"), model)
    f = model / f"{model.name}.SemanticModel" / "definition" / "tables" / "Analysis DAX.tmdl"
    text = f.read_text()
    if "'External Net Sales'" not in text:
        f.write_text(text.replace("\tmeasure 'Net Sales PM'", MEASURE + "\tmeasure 'Net Sales PM'", 1))
    if not (model / "warehouse.yaml").exists():
        (model / "warehouse.yaml").write_text(WAREHOUSE)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", default=str(ROOT / "build" / "pbi-sample"), help="folder that gets models/sales-returns/")
    args = p.parse_args()
    model = Path(args.out) / "models" / "sales-returns"
    fetch(model / "sales-returns.pbix")
    owner_edit(model)
    print(f"ready: {model}\n"
          f"  {model.name}.pbix              the file as Microsoft publishes it (58 measures)\n"
          f"  {model.name}.SemanticModel/    the same model as TMDL, with External Net Sales added (59)\n"
          f"  warehouse.yaml                  the Sales and Store tables confirmed")


if __name__ == "__main__":
    main()
