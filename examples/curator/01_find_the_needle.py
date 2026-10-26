"""Step 1: find the needle.

Search 12 documents for PFAS, then decide for each hit: is it a change to the model,
a business rule, or noise? Every claim keeps its document, page and paragraph.

    uv run python examples/curator/01_find_the_needle.py [--llm]
"""
import sys

from curatorlib import extract_claim, extract_claim_llm, load_corpus, search

passages = load_corpus()
docs = len({p.doc for p in passages})
hits = search(passages, ["PFAS"])
print(f"Searched {len(passages)} passages in {docs} documents; {len(hits)} mention PFAS.\n")

extract = extract_claim
if "--llm" in sys.argv:
    from insurance_context import llm
    extract = lambda p: extract_claim_llm(p, llm.complete)  # noqa: E731

for claim in map(extract, hits):
    mark = {"ontology_change": "NEEDLE", "rule": "rule  ", "noise": "noise "}[claim.kind]
    print(f"[{mark}] {claim.passage.cite()}")
    print(f"         \"{claim.passage.text[:110]}...\"")
    print(f"         -> {claim.why}\n")
