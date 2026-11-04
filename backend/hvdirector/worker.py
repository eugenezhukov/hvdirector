"""hvdirector-worker: executes queued tasks and periodically syncs VM states.

A single dispatcher thread claims queued tasks (FOR UPDATE SKIP LOCKED) and
hands them to a thread pool.  Tasks sharing a lock key (e.g. the same VM) are
executed one after another.
"""

from __future__ import annotations

import logging
import signal
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import select, update

from .config import get_config
from .db import SessionLocal
from .hyperv import HyperVError, get_driver
from .logging_setup import setup_logging
from .models import Task, UserSession, ConsoleTicket, Iso, utcnow
from .services import vmops
from .services.isostore import IsoStore
from .services.settings import get_setting

log = logging.getLogger("hvdirector.worker")


def _set_progress(task_id: uuid.UUID, pct: int, message: str) -> None:
    db = SessionLocal()
    try:
        db.execute(update(Task).where(Task.id == task_id).values(progress=pct, message=message))
        db.commit()
    finally:
        db.close()


class Worker:
    def __init__(self, threads: int | None = None) -> None:
        cfg = get_config()
        self.driver = get_driver()
        self.pool = ThreadPoolExecutor(max_workers=threads or cfg.worker_threads, thread_name_prefix="task")
        self.active_keys: set[str] = set()
        self.active_count = 0
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.last_sync = 0.0
        self.last_housekeeping = 0.0
        self.sync_running = False

    # ------------------------------------------------------------- lifecycle
    def recover(self) -> None:
        db = SessionLocal()
        try:
            n = db.execute(
                update(Task).where(Task.status == "running")
                .values(status="error", message="Interrupted by worker restart", finished_at=utcnow())
            ).rowcount
            db.commit()
            if n:
                log.warning("Marked %d interrupted task(s) as failed", n)
        finally:
            db.close()

    def run_forever(self) -> None:
        self.recover()
        log.info("Worker started (driver=%s)", self.driver.name)
        while not self.stop_event.is_set():
            try:
                self.dispatch()
                self.periodic()
            except Exception:  # noqa: BLE001
                log.exception("Dispatcher error")
            self.stop_event.wait(0.5)
        self.pool.shutdown(wait=True)
        log.info("Worker stopped")

    # -------------------------------------------------------------- dispatch
    def dispatch(self) -> None:
        with self.lock:
            free_slots = self.pool._max_workers - self.active_count  # noqa: SLF001
        if free_slots <= 0:
            return
        db = SessionLocal()
        try:
            rows = db.scalars(
                select(Task).where(Task.status == "queued").order_by(Task.created_at)
                .limit(50).with_for_update(skip_locked=True)
            ).all()
            claimed: list[uuid.UUID] = []
            with self.lock:
                for t in rows:
                    if len(claimed) >= free_slots:
                        break
                    if t.lock_key and t.lock_key in self.active_keys:
                        continue
                    if t.lock_key:
                        self.active_keys.add(t.lock_key)
                    t.status = "running"
                    t.started_at = utcnow()
                    t.progress = 1
                    claimed.append(t.id)
                    self.active_count += 1
            db.commit()
        finally:
            db.close()
        for task_id in claimed:
            self.pool.submit(self.execute, task_id)

    def execute(self, task_id: uuid.UUID) -> None:
        db = SessionLocal()
        lock_key = None
        try:
            task = db.get(Task, task_id)
            assert task is not None
            lock_key = task.lock_key
            fn = vmops.HANDLERS.get(task.kind)
            log.info("Task %s %s started (%s)", task.kind, task.id, task.target_name)
            if fn is None:
                raise RuntimeError(f"No handler for task kind {task.kind}")
            ctx = vmops.TaskContext(task.id, _set_progress)
            result = fn(db, self.driver, task, ctx) or {}
            # Explicit UPDATE: progress messages were written by another session, so the
            # ORM copy of the row is stale and would skip "unchanged" columns.
            db.execute(update(Task).where(Task.id == task_id).values(
                status="success", progress=100, message="", result=result, finished_at=utcnow()))
            db.commit()
            log.info("Task %s %s succeeded", task.kind, task_id)
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            msg = str(exc) if isinstance(exc, HyperVError) else f"{exc}"
            if not isinstance(exc, HyperVError):
                log.exception("Task %s failed", task_id)
            else:
                log.warning("Task %s failed: %s", task_id, msg)
            try:
                task = db.get(Task, task_id)
                hook = vmops.ERROR_HOOKS.get(task.kind) if task else None
                if hook is not None:
                    try:
                        hook(db, self.driver, task, exc)
                    except Exception:  # noqa: BLE001
                        log.exception("Error hook for %s failed", task_id)
                        db.rollback()
                        task = db.get(Task, task_id)
                db.execute(update(Task).where(Task.id == task_id).values(
                    status="error", message=msg[:4000], finished_at=utcnow()))
                db.commit()
            except Exception:  # noqa: BLE001
                log.exception("Cannot record failure of task %s", task_id)
                db.rollback()
        finally:
            db.close()
            with self.lock:
                self.active_count -= 1
                if lock_key:
                    self.active_keys.discard(lock_key)

    # -------------------------------------------------------------- periodic
    def periodic(self) -> None:
        now = time.monotonic()
        db = SessionLocal()
        try:
            interval = int(get_setting(db, "sync").get("interval_s", 60))
        finally:
            db.close()
        if interval > 0 and now - self.last_sync >= interval and not self.sync_running:
            self.last_sync = now
            self.sync_running = True
            self.pool.submit(self._sync)
        if now - self.last_housekeeping >= 600:
            self.last_housekeeping = now
            self.pool.submit(self._housekeeping)

    def _sync(self) -> None:
        db = SessionLocal()
        try:
            vmops.sync_all(db, self.driver)
        finally:
            db.close()
            self.sync_running = False

    def _housekeeping(self) -> None:
        db = SessionLocal()
        try:
            now = utcnow()
            db.query(UserSession).filter(UserSession.expires_at < now).delete()
            db.query(ConsoleTicket).filter(ConsoleTicket.expires_at < now).delete()
            # Abandoned uploads (older than 24 h)
            store = IsoStore.from_setting(get_setting(db, "iso_share"))
            stale = db.scalars(select(Iso).where(Iso.status == "uploading")).all()
            for iso in stale:
                if (now - iso.created_at).total_seconds() > 86400:
                    try:
                        store.delete(iso.file_name)
                    except Exception as exc:  # noqa: BLE001
                        log.warning("Cannot delete stale upload %s: %s", iso.file_name, exc)
                        continue
                    db.delete(iso)
            db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()
            log.exception("Housekeeping failed")
        finally:
            db.close()


def main() -> None:
    cfg = get_config()
    setup_logging(cfg.log_level)
    worker = Worker()

    def _stop(*_: object) -> None:
        worker.stop_event.set()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    worker.run_forever()


if __name__ == "__main__":
    main()
