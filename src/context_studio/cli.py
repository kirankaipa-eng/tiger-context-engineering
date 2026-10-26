"""ctx-studio: set up, run and reset Context Studio.

    ctx-studio init            create the content repo and build reference warehouses
    ctx-studio serve           run the API on :8700 (the web app runs on :3700)
    ctx-studio scan <client>   run the curator over a client's documents
    ctx-studio onboard <client>  propose adding a client from this checkout to the content repo
    ctx-studio harvest-powerbi <client> <pbip folder | model.bim | .pbit | .pbix>
                               propose a crosswalk of a Power BI model's measures
    ctx-studio reset           delete STUDIO_HOME and start again (asks first)
"""

from __future__ import annotations

import argparse
import shutil
import sys


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="ctx-studio")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    sv = sub.add_parser("serve")
    sv.add_argument("--port", type=int, default=8700)
    sv.add_argument("--host", default="127.0.0.1")
    sc = sub.add_parser("scan")
    sc.add_argument("client")
    sc.add_argument("--no-llm", action="store_true")
    ob = sub.add_parser("onboard")
    ob.add_argument("client")
    hv = sub.add_parser("harvest-powerbi")
    hv.add_argument("client")
    hv.add_argument("folder")
    rs = sub.add_parser("reset")
    rs.add_argument("--yes", action="store_true")
    args = ap.parse_args(argv)

    from .config import bootstrap, settings
    if args.cmd == "reset":
        home = settings().home
        if not args.yes and input(f"Delete {home} (all proposals and history)? [y/N] ").lower() != "y":
            sys.exit(1)
        shutil.rmtree(home, ignore_errors=True)
        print(f"removed {home}")
        return

    s = bootstrap()
    if args.cmd == "init":
        from .proposals import Studio
        st = Studio(s)
        for c in st.clients():
            if c["has_data"]:
                print(f"building reference data for {c['name']}...", flush=True)
                st.served_warehouse(c["name"])
        print(f"Context Studio ready in {s.home}")
    elif args.cmd == "serve":
        import uvicorn
        uvicorn.run("context_studio.app:app", host=args.host, port=args.port, log_level="info")
    elif args.cmd == "scan":
        from insurance_context import llm
        from . import curator
        from .proposals import Studio
        st = Studio(s)
        rep = curator.scan(st, args.client, complete=None if args.no_llm or not llm.configured() else llm.complete,
                           progress=print)
        for r in st.list(status="checking", client=args.client):  # finish checks before the process exits
            st.check(r["id"])
        print(curator.evaluate(st, args.client, rep))
    elif args.cmd == "onboard":
        from .proposals import Studio
        st = Studio(s)
        r = onboard(st, args.client)
        checks = st.check(r["id"]).get("checks") or {}  # finish checks before the process exits
        print(f"{r['id']}: {r['title']} -> {', '.join(r['area_titles'].values())}; "
              f"checks {'pass' if checks.get('passed') else 'FAIL'}")
    elif args.cmd == "harvest-powerbi":
        from .proposals import Studio
        st = Studio(s)
        r = harvest_powerbi(st, args.client, args.folder)
        checks = st.check(r["id"]).get("checks") or {}
        print(f"{r['id']}: {r['title']} -> {', '.join(r['area_titles'].values())}; "
              f"checks {'pass' if checks.get('passed') else 'FAIL'}")


def harvest_powerbi(st, client: str, folder: str, author: dict | None = None) -> dict:
    """One proposal: a Power BI model's measures crosswalked to governed metrics, plus the governed
    metrics generated back as TMDL. The BI model keeps its measures; the crosswalk is owned content."""
    import yaml as _yaml
    from pathlib import Path

    from insurance_context import powerbi

    model = Path(folder).name if Path(folder).is_dir() else Path(folder).stem
    sl = st.semantic(client)
    rows = powerbi.crosswalk(sl, powerbi.harvest(folder))
    governed = list(dict.fromkeys(r["metric"] for r in rows if r["status"] == "governed"))
    tmdl, kept = powerbi.to_tmdl(sl, governed)
    count = {k: sum(1 for r in rows if r["status"] == k) for k in ("governed", "presentation", "gap")}
    doc = {"model": model, "source": str(folder), "summary": count,
           "measures": [{k: r[k] for k in ("name", "table", "status", "metric", "why", "dax")} for r in rows]}
    base = f"clients/{client}/powerbi"
    ops = [{"op": "write_file", "path": f"{base}/{model}.crosswalk.yaml",
            "content": "# Measures of a Power BI semantic model, crosswalked to governed metrics.\n"
                       "# status: governed (same quantity as a governed metric) | presentation (formats or\n"
                       "# compares a governed quantity) | gap (the context layer does not cover it yet).\n"
                       + _yaml.safe_dump(doc, sort_keys=False, width=110, allow_unicode=True)},
           {"op": "write_file", "path": f"{base}/governed_measures.tmdl", "content": tmdl + "\n"}]
    gaps = [r["name"] for r in rows if r["status"] == "gap"]
    steward = st.owners().person("plains-medecon-lead" if client == "plains_health" else "pack-steward")
    author = author or {"id": steward.id, "name": steward.name, "kind": "person"}
    return st.create(client=client, title=f"Crosswalk the {model} Power BI model ({len(rows)} measures)", ops=ops,
                     author=author,
                     summary=(f"{count['governed']} measures are governed metrics already, {count['presentation']} "
                              f"format or compare them over time, and {count['gap']} are not covered yet: "
                              f"{', '.join(gaps[:8])}{' and more' if len(gaps) > 8 else ''}. The governed "
                              f"metrics are generated back as TMDL ({len(governed)} measures"
                              + (f"; {len(kept)} stay in SQL" if kept else "") + ")."),
                     kind="change")


def onboard(st, client: str) -> dict:
    """One proposal that adds a client's content (not its data) and its owners to the content repo.

    Onboarding a client is a change like any other: its files and the owners who will
    approve its future changes are reviewed by the people who own the repo today.
    """
    import io
    from pathlib import Path

    from ruamel.yaml import YAML

    src = Path(__file__).resolve().parents[2] / "clients" / client
    if not src.exists():
        raise SystemExit(f"no client {client!r} in this checkout")
    ops = [{"op": "write_file", "path": str(p.relative_to(src.parents[1])), "content": p.read_text()}
           for p in sorted(src.rglob("*")) if p.is_file() and "data" not in p.relative_to(src).parts
           and p.suffix in (".yaml", ".yml", ".md", ".tmdl")]
    # Owners: add the client's people and areas from the seed to the repo's owners.yaml.
    y = YAML()
    y.preserve_quotes = True
    live = y.load((st.s.main / "owners.yaml").read_text())
    seed = y.load((Path(__file__).parent / "seed" / "owners.yaml").read_text())
    prefix = client.split("_")[0]
    for section in ("people", "areas"):
        for key, val in seed[section].items():
            if key.startswith(prefix) and key not in live[section]:
                live[section][key] = val
    buf = io.StringIO()
    y.dump(live, buf)
    ops.append({"op": "write_file", "path": "owners.yaml", "content": buf.getvalue()})
    steward = st.owners().person("pack-steward")
    title = client.replace("_", " ").title()
    return st.create(client=client, title=f"Onboard {title}", ops=ops,
                     author={"id": "pack-steward", "name": steward.name, "kind": "person"},
                     summary=f"Adds {title}: its vocabulary, warehouse mappings, documents for the curator, "
                             f"and the owners who will approve its changes from now on.", kind="change")


if __name__ == "__main__":
    main()
