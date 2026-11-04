"""VM load graphs: collection from Hyper-V, rate computation, storage and time-bucketed queries.

Every ``metrics.interval_s`` seconds the worker asks every Up node of every
cluster for the raw cumulative counters of the managed VMs it runs
(``vm_metrics`` script): hypervisor virtual-processor run time, assigned and
demanded memory, and Hyper-V resource metering totals for disk and network.
Rates are averages between two consecutive readings of the same VM on the
same node; a node change (live migration, failover) or a counter reset only
skips one interval.  The previous reading is kept in ``VM.metrics_state``.
"""

from __future__ import annotations

import datetime as dt
import logging
import math
import time
import uuid
from typing import Any

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from ..errors import AppError
from ..hyperv import HyperVDriver, HyperVError
from ..models import VM, Cluster, VmMetric, utcnow
from .inventory import UP_STATES, endpoint_for
from .settings import get_setting

log = logging.getLogger(__name__)

MB = 1024 * 1024
RANGES = {"1h": 3600, "6h": 6 * 3600, "24h": 86400, "7d": 7 * 86400, "30d": 30 * 86400}
MAX_POINTS = 360
FIELDS = ("cpu_pct", "mem_assigned_mb", "mem_demand_mb", "disk_read_bps", "disk_write_bps", "disk_iops",
          "net_rx_bps", "net_tx_bps")
_RATES = (("disk_read_bps", "disk", "read_mb", MB), ("disk_write_bps", "disk", "write_mb", MB),
          ("disk_iops", "disk", "io", 1), ("net_rx_bps", "net", "rx_mb", MB), ("net_tx_bps", "net", "tx_mb", MB))
_reported: set[str] = set()


def _warn_once(message: str) -> None:
    if message not in _reported and len(_reported) < 1000:
        _reported.add(message)
        log.warning("%s", message)


def _counter_delta(cur: dict[str, Any] | None, prev: dict[str, Any] | None, key: str) -> float | None:
    if not cur or not prev or cur.get(key) is None or prev.get(key) is None:
        return None
    d = float(cur[key]) - float(prev[key])
    return d if d >= 0 else None  # counter reset (metering reset, node reboot)


def compute_sample(prev: dict[str, Any] | None, item: dict[str, Any], host: str, now: float,
                   logical_cpus: int | None) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Interval averages from two consecutive readings: (sample or None, new state or None)."""
    if item.get("state") != "Running":
        return None, None
    state = {"host": host, "t": now, "cpu": item.get("cpu"), "disk": item.get("disk"), "net": item.get("net")}
    same = bool(prev) and prev.get("host") == host
    before = prev if same else {}
    sample: dict[str, Any] = {"mem_assigned_mb": item.get("mem_assigned_mb") or None,
                              "mem_demand_mb": item.get("mem_demand_mb") or None}
    cpu = None
    cur_cpu, prev_cpu = item.get("cpu"), before.get("cpu")
    if cur_cpu and prev_cpu and cur_cpu.get("n") and cur_cpu.get("n") == prev_cpu.get("n"):
        span = float(cur_cpu["ts"]) - float(prev_cpu["ts"])
        run = float(cur_cpu["run"]) - float(prev_cpu["run"])
        if span > 0 and run >= 0:
            cpu = run / (cur_cpu["n"] * span) * 100
    elif not cur_cpu and item.get("cpu_usage") is not None and logical_cpus and item.get("vcpu"):
        cpu = float(item["cpu_usage"]) * logical_cpus / item["vcpu"]  # Get-VM CPUUsage is % of the whole host
    sample["cpu_pct"] = None if cpu is None else round(min(100.0, max(0.0, cpu)), 2)
    elapsed = now - float(before["t"]) if before.get("t") else 0.0
    for out, group, key, scale in _RATES:
        d = _counter_delta(item.get(group), before.get(group), key) if elapsed > 0 else None
        sample[out] = None if d is None else round(d * scale / elapsed, 2)
    return sample, state


def collect_cluster(db: Session, driver: HyperVDriver, cluster: Cluster) -> int:
    """One collection round for a cluster; returns the number of VMs read."""
    vms = db.scalars(select(VM).where(VM.cluster_id == cluster.id, VM.hv_id.is_not(None),
                                      VM.status == "ready")).all()
    if not vms:
        return 0
    by_id = {vm.hv_id.upper(): vm for vm in vms if vm.hv_id}
    ids = sorted(by_id)
    now = time.time()
    ts = dt.datetime.fromtimestamp(round(now), dt.timezone.utc)
    seen: set[uuid.UUID] = set()
    for h in cluster.hosts:
        if not h.in_cluster or (cluster.kind == "cluster" and h.state not in UP_STATES):
            continue
        try:
            res = driver.vm_metrics(endpoint_for(cluster, h), ids)
        except HyperVError as exc:
            _warn_once(f"VM metrics of {cluster.name}/{h.name} failed: {exc}")
            continue
        for err in res.get("errors") or []:
            _warn_once(f"VM metrics on {h.name}: {err}")
        lps = res.get("logical_cpus") or h.logical_cpus
        for item in res.get("vms") or []:
            vm = by_id.get(str(item.get("id", "")).upper())
            if vm is None or vm.id in seen:
                continue
            seen.add(vm.id)
            sample, state = compute_sample(vm.metrics_state, item, str(res.get("host") or h.name), now, lps)
            vm.metrics_state = state
            if sample is not None:
                db.merge(VmMetric(vm_id=vm.id, ts=ts, **sample))
    for vm in vms:
        if vm.id not in seen and vm.metrics_state is not None:
            vm.metrics_state = None
    return len(seen)


def collect_all(db: Session, driver: HyperVDriver) -> int:
    total = 0
    for cluster in db.scalars(select(Cluster)).all():
        try:
            total += collect_cluster(db, driver, cluster)
            db.commit()
        except Exception as exc:  # noqa: BLE001 - one cluster must not stop the others
            db.rollback()
            _warn_once(f"VM metrics of {cluster.name} failed: {exc}")
    return total


def purge(db: Session, retention_days: int) -> int:
    cutoff = utcnow() - dt.timedelta(days=max(1, int(retention_days)))
    res = db.execute(delete(VmMetric).where(VmMetric.ts < cutoff))
    return int(res.rowcount or 0)


def series(db: Session, vm: VM, range_key: str) -> dict[str, Any]:
    """Time-bucketed averages for the graphs; gaps (VM off, collection stopped) become nulls."""
    if range_key not in RANGES:
        raise AppError("invalid_value", "Unknown range", field="range")
    span = RANGES[range_key]
    interval = int(get_setting(db, "metrics").get("interval_s") or 60)
    step = max(interval, 60, math.ceil(span / MAX_POINTS / 60) * 60)
    end = utcnow()
    start = end - dt.timedelta(seconds=span)
    rows = db.execute(text(
        "SELECT date_bin(make_interval(secs => :step), ts, TIMESTAMPTZ '2001-01-01 00:00:00+00') AS bucket, "
        "avg(cpu_pct), avg(mem_assigned_mb), avg(mem_demand_mb), avg(disk_read_bps), avg(disk_write_bps), "
        "avg(disk_iops), avg(net_rx_bps), avg(net_tx_bps) "
        "FROM vm_metrics WHERE vm_id = :vm AND ts >= :start GROUP BY bucket ORDER BY bucket"),
        {"step": step, "vm": vm.id, "start": start}).all()
    t: list[int] = []
    cols: dict[str, list[float | None]] = {f: [] for f in FIELDS}
    prev: dt.datetime | None = None
    for row in rows:
        bucket = row[0]
        if prev is not None and (bucket - prev).total_seconds() > step * 1.5:
            t.append(int((prev + dt.timedelta(seconds=step)).timestamp() * 1000))
            for f in FIELDS:
                cols[f].append(None)
        t.append(int(bucket.timestamp() * 1000))
        for f, v in zip(FIELDS, row[1:], strict=True):
            cols[f].append(None if v is None else round(float(v), 2))
        prev = bucket
    latest = db.scalar(select(VmMetric).where(VmMetric.vm_id == vm.id).order_by(VmMetric.ts.desc()).limit(1))
    return {
        "range": range_key, "step_s": step, "interval_s": interval, "from": start.isoformat(), "to": end.isoformat(),
        "memory_mb": vm.memory_mb, "vcpu": vm.vcpu, "t": t, "series": cols,
        "latest": ({"ts": latest.ts.isoformat(), **{f: getattr(latest, f) for f in FIELDS}} if latest else None),
    }


__all__ = ["FIELDS", "RANGES", "collect_all", "collect_cluster", "compute_sample", "purge", "series"]
