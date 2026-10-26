# Ontology curator: hello world

Five small scripts that walk one needle (PFAS) through the whole curator loop.
The spec explains the design; these are starting points for each component.

| Script | Step | Needs |
| --- | --- | --- |
| `01_find_the_needle.py` | Search 12 documents, classify each PFAS mention | nothing (`--llm` optional) |
| `02_corroborate.py` | Link to the model, check the data, pick layer and owners | reference warehouse (built on first run) |
| `03_draft_and_verify.py` | Branch, edit the pack, write the OKF proposal, run the checks | git |
| `04_open_change_request.py` | Open the change request (local, or a real git host via push options) | git; SSH for `--remote` |
| `05_curator_chat.py` | Owner reviews and approves by chat | `API_KEY`, `API_BASE_URL`, `CHAT_MODEL` |

```bash
uv run python examples/curator/01_find_the_needle.py
uv run python examples/curator/02_corroborate.py
uv run python examples/curator/03_draft_and_verify.py
uv run python examples/curator/04_open_change_request.py
uv run python examples/curator/05_curator_chat.py
```

`corpus/answer_key.yaml` lists every needle and decoy in the practice documents, for
measuring how many the curator finds (recall) and how many it gets wrong (precision).
Scratch state lives in `.work/`; delete it to start over.
