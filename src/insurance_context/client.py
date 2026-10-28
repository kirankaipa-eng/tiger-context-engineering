"""A client deployment: one line pack, one group overlay, one or more operating units.

    clients/<client>/
      client.yaml                 group overlay + unit registry + agent policy
      units/<unit>/mappings.yaml  how this unit's source system maps to the ontology
      units/<unit>/overlay.yaml   optional unit-level extensions (same operations)

Each unit is an operating unit or a source system. Units keep their own systems
and vocabulary; the group schema is what makes them answerable as one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path

import yaml

from .overlay import CONTENT_ROOT, LINES_DIR, OverlayError, ResolvedSchema, resolve

CLIENTS_DIR = CONTENT_ROOT / "clients"
DEFAULT_REDACT = ("pii", "phi")


@dataclass
class Unit:
    code: str
    name: str
    path: Path
    source_system: str
    client: "Client" = field(repr=False)

    @property
    def schema_name(self) -> str:
        """Warehouse schema holding this unit's ontology views."""
        return f"u_{self.code.lower()}"

    @cached_property
    def mappings(self) -> dict:
        path = self.path / "mappings.yaml"
        if not path.exists():
            raise OverlayError(f"Unit {self.code} has no mappings.yaml at {path}")
        return yaml.safe_load(path.read_text(encoding="utf-8"))

    @cached_property
    def overlay(self) -> dict | None:
        path = self.path / "overlay.yaml"
        if not path.exists():
            return None
        ov = yaml.safe_load(path.read_text(encoding="utf-8"))
        ov.setdefault("client", f"{self.client.name}_{self.code.lower()}")
        ov.setdefault("prefix", f"{self.client.prefix}_{self.code.lower()}")
        ov.setdefault("namespace", f"{self.client.namespace}units/{self.code.lower()}/")
        return ov

    @cached_property
    def resolved(self) -> ResolvedSchema:
        overlays = [self.client.config]
        if self.overlay:
            overlays.append(self.overlay)
        return resolve(self.client.line, overlays)


class Client:
    def __init__(self, name: str, root: Path = CLIENTS_DIR):
        self.name = name
        self.dir = root / name
        path = self.dir / "client.yaml"
        if not path.exists():
            raise OverlayError(f"No client config at {path}")
        self.config: dict = yaml.safe_load(path.read_text(encoding="utf-8"))
        if self.config.get("client") != name:
            raise OverlayError(f"{path}: client is {self.config.get('client')!r}, expected {name!r}")

    @property
    def line(self) -> str:
        return self.config["line"]

    @cached_property
    def line_version(self) -> str:
        """Version of the line pack itself (overlays carry their own version)."""
        pack = yaml.safe_load((LINES_DIR / f"{self.line}.yaml").read_text(encoding="utf-8"))
        return str(pack.get("version", "0"))

    @property
    def prefix(self) -> str:
        return self.config["prefix"]

    @property
    def namespace(self) -> str:
        return self.config["namespace"]

    @property
    def title(self) -> str:
        return self.config.get("title", self.name)

    @property
    def redact(self) -> tuple[str, ...]:
        policy = self.config.get("agent_policy") or {}
        return tuple(policy.get("redact", DEFAULT_REDACT))

    @cached_property
    def units(self) -> list[Unit]:
        units = []
        for u in self.config.get("units") or []:
            units.append(Unit(
                code=u["code"],
                name=u.get("name", u["code"]),
                path=self.dir / u.get("path", f"units/{u['code'].lower()}"),
                source_system=u.get("source_system", u["code"]),
                client=self,
            ))
        if not units:
            raise OverlayError(f"Client {self.name} declares no units")
        return units

    def unit(self, code: str) -> Unit:
        for u in self.units:
            if u.code.lower() == code.lower():
                return u
        raise OverlayError(f"Client {self.name} has no unit {code!r}")

    @cached_property
    def resolved(self) -> ResolvedSchema:
        """The group-level schema: line pack + client overlay."""
        return resolve(self.line, [self.config])


def list_clients(root: Path = CLIENTS_DIR) -> list[str]:
    return sorted(p.parent.name for p in root.glob("*/client.yaml"))
