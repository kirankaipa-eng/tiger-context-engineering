"""Context Studio: proposals over git, owners, checks, curator, API."""

import os

import pytest
import yaml

pytest.importorskip("ruamel.yaml")

PFAS = {"op": "add_enum_value", "enum": "CauseOfLossEnum", "value": "pfas",
        "description": "PFAS contamination (regulatory coding requirement)."}


@pytest.fixture(scope="module")
def studio(tmp_path_factory):
    home = tmp_path_factory.mktemp("studio")
    os.environ["CTX_STUDIO_HOME"] = str(home)
    from context_studio.config import bootstrap
    from context_studio.proposals import Studio
    return Studio(bootstrap())


def test_seed_is_a_git_repo_with_owners(studio):
    assert (studio.s.main / "owners.yaml").exists()
    assert (studio.s.main / "clients" / "summit_group" / "corpus").exists()
    assert [c.subject for c in studio.repo.log()] == ["Initial ontology content"]


def test_edits_keep_everything_else_byte_for_byte(tmp_path, studio):
    import shutil
    from context_studio import edits
    root = tmp_path / "c"
    shutil.copytree(studio.s.main / "ontology", root / "ontology")
    shutil.copytree(studio.s.main / "clients", root / "clients")
    pack = root / "ontology" / "lines" / "commercial_pc.yaml"
    before = pack.read_text().splitlines()
    edits.apply(root, [PFAS])
    after = pack.read_text().splitlines()
    added = [ln for ln in after if ln not in before]
    assert any("pfas:" in ln for ln in added) and len(added) <= 3
    assert all(ln in after for ln in before)  # comments and layout kept
    with pytest.raises(edits.EditError):
        edits.apply(root, [PFAS])  # already there


def test_routing_by_paths_keys_and_content(studio):
    o = studio.owners()
    ids = lambda areas: {a.id for a in areas}
    assert ids(o.areas_for(["ontology/lines/commercial_pc.yaml"], {}, [])) == {"pack-concepts"}
    both = o.areas_for(["clients/summit_group/client.yaml"], {"clients/summit_group/client.yaml": {"metrics"}}, [])
    assert ids(both) == {"summit-vocabulary", "metrics"}
    assert "privacy" in ids(o.areas_for(["clients/summit_group/client.yaml"], {}, ["  sensitivity: pii"]))


def test_lifecycle_check_approve_merge_undo(studio):
    from context_studio.proposals import CURATOR
    r = studio.create(client="summit_group", title="Add PFAS as a cause of loss", author=CURATOR, ops=[PFAS],
                      evidence=[{"quote": "code PFAS separately", "doc": "doi_bulletin_2026_14.md", "page": 3}],
                      run_checks=False)
    assert r["areas"] == ["pack-concepts"] and r["reviewers"] == {"pack-concepts": ["pack-steward"]}
    assert studio.repo.log()[0].subject == "Initial ontology content"  # nothing on main yet

    r = studio.check(r["id"])
    assert r["status"] == "in_review" and r["checks"]["passed"]
    assert any(f["slot"] == "cause_of_loss" for f in r["checks"]["findings_cleared"])

    with pytest.raises(PermissionError):
        studio.approve(r["id"], "actuarial-lead")  # not an owner of this area
    r = studio.approve(r["id"], "pack-steward")
    assert r["status"] == "merged"
    head = studio.repo.log()[0]
    assert head.subject == "Add PFAS as a cause of loss" and "Approved-by: Pack steward" in head.body
    assert "pfas:" in (studio.s.main / "ontology/lines/commercial_pc.yaml").read_text()
    okf = (studio.s.main / "okf/proposals" / f"{r['id']}.md").read_text()
    assert "status: accepted" in okf and "human:pack-steward" in okf

    hist = studio.history()
    assert hist[0]["approvals"][0]["by"] == "pack-steward"

    u = studio.revert(r["merged_sha"], "pack-steward")
    assert u["title"].startswith("Undo:") and u["areas"] == ["pack-concepts"]
    u = studio.check(u["id"])
    studio.approve(u["id"], "pack-steward")
    assert "pfas:" not in (studio.s.main / "ontology/lines/commercial_pc.yaml").read_text()


def test_request_changes_then_revise_resets_approvals(studio):
    me = {"id": "summit-data-steward", "name": "Summit data steward", "kind": "person"}
    r = studio.create(client="summit_group", title="Call Producer 'Agency'", author=me, run_checks=False,
                      ops=[{"op": "set_label", "client": "summit_group", "kind": "classes", "name": "Producer",
                            "label": "Agency"}])
    assert r["areas"] == ["summit-vocabulary"]
    studio.check(r["id"])
    r = studio.comment(r["id"], "summit-data-steward", "Use 'Producing agency'", "request_changes")
    assert r["status"] == "changes_requested"
    from context_studio.proposals import CURATOR
    r = studio.revise(r["id"], [{"op": "set_label", "client": "summit_group", "kind": "classes",
                                 "name": "Producer", "label": "Producing agency"}], CURATOR, "As asked.")
    assert r["status"] == "checking" and r["approvals"] == []
    studio.close(r["id"], "summit-data-steward")


def test_questions_route_to_owners(studio):
    from context_studio.proposals import CURATOR
    q = studio.ask(client="summit_group", title="Two definitions of litigated", summary="Owners decide.",
                   evidence=[], about={"type": "definition", "conflict": True}, author=CURATOR)
    assert q["status"] == "question" and "summit-vocabulary" in q["areas"]
    assert studio.waiting_on(q, "summit-data-steward")
    assert studio.answer(q["id"], "summit-data-steward", "Suit filed.")["status"] == "answered"


def test_curator_rules_mode_finds_pfas_and_sets_aside_rules(studio):
    from context_studio import curator
    rep = curator.scan(studio, "summit_group", complete=None)
    actions = {r["action"] for r in rep["results"]}
    assert actions & {"proposed", "already_known", "already_proposed"}
    assert any("uw_guideline" in s["doc"] for s in rep["set_aside"])


def test_api_inbox_and_permissions(studio):
    from fastapi.testclient import TestClient
    from context_studio.app import app
    c = TestClient(app)
    assert c.get("/api/health").json()["ok"]
    people = {p["id"] for p in c.get("/api/people").json()["people"]}
    assert {"pack-steward", "actuarial-lead"} <= people
    assert c.post("/api/proposals", json={"client": "summit_group", "title": "x", "ops": []}).status_code == 401
    assert c.get("/api/inbox?client=summit_group", headers={"X-Studio-User": "nobody"}).status_code == 403
    model = c.get("/api/map?client=summit_group").json()
    assert any(k["name"] == "CommercialClaim" for k in model["classes"])
    m = c.get("/api/metrics/loss_alae_ratio?client=summit_group").json()
    assert m["values"]["rows"]


def test_branch_catches_up_with_main_and_failed_checks_block_approval(studio):
    from context_studio.proposals import CURATOR, ProposalError
    a = studio.create(client="summit_group", title="Add PFAS again", author=CURATOR, ops=[PFAS], run_checks=False,
                      evidence=[{"quote": "q", "doc": "d.md", "page": 1}])
    b = studio.create(client="summit_group", title="Add microplastics as a cause of loss", author=CURATOR,
                      run_checks=False, evidence=[{"quote": "q", "doc": "d.md", "page": 1}],
                      ops=[{**PFAS, "value": "microplastics", "description": "Microplastics."}])
    studio.check(a["id"])
    studio.approve(a["id"], "pack-steward")            # main moves after b was drafted
    b = studio.check(b["id"])                          # b catches up, so PFAS is not a "new" finding
    assert b["checks"]["passed"], b["checks"]["items"]
    r = studio.get(b["id"])
    r["checks"]["passed"] = False
    studio.reviews.save(r)
    with pytest.raises(ProposalError):
        studio.approve(b["id"], "pack-steward")
