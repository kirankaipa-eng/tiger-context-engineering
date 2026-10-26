"""Reviews: the part of git work that differs by host.

A proposal's review record (title, evidence, checks, comments, approvals, status) is
kept by a ReviewBackend. `LocalReviews` keeps JSON files next to the local repo and
runs offline; `GitLabReviews` maps the same record onto a merge request with
python-gitlab. Everything above this module only sees `ReviewBackend`.
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

_LOCK = threading.RLock()


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ReviewBackend(Protocol):
    def create(self, record: dict) -> dict: ...
    def get(self, pid: str) -> dict: ...
    def list(self) -> list[dict]: ...
    def save(self, record: dict) -> dict: ...


class LocalReviews:
    """One JSON record per proposal under STUDIO_HOME/reviews."""

    def __init__(self, folder: Path):
        self.folder = folder
        folder.mkdir(parents=True, exist_ok=True)

    def _path(self, pid: str) -> Path:
        if not re.fullmatch(r"[a-z0-9-]{3,64}", pid):
            raise KeyError(pid)
        return self.folder / f"{pid}.json"

    def create(self, record: dict) -> dict:
        record.setdefault("created_at", now())
        return self.save(record)

    def get(self, pid: str) -> dict:
        path = self._path(pid)
        if not path.exists():
            raise KeyError(pid)
        return json.loads(path.read_text())

    def list(self) -> list[dict]:
        out = []
        for p in self.folder.glob("*.json"):
            try:
                out.append(json.loads(p.read_text()))
            except ValueError:  # one damaged record must not take the whole inbox down
                print(f"context-studio: skipping unreadable review record {p.name}", file=sys.stderr)
        return sorted(out, key=lambda r: r.get("updated_at", ""), reverse=True)

    def save(self, record: dict) -> dict:
        with _LOCK:
            record["updated_at"] = now()
            path = self._path(record["id"])
            # Write then rename: a reader (or another process) never sees a half-written record.
            tmp = path.with_suffix(f".{os.getpid()}.tmp")
            tmp.write_text(json.dumps(record, indent=2))
            os.replace(tmp, path)
        return record


class GitLabReviews:
    """The same record on a GitLab merge request.

    The human-readable body is the MR description; the structured record rides in a
    hidden comment at its end so the Studio can round-trip it. Approvals are GitLab
    approvals (the owner's own account); comments are MR notes. Needs `GIT_TOKEN`
    (a project bot token that can open MRs and comment, but not merge to main).
    """

    MARK = "<!-- context-studio:"

    def __init__(self, url: str, project: str, token: str):
        import gitlab  # optional extra: pip install insurance-context[gitlab]
        self.gl = gitlab.Gitlab(url, private_token=token)
        self.project = self.gl.projects.get(project)

    def _record(self, mr) -> dict:
        desc = mr.description or ""
        i = desc.rfind(self.MARK)
        record = json.loads(desc[i + len(self.MARK): desc.rindex("-->")]) if i >= 0 else {}
        record.update({"mr_iid": mr.iid, "web_url": mr.web_url})
        state = {"merged": "merged", "closed": "closed"}.get(mr.state)
        if state:
            record["status"] = state
        return record

    def _find(self, pid: str):
        for mr in self.project.mergerequests.list(labels=["curator"], search=pid, all=True):
            if pid in (mr.description or ""):
                return mr
        raise KeyError(pid)

    def create(self, record: dict) -> dict:
        from .render import mr_body
        record.setdefault("created_at", now())
        mr = self.project.mergerequests.create({
            "source_branch": record["branch"], "target_branch": "main", "title": record["title"],
            "description": mr_body(record) + f"\n\n{self.MARK}{json.dumps(record)}-->",
            "labels": ["curator", *[f"area::{a}" for a in record.get("areas", [])]],
        })
        return self._record(mr)

    def get(self, pid: str) -> dict:
        return self._record(self._find(pid))

    def list(self) -> list[dict]:
        return [self._record(mr) for mr in self.project.mergerequests.list(labels=["curator"], all=True)]

    def save(self, record: dict) -> dict:
        from .render import mr_body
        record["updated_at"] = now()
        mr = self._find(record["id"])
        mr.description = mr_body(record) + f"\n\n{self.MARK}{json.dumps(record)}-->"
        mr.save()
        return record


def make_backend(settings) -> ReviewBackend:
    import os
    if settings.git_backend == "gitlab":
        return GitLabReviews(settings.gitlab_url, settings.gitlab_project, os.environ["GIT_TOKEN"])
    return LocalReviews(settings.reviews)
