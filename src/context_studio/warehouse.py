"""Synthetic reference warehouses, one per client, so every screen has real numbers.

The raw warehouse (source tables only) is built once. Views are installed for the
content being served: main, or a proposal branch on a throwaway copy.
"""

from __future__ import annotations

import shutil
import threading
from datetime import date
from pathlib import Path

_LOCK = threading.RLock()


def _checkpoint(path: Path) -> None:
    """Fold the write-ahead log into the file, so the file alone is a complete warehouse."""
    import duckdb
    con = duckdb.connect(str(path))
    try:
        con.execute("CHECKPOINT")
    finally:
        con.close()


def _builders() -> dict:
    from insurance_context.fixtures import payer_warehouse, pc_warehouse
    out = {"summit_group": (pc_warehouse.build, pc_warehouse.VALUATION),
           "acme_health": (payer_warehouse.build, getattr(payer_warehouse, "VALUATION", None))}
    meridian = getattr(payer_warehouse, "build_meridian", None)
    if meridian:
        out["meridian_health"] = (meridian, getattr(payer_warehouse, "MERIDIAN_VALUATION",
                                                    getattr(payer_warehouse, "VALUATION", None)))
    try:
        from insurance_context.fixtures import la_warehouse
        out["northwind_life"] = (la_warehouse.build_northwind, la_warehouse.NORTHWIND_VALUATION)
    except (ImportError, AttributeError):
        pass  # life & annuity reference data not built in this checkout
    try:
        from insurance_context.fixtures import mee_warehouse
        if all((mee_warehouse.DATA / f"{t}.parquet").exists() for t in mee_warehouse.TABLES):
            out["plains_health"] = (mee_warehouse.build_plains, mee_warehouse.PLAINS_VALUATION)
    except (ImportError, AttributeError):
        pass  # medical economics demo data not snapshotted in this checkout
    return out


def supported(client: str) -> bool:
    return client in _builders()


def valuation(client: str) -> date | None:
    entry = _builders().get(client)
    return entry[1] if entry else None


def raw_path(folder: Path, client: str) -> Path | None:
    """The source-only warehouse for a client, built on first use."""
    entry = _builders().get(client)
    if not entry:
        return None
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{client}.raw.duckdb"
    with _LOCK:
        if not path.exists():
            tmp = folder / f"{client}.building.duckdb"
            tmp.unlink(missing_ok=True)
            entry[0](tmp)
            _checkpoint(tmp)
            tmp.rename(path)
    return path


def served_path(folder: Path, client: str, head: str) -> Path | None:
    """Raw warehouse + views for the content at `head` (main), rebuilt when main moves."""
    raw = raw_path(folder, client)
    if raw is None:
        return None
    path = folder / f"{client}.{head[:10]}.duckdb"
    with _LOCK:
        return _served(folder, client, raw, path)


def _served(folder: Path, client: str, raw: Path, path: Path) -> Path:
    if not path.exists():
        import duckdb
        from insurance_context import sqlgen
        from insurance_context.client import Client
        for old in folder.glob(f"{client}.*.duckdb"):
            if old != raw and old.name.count(".") == 2 and not old.name.endswith(".building.duckdb"):
                old.unlink(missing_ok=True)
        tmp = folder / f"{client}.views.building.duckdb"
        shutil.copy(raw, tmp)
        con = duckdb.connect(str(tmp))
        try:
            sqlgen.install(Client(client), con)
            con.execute("CHECKPOINT")
        finally:
            con.close()
        tmp.rename(path)
    return path
