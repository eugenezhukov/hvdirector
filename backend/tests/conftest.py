"""Test fixtures.

Integration tests need a PostgreSQL database (default:
postgresql+psycopg://hvdirector:hvdirector@127.0.0.1:5432/hvdirector_test,
override with HVD_TEST_DATABASE_URL).  The schema is dropped and recreated.
They use the Hyper-V simulator, so no Hyper-V host is required.
"""

from __future__ import annotations

import os
import tempfile

import pytest

_tmp = tempfile.mkdtemp(prefix="hvd-test-")
os.environ["HVD_CONFIG_FILE"] = os.path.join(_tmp, "none.env")
os.environ["HVD_DATABASE_URL"] = os.environ.get(
    "HVD_TEST_DATABASE_URL", "postgresql+psycopg://hvdirector:hvdirector@127.0.0.1:5432/hvdirector_test")
os.environ["HVD_SECRET_KEY"] = "test-secret-key-for-hvdirector"
os.environ["HVD_HYPERV_DRIVER"] = "fake"
os.environ["HVD_FAKE_STATE"] = os.path.join(_tmp, "fake.json")
os.environ["HVD_FAKE_DELAY"] = "0"
os.environ["HVD_COOKIE_SECURE"] = "false"
os.environ["HVD_FRONTEND_DIST"] = os.path.join(_tmp, "no-dist")
os.environ["HVD_LOG_LEVEL"] = "WARNING"

TMP_DIR = _tmp


def _db_available() -> bool:
    from sqlalchemy import create_engine, text

    try:
        eng = create_engine(os.environ["HVD_DATABASE_URL"])
        with eng.connect() as c:
            c.execute(text("select 1"))
        eng.dispose()
        return True
    except Exception:  # noqa: BLE001
        return False


@pytest.fixture(scope="session")
def database():
    if not _db_available():
        pytest.skip("PostgreSQL test database not available")
    from sqlalchemy import text

    from hvdirector.cli import main as manage
    from hvdirector.db import get_engine

    eng = get_engine()
    with eng.begin() as c:
        c.execute(text("drop schema public cascade"))
        c.execute(text("create schema public"))
    assert manage(["migrate"]) == 0
    assert manage(["create-admin", "--username", "admin", "--password", "Admin123!"]) == 0
    from hvdirector.db import session_scope
    from hvdirector.services.settings import set_setting

    with session_scope() as db:
        set_setting(db, "iso_share", {"backend": "local", "local_path": os.path.join(TMP_DIR, "iso")})
    return eng


@pytest.fixture(scope="session")
def app(database):
    from hvdirector.main import create_app

    return create_app()


class Api:
    """Small wrapper around TestClient that keeps the CSRF token."""

    def __init__(self, app) -> None:  # noqa: ANN001
        from fastapi.testclient import TestClient

        self.client = TestClient(app)
        self.csrf = ""

    def login(self, username: str, password: str, org: str | None = None) -> dict:
        r = self.client.post("/api/auth/login", json={"username": username, "password": password, "org": org})
        assert r.status_code == 200, r.text
        data = r.json()
        self.csrf = data["csrf_token"]
        return data

    def _h(self) -> dict:
        return {"X-CSRF-Token": self.csrf}

    def get(self, url: str, **kw):  # noqa: ANN003, ANN201
        return self.client.get(url, **kw)

    def post(self, url: str, json=None, **kw):  # noqa: ANN001, ANN003, ANN201
        return self.client.post(url, json=json, headers=self._h(), **kw)

    def put(self, url: str, **kw):  # noqa: ANN003, ANN201
        return self.client.put(url, headers=self._h(), **kw)

    def patch(self, url: str, json=None):  # noqa: ANN001, ANN201
        return self.client.patch(url, json=json, headers=self._h())

    def delete(self, url: str):  # noqa: ANN201
        return self.client.delete(url, headers=self._h())


@pytest.fixture()
def api_factory(app):
    return lambda: Api(app)


def run_tasks() -> list[dict]:
    """Execute every queued task synchronously (stand-in for hvdirector-worker)."""
    from sqlalchemy import select

    from hvdirector.db import SessionLocal
    from hvdirector.models import Task, utcnow
    from hvdirector.services.taskqueue import task_to_dict
    from hvdirector.worker import Worker

    worker = Worker(threads=1)
    done = []
    for _ in range(100):
        db = SessionLocal()
        t = db.scalars(select(Task).where(Task.status == "queued").order_by(Task.created_at)).first()
        if t is None:
            db.close()
            break
        t.status = "running"
        t.started_at = utcnow()
        tid = t.id
        db.commit()
        db.close()
        worker.active_count += 1
        worker.execute(tid)
        db = SessionLocal()
        done.append(task_to_dict(db.get(Task, tid)))
        db.close()
    worker.pool.shutdown(wait=False)
    return done
