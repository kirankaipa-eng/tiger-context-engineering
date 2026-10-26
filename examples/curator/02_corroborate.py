"""Step 2: link the claim to the model and check it against the data.

    uv run python examples/curator/02_corroborate.py
"""
from curatorlib import corroborate, decide, extract_claim, link_to_ontology, load_corpus, search

needle = next(c for c in map(extract_claim, search(load_corpus(), ["PFAS"])) if c.kind == "ontology_change")
print(f"Claim: separate {needle.subject} code for {needle.value}  ({needle.passage.cite()})\n")

link = link_to_ontology(needle)
print(f"Link:  {link['label']}.{link['slot']} uses {link['enum']}; "
      f"'{link['value']}' is {'already there' if link['exists'] else 'not in the model yet'}")

corr = corroborate(link)
print(f"Data:  {'agrees' if corr['agrees'] else 'no support yet'} - {corr['evidence']}")

d = decide(needle, corr)
print(f"Decide: {d['layer']} layer (regulation applies to every carrier), "
      f"confidence {d['confidence']}, owners {', '.join(d['owners'])}")
