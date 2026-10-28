"""Where a captured model's SQL runs: Databricks, or a local DuckDB stand-in for development.

Databricks (pip install 'insurance-context[databricks]'):
    DATABRICKS_SERVER_HOSTNAME   adb-....azuredatabricks.net
    DATABRICKS_HTTP_PATH         /sql/1.0/warehouses/<id>
    DATABRICKS_CLIENT_ID / DATABRICKS_CLIENT_SECRET   OAuth M2M as a service principal (preferred;
                                 databricks-sdk's oauth_service_principal mints and refreshes tokens)
    DATABRICKS_TOKEN             a personal access token, used only when no client id/secret is set
Give the principal SELECT on the captured tables and nothing else. Connections are pooled (a few
idle ones kept, a stale one replaced and the query retried once) and every statement runs under
the warehouse's STATEMENT_TIMEOUT (`timeout` seconds). The local stand-in opens the DuckDB file
read-only per query, with external access off, and interrupts a query that outlives `timeout`.
"""

from __future__ import annotations

import os
import queue
import threading
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .bilayer import BILayer


def _layer_error(msg: str) -> Exception:
    from .bilayer import LayerError
    return LayerError(msg)


class Warehouse:
    """Databricks (DATABRICKS_SERVER_HOSTNAME, DATABRICKS_HTTP_PATH and OAuth M2M or a PAT), or the
    local DuckDB stand-in built from the .pbix's own rows."""

    def __init__(self, kind: str, target: str, http_path: str | None = None, timeout: float | None = None,
                 pool: int = 4):
        self.kind, self.target, self.timeout = kind, target, timeout
        self.dialect = "databricks" if kind == "databricks" else "duckdb"
        self.http_path = http_path or os.environ.get("DATABRICKS_HTTP_PATH")
        self.auth = None
        if kind == "databricks":
            self.auth = ("oauth-m2m" if os.environ.get("DATABRICKS_CLIENT_ID")
                         and os.environ.get("DATABRICKS_CLIENT_SECRET") else "pat")
        self._idle: queue.LifoQueue = queue.LifoQueue(maxsize=pool)

    @classmethod
    def open(cls, folder: str | Path, layer: "BILayer", prefer: str = "auto",
             timeout: float | None = None) -> "Warehouse":
        from . import llm
        llm.configured()  # loads .env, which may hold the Databricks settings
        if prefer in ("auto", "databricks") and os.environ.get("DATABRICKS_SERVER_HOSTNAME"):
            return cls("databricks", os.environ["DATABRICKS_SERVER_HOSTNAME"], timeout=timeout)
        if prefer == "databricks":
            raise _layer_error("set DATABRICKS_SERVER_HOSTNAME, DATABRICKS_HTTP_PATH and DATABRICKS_CLIENT_ID/"
                               "DATABRICKS_CLIENT_SECRET (or DATABRICKS_TOKEN)")
        catalog = (layer.spec.get("warehouse") or {}).get("catalog", "pilot")
        path = Path(folder) / f"{catalog}.duckdb"
        if not path.exists():
            raise _layer_error(f"no data to run on: no local copy of the rows at {path} (a TMDL folder has none; "
                               "capture the .pbix with `ictx pbi import` or `ictx pbi export`), and no Databricks "
                               "settings (DATABRICKS_SERVER_HOSTNAME, DATABRICKS_HTTP_PATH, ... in .env)")
        return cls("local", str(path), timeout=timeout)

    def _connect(self):
        from databricks import sql as dbsql  # pip install databricks-sql-connector
        kw = {"server_hostname": self.target, "http_path": self.http_path}
        if not self.http_path:
            raise _layer_error("set DATABRICKS_HTTP_PATH (the SQL warehouse's HTTP path)")
        if self.timeout:
            kw["session_configuration"] = {"STATEMENT_TIMEOUT": str(int(self.timeout))}
        if self.auth == "oauth-m2m":
            host, cid, secret = self.target, os.environ["DATABRICKS_CLIENT_ID"], os.environ["DATABRICKS_CLIENT_SECRET"]

            def credentials_provider():
                from databricks.sdk.core import Config, oauth_service_principal  # pip install databricks-sdk
                return oauth_service_principal(Config(host=f"https://{host}", client_id=cid, client_secret=secret))
            kw["credentials_provider"] = credentials_provider
        elif os.environ.get("DATABRICKS_TOKEN"):
            kw["access_token"] = os.environ["DATABRICKS_TOKEN"]
        else:
            raise _layer_error("set DATABRICKS_CLIENT_ID and DATABRICKS_CLIENT_SECRET (or DATABRICKS_TOKEN)")
        return dbsql.connect(**kw)

    def _databricks(self, sql: str, limit: int | None) -> tuple[list[str], list[tuple]]:
        for attempt in (0, 1):
            try:
                con, reused = self._idle.get_nowait(), True
            except queue.Empty:
                con, reused = self._connect(), False
            try:
                with con.cursor() as cur:
                    cur.execute(sql)
                    rows = cur.fetchmany(limit) if limit else cur.fetchall()
                    out = [d[0] for d in cur.description], [tuple(r) for r in rows]
            except Exception as exc:
                try:
                    con.close()
                except Exception:  # noqa: BLE001, S110 - it is being discarded anyway
                    pass
                # a pooled connection may have gone stale; a SQL error would only fail again
                if reused and attempt == 0 and type(exc).__name__ != "ServerOperationError":
                    continue
                raise
            try:
                self._idle.put_nowait(con)
            except queue.Full:
                con.close()
            return out
        raise AssertionError("unreachable")

    def query(self, sql: str, limit: int | None = None) -> tuple[list[str], list[tuple]]:
        """Columns and rows (at most `limit`); raises on a SQL error or a statement over `timeout`."""
        if self.kind == "databricks":
            return self._databricks(sql, limit)
        import duckdb
        con = duckdb.connect(self.target, read_only=True,
                             config={"enable_external_access": False, "lock_configuration": True})
        timer = threading.Timer(self.timeout, con.interrupt) if self.timeout else None
        try:
            if timer:
                timer.start()
            try:
                cur = con.execute(sql)
                return [d[0] for d in cur.description], cur.fetchmany(limit) if limit else cur.fetchall()
            except duckdb.InterruptException:
                raise _layer_error(f"query cancelled after the {self.timeout:g}s timeout") from None
        finally:
            if timer:
                timer.cancel()
            con.close()

    def ping(self) -> tuple[bool, str]:
        try:
            self.query("SELECT 1")
            return True, "ok"
        except Exception as exc:  # noqa: BLE001 - health reports it
            return False, f"{type(exc).__name__}: {exc}"

    def close(self) -> None:
        while True:
            try:
                self._idle.get_nowait().close()
            except queue.Empty:
                return
            except Exception:  # noqa: BLE001, S110
                pass

    @property
    def about(self) -> str:
        """Where a local stand-in's rows come from: the .pbix's own, unless a note beside it says otherwise
        (<db>.about, written by whatever generated the rows)."""
        note = Path(str(self.target) + ".about")
        return note.read_text(encoding="utf-8").strip() if note.exists() else "the .pbix's own rows"

    def describe(self) -> str:
        return f"Databricks ({self.target})" if self.kind == "databricks" else \
            f"local stand-in {Path(self.target).name} ({self.about}; Databricks not configured)"
