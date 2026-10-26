"""Ownership: which areas a change touches, and who may approve it (owners.yaml)."""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class Area:
    id: str
    title: str
    owners: list[str]
    paths: list[str] = field(default_factory=list)
    contains: list[str] = field(default_factory=list)
    min_approvals: int = 1


@dataclass
class Person:
    id: str
    name: str
    role: str = ""


class Owners:
    def __init__(self, data: dict):
        self.people = {pid: Person(pid, p.get("name", pid), p.get("role", ""))
                       for pid, p in (data.get("people") or {}).items()}
        self.areas = {aid: Area(aid, a.get("title", aid), list(a.get("owners", [])), list(a.get("paths", [])),
                                list(a.get("contains", [])), int(a.get("min_approvals", 1)))
                      for aid, a in (data.get("areas") or {}).items()}

    @classmethod
    def load(cls, repo: Path) -> "Owners":
        return cls(yaml.safe_load((repo / "owners.yaml").read_text()) or {})

    def person(self, pid: str) -> Person:
        return self.people.get(pid) or Person(pid, pid)

    def areas_of(self, pid: str) -> list[Area]:
        return [a for a in self.areas.values() if pid in a.owners]

    def areas_for(self, files: list[str], changed_keys: dict[str, set[str]], added_lines: list[str]) -> list[Area]:
        """Every area the change touches.

        files         changed paths
        changed_keys  path -> top-level YAML keys whose value changed (for `file#key` rules)
        added_lines   the diff's added and removed lines (for `contains` rules)
        """
        hit = []
        for a in self.areas.values():
            matched = False
            for pat in a.paths:
                path_pat, _, key = pat.partition("#")
                for f in files:
                    if fnmatch.fnmatch(f, path_pat) and (not key or key in changed_keys.get(f, set())):
                        matched = True
            if a.contains and any(c in line for c in a.contains for line in added_lines):
                matched = True
            if matched:
                hit.append(a)
        # `file#key` rules are narrower than a whole-file rule on the same file only by key;
        # both apply when both match (a client metric override needs the steward and actuarial).
        return hit
