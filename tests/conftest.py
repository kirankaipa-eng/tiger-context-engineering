from __future__ import annotations

import shutil
from datetime import date

import duckdb
import pytest

from insurance_context import sqlgen
from insurance_context.client import Client
from insurance_context.fixtures import la_warehouse, mee_warehouse, payer_warehouse, pc_warehouse

VALUATION = pc_warehouse.VALUATION


@pytest.fixture(scope="session")
def summit() -> Client:
    return Client("summit_group")


@pytest.fixture(scope="session")
def acme() -> Client:
    return Client("acme_health")


@pytest.fixture(scope="session")
def meridian() -> Client:
    return Client("meridian_health")


@pytest.fixture(scope="session")
def northwind() -> Client:
    return Client("northwind_life")


@pytest.fixture(scope="session")
def summit_wh(tmp_path_factory, summit):
    path = tmp_path_factory.mktemp("summit") / "warehouse.duckdb"
    pc_warehouse.build(path)
    con = duckdb.connect(str(path))
    sqlgen.install(summit, con)
    con.close()
    return path


@pytest.fixture(scope="session")
def acme_wh(tmp_path_factory, acme):
    path = tmp_path_factory.mktemp("acme") / "warehouse.duckdb"
    payer_warehouse.build(path)
    con = duckdb.connect(str(path))
    sqlgen.install(acme, con)
    con.close()
    return path


@pytest.fixture(scope="session")
def meridian_wh(tmp_path_factory, meridian):
    path = tmp_path_factory.mktemp("meridian") / "warehouse.duckdb"
    payer_warehouse.build_meridian(path)
    con = duckdb.connect(str(path))
    sqlgen.install(meridian, con)
    con.close()
    return path


@pytest.fixture(scope="session")
def northwind_wh(tmp_path_factory, northwind):
    path = tmp_path_factory.mktemp("northwind") / "warehouse.duckdb"
    la_warehouse.build_northwind(path)
    con = duckdb.connect(str(path))
    sqlgen.install(northwind, con)
    con.close()
    return path


@pytest.fixture()
def summit_con(summit_wh):
    con = duckdb.connect(str(summit_wh), read_only=True)
    yield con
    con.close()


@pytest.fixture()
def summit_copy(summit_wh, tmp_path):
    """A writable copy for tests that mutate source data."""
    path = tmp_path / "copy.duckdb"
    shutil.copy(summit_wh, path)
    return path


@pytest.fixture()
def as_of() -> date:
    return VALUATION


@pytest.fixture(scope="session")
def plains() -> Client:
    return Client("plains_health")


@pytest.fixture(scope="session")
def plains_wh(tmp_path_factory, plains):
    if not all((mee_warehouse.DATA / f"{t}.parquet").exists() for t in mee_warehouse.TABLES):
        pytest.skip("Plains snapshot not present; run scripts/snapshot_mee_demo.py")
    path = tmp_path_factory.mktemp("plains") / "warehouse.duckdb"
    mee_warehouse.build_plains(path)
    con = duckdb.connect(str(path))
    sqlgen.install(plains, con)
    con.close()
    return path
