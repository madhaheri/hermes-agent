"""Read-only, content-free Hermes operations monitor.

The collector reads only bounded host metadata and Hermes' canonical state files.  It does
not inspect processes, transcripts, prompts, outputs, identifiers, configuration secrets, or
the network, and it never writes.  ``OperationsMonitor`` keeps alert state in memory: callers
that need transition alerts should retain one instance (the CLI's ``--watch`` mode does so).

Default operational thresholds (warning / critical / recovery) are deliberately explicit:

* gateway heartbeat age: 90s / 180s / below 60s;
* cron ticker heartbeat age: 120s / 300s / below 60s;
* cron job lateness: 300s / 900s / at most 60s; any persisted failed job warns;
* root disk and memory use: 85% / 95% / below 80%; swap: 50% / 80% / below 40%;
* sustained CPU: 85% / 95% for three samples / below 75%;
* session DB: 2GiB / 5GiB / below 1.5GiB and 10k / 25k sessions / below 8k;
* delegation saturation: 80% / 100% of the supplied capacity / below 60%.

An unavailable or malformed source is ``unknown``, never healthy or unhealthy.  Inputs are
size-capped, counts are capped, SQLite is opened ``mode=ro``, and emitted alerts contain only
allowlisted signal names, numeric values, units, and states.
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Optional
from urllib.parse import quote

from hermes_constants import get_hermes_home

_MAX_MARKER_BYTES = 16_384
_MAX_JOBS_BYTES = 2 * 1024 * 1024
_MAX_JOBS = 10_000
_COUNT_CAP = 10_000_000


@dataclass(frozen=True, slots=True)
class Threshold:
    warning: float
    critical: float
    recovery: float
    samples: int = 1


@dataclass(frozen=True, slots=True)
class MonitorThresholds:
    gateway_age_s: Threshold = Threshold(90, 180, 60)
    ticker_age_s: Threshold = Threshold(120, 300, 60)
    cron_lateness_s: Threshold = Threshold(300, 900, 60)
    disk_percent: Threshold = Threshold(85, 95, 80)
    memory_percent: Threshold = Threshold(85, 95, 80)
    swap_percent: Threshold = Threshold(50, 80, 40)
    cpu_percent: Threshold = Threshold(85, 95, 75, samples=3)
    session_db_bytes: Threshold = Threshold(2 * 1024**3, 5 * 1024**3, 1536 * 1024**2)
    session_count: Threshold = Threshold(10_000, 25_000, 8_000)
    delegation_percent: Threshold = Threshold(80, 100, 60)


@dataclass(frozen=True, slots=True)
class Signal:
    name: str
    state: str
    value: Optional[float] = None
    unit: str = ""


@dataclass(frozen=True, slots=True)
class Alert:
    signal: str
    transition: str
    state: str
    value: Optional[float]
    unit: str
    observed_at: float

    def to_dict(self) -> dict[str, object]:
        return {
            "event": "hermes_ops_alert",
            "signal": self.signal,
            "transition": self.transition,
            "state": self.state,
            "value": self.value,
            "unit": self.unit,
            "observed_at": self.observed_at,
        }


@dataclass(frozen=True, slots=True)
class ResourceSample:
    disk_percent: Optional[float]
    memory_percent: Optional[float]
    swap_percent: Optional[float]
    cpu_percent: Optional[float]


def _bounded_text(path: Path, limit: int) -> Optional[str]:
    try:
        if path.stat().st_size > limit:
            return None
        with path.open("r", encoding="utf-8") as handle:
            value = handle.read(limit + 1)
        return value if len(value) <= limit else None
    except (OSError, UnicodeError):
        return None


def _percent(used: float, total: float) -> Optional[float]:
    if total <= 0 or used < 0:
        return None
    return min(100.0, max(0.0, used * 100.0 / total))


class HostResourceSampler:
    """Linux host sampler. CPU is a delta between calls, never a blocking sample."""

    def __init__(self) -> None:
        self._cpu: Optional[tuple[int, int]] = None

    def __call__(self) -> ResourceSample:
        disk = memory = swap = cpu = None
        try:
            usage = shutil.disk_usage(Path("/"))
            disk = _percent(usage.used, usage.total)
        except OSError:
            pass
        raw = _bounded_text(Path("/proc/meminfo"), _MAX_MARKER_BYTES)
        if raw is not None:
            values: dict[str, int] = {}
            for line in raw.splitlines():
                fields = line.replace(":", "").split()
                if len(fields) >= 2 and fields[0] in {"MemTotal", "MemAvailable", "SwapTotal", "SwapFree"}:
                    try:
                        values[fields[0]] = int(fields[1])
                    except ValueError:
                        pass
            memory = _percent(values.get("MemTotal", 0) - values.get("MemAvailable", 0), values.get("MemTotal", 0))
            swap = _percent(values.get("SwapTotal", 0) - values.get("SwapFree", 0), values.get("SwapTotal", 0))
            if values.get("SwapTotal") == 0:
                swap = 0.0
        stat = _bounded_text(Path("/proc/stat"), _MAX_MARKER_BYTES)
        if stat:
            try:
                fields = [int(value) for value in stat.splitlines()[0].split()[1:]]
                total, idle = sum(fields), fields[3] + (fields[4] if len(fields) > 4 else 0)
                if self._cpu is not None and total > self._cpu[0]:
                    cpu = _percent((total - self._cpu[0]) - (idle - self._cpu[1]), total - self._cpu[0])
                self._cpu = (total, idle)
            except (IndexError, ValueError):
                pass
        return ResourceSample(disk, memory, swap, cpu)


def _age_from_epoch(path: Path, now: float) -> Optional[float]:
    raw = _bounded_text(path, _MAX_MARKER_BYTES)
    try:
        return max(0.0, now - float(raw.strip())) if raw is not None else None
    except (AttributeError, ValueError, OverflowError):
        return None


def _gateway_age(home: Path, now: float) -> Optional[float]:
    raw = _bounded_text(home / "state" / "gateway.heartbeat", _MAX_MARKER_BYTES)
    try:
        value = json.loads(raw) if raw is not None else None
        stamp = datetime.fromisoformat(value["updated_at"])
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return max(0.0, now - stamp.timestamp())
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _parse_timestamp(value: object) -> Optional[float]:
    try:
        stamp = datetime.fromisoformat(str(value))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return stamp.timestamp()
    except (TypeError, ValueError, OverflowError):
        return None


def _cron_metadata(home: Path, now: float) -> tuple[Optional[int], Optional[float]]:
    raw = _bounded_text(home / "cron" / "jobs.json", _MAX_JOBS_BYTES)
    if raw is None:
        return None, None
    try:
        decoded = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None, None
    jobs = decoded.get("jobs") if isinstance(decoded, dict) else decoded
    if not isinstance(jobs, list) or len(jobs) > _MAX_JOBS:
        return None, None
    failures = 0
    max_lateness = 0.0
    saw_schedule = False
    for job in jobs:
        if not isinstance(job, dict) or not job.get("enabled", True) or job.get("state") in {"paused", "completed"}:
            continue
        status = str(job.get("last_status") or "").lower()
        if status in {"error", "failed", "failure"} or bool(job.get("last_error")):
            failures += 1
        due = _parse_timestamp(job.get("next_run_at"))
        if due is not None:
            saw_schedule = True
            max_lateness = max(max_lateness, now - due)
    return failures, max(0.0, max_lateness) if saw_schedule else 0.0


def _session_metadata(home: Path) -> tuple[Optional[int], Optional[int]]:
    path = home / "state.db"
    try:
        size = path.stat().st_size
        # immutable=1 prevents SQLite from creating journal/SHM files. The tradeoff is a
        # deliberately approximate snapshot when another process has uncheckpointed WAL data.
        uri = f"file:{quote(str(path.resolve()))}?mode=ro&immutable=1"
        with sqlite3.connect(uri, uri=True, timeout=0.25) as conn:
            conn.execute("PRAGMA query_only=ON")
            row = conn.execute(
                "SELECT COUNT(*) FROM (SELECT 1 FROM sessions LIMIT ?)", (_COUNT_CAP + 1,)
            ).fetchone()
        count = min(_COUNT_CAP, max(0, int(row[0]))) if row else None
        return size, count
    except (OSError, sqlite3.Error, TypeError, ValueError):
        return None, None


def _delegation_metadata() -> tuple[int, int] | None:
    """Use the live delegation registry and its canonical configured slot resolver."""
    try:
        from tools.async_delegation import active_count
        from tools.delegate_tool_config import _get_max_concurrent_children

        return max(0, int(active_count())), max(1, int(_get_max_concurrent_children()))
    except Exception:
        return None


def _classified(name: str, value: Optional[float], unit: str, threshold: Threshold) -> Signal:
    if value is None or not math.isfinite(value):
        return Signal(name, "unknown", unit=unit)
    if value >= threshold.critical:
        state = "critical"
    elif value >= threshold.warning:
        state = "warning"
    else:
        state = "healthy"
    return Signal(name, state, round(value, 2), unit)


@dataclass(slots=True)
class OperationsMonitor:
    home: Optional[Path] = None
    clock: Callable[[], float] = time.time
    resources: Callable[[], ResourceSample] = field(default_factory=HostResourceSampler)
    delegation_reader: Callable[[], tuple[int, int] | None] = _delegation_metadata
    thresholds: MonitorThresholds = field(default_factory=MonitorThresholds)
    _active: dict[str, str] = field(default_factory=dict, init=False)
    _breaches: dict[str, int] = field(default_factory=dict, init=False)

    def collect(self, *, observed_at: Optional[float] = None) -> list[Signal]:
        """Collect a bounded snapshot. No source failure propagates or changes external state."""
        home = Path(self.home) if self.home is not None else get_hermes_home()
        now = self.clock() if observed_at is None else observed_at
        try:
            resources = self.resources()
        except Exception:
            resources = ResourceSample(None, None, None, None)
        failures, lateness = _cron_metadata(home, now)
        db_size, sessions = _session_metadata(home)
        try:
            delegation = self.delegation_reader()
            delegation_pct = _percent(delegation[0], delegation[1]) if delegation is not None else None
        except Exception:
            delegation_pct = None
        t = self.thresholds
        signals = [
            _classified("gateway_freshness", _gateway_age(home, now), "seconds", t.gateway_age_s),
            _classified("ticker_freshness", _age_from_epoch(home / "cron" / "ticker_heartbeat", now), "seconds", t.ticker_age_s),
            Signal("cron_failures", "warning" if failures else ("healthy" if failures == 0 else "unknown"), float(failures) if failures is not None else None, "jobs"),
            _classified("cron_lateness", lateness, "seconds", t.cron_lateness_s),
            _classified("root_disk", resources.disk_percent, "percent", t.disk_percent),
            _classified("memory", resources.memory_percent, "percent", t.memory_percent),
            _classified("swap", resources.swap_percent, "percent", t.swap_percent),
            _classified("sustained_cpu", resources.cpu_percent, "percent", t.cpu_percent),
            _classified("session_db_size", float(db_size) if db_size is not None else None, "bytes", t.session_db_bytes),
            _classified("session_count", float(sessions) if sessions is not None else None, "sessions", t.session_count),
            _classified("delegation_saturation", delegation_pct, "percent", t.delegation_percent),
        ]
        return signals

    def sample(self) -> list[Alert]:
        """Return only state transitions: breach/escalation and hysteretic recovery."""
        observed_at = self.clock()
        alerts: list[Alert] = []
        thresholds = self._threshold_by_name()
        for signal in self.collect(observed_at=observed_at):
            threshold = thresholds.get(signal.name)
            old = self._active.get(signal.name)
            if signal.state == "unknown":
                self._breaches[signal.name] = 0
                continue
            unhealthy = signal.state in {"warning", "critical"}
            if unhealthy:
                self._breaches[signal.name] = self._breaches.get(signal.name, 0) + 1
                required = threshold.samples if threshold else 1
                severity = {"warning": 1, "critical": 2}
                is_escalation = old is None or severity[signal.state] > severity[old]
                if self._breaches[signal.name] >= required and is_escalation:
                    transition = "opened" if old is None else "escalated"
                    self._active[signal.name] = signal.state
                    alerts.append(Alert(signal.name, transition, signal.state, signal.value, signal.unit, observed_at))
                continue
            self._breaches[signal.name] = 0
            if old is not None and signal.value is not None and (threshold is None or signal.value < threshold.recovery):
                self._active.pop(signal.name, None)
                alerts.append(Alert(signal.name, "recovered", "healthy", signal.value, signal.unit, observed_at))
        return alerts

    def _threshold_by_name(self) -> dict[str, Threshold]:
        t = self.thresholds
        return {
            "gateway_freshness": t.gateway_age_s, "ticker_freshness": t.ticker_age_s,
            "cron_lateness": t.cron_lateness_s, "root_disk": t.disk_percent,
            "memory": t.memory_percent, "swap": t.swap_percent, "sustained_cpu": t.cpu_percent,
            "session_db_size": t.session_db_bytes, "session_count": t.session_count,
            "delegation_saturation": t.delegation_percent,
        }


def _emit(alerts: Iterable[Alert]) -> None:
    for alert in alerts:
        print(json.dumps(alert.to_dict(), separators=(",", ":"), sort_keys=True), flush=True)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only Hermes operations monitor (alert-only JSONL output)")
    parser.add_argument("--home", type=Path, help="Hermes home to observe; defaults to the active profile home")
    parser.add_argument("--watch", type=float, metavar="SECONDS", help="sample continuously at this interval")
    args = parser.parse_args(argv)
    if args.watch is not None and args.watch < 1:
        parser.error("--watch must be at least 1 second")
    monitor = OperationsMonitor(home=args.home)
    while True:
        _emit(monitor.sample())
        if args.watch is None:
            return 0
        time.sleep(args.watch)


if __name__ == "__main__":
    raise SystemExit(main())
