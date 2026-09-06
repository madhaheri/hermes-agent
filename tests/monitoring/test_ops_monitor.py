import json
import sqlite3
from datetime import datetime, timezone

from agent.monitoring import ops_monitor
from agent.monitoring.ops_monitor import HostResourceSampler, OperationsMonitor, ResourceSample


NOW = 2_000_000_000.0


def _healthy_home(tmp_path):
    home = tmp_path / "hermes"
    (home / "state").mkdir(parents=True)
    (home / "cron").mkdir()
    stamp = datetime.fromtimestamp(NOW - 5, timezone.utc).isoformat()
    (home / "state" / "gateway.heartbeat").write_text(json.dumps({"updated_at": stamp}))
    (home / "cron" / "ticker_heartbeat").write_text(str(NOW - 5))
    (home / "cron" / "jobs.json").write_text(json.dumps({"jobs": []}))
    with sqlite3.connect(home / "state.db") as conn:
        conn.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY)")
    return home


def _resources(cpu=10.0):
    return ResourceSample(disk_percent=20, memory_percent=30, swap_percent=0, cpu_percent=cpu)


def _sample_meminfo(monkeypatch, meminfo):
    monkeypatch.setattr(
        ops_monitor,
        "_bounded_text",
        lambda path, _limit: meminfo if str(path) == "/proc/meminfo" else None,
    )
    return HostResourceSampler()()


def test_missing_mem_available_reports_unknown_memory(monkeypatch):
    sample = _sample_meminfo(monkeypatch, "MemTotal: 1000 kB\nSwapTotal: 0 kB\n")

    assert sample.memory_percent is None


def test_missing_swap_total_reports_healthy_zero_swap(monkeypatch):
    sample = _sample_meminfo(
        monkeypatch,
        "MemTotal: 1000 kB\nMemAvailable: 500 kB\nSwapFree: 250 kB\n",
    )

    assert sample.swap_percent == 0.0


def test_healthy_run_is_silent_and_reads_injected_home(tmp_path, capsys, monkeypatch):
    home = _healthy_home(tmp_path)
    monitor = OperationsMonitor(
        home=home, clock=lambda: NOW, resources=_resources, delegation_reader=lambda: (1, 10),
    )

    assert monitor.sample() == []
    monkeypatch.setattr(ops_monitor, "OperationsMonitor", lambda home: monitor)
    assert ops_monitor.main(["--home", str(home)]) == 0
    assert capsys.readouterr().out == ""


def test_alerts_are_transition_only_sustained_and_hysteretic(tmp_path):
    home = _healthy_home(tmp_path)
    samples = iter([90.0, 90.0, 90.0, 82.0, 70.0])
    monitor = OperationsMonitor(
        home=home,
        clock=lambda: NOW,
        resources=lambda: _resources(next(samples)),
        delegation_reader=lambda: (1, 10),
    )

    assert monitor.sample() == []
    assert monitor.sample() == []
    opened = monitor.sample()
    assert [(item.signal, item.transition, item.state) for item in opened] == [
        ("sustained_cpu", "opened", "warning")
    ]
    assert monitor.sample() == []  # below warning but above the 75% recovery threshold
    recovered = monitor.sample()
    assert [(item.signal, item.transition, item.state) for item in recovered] == [
        ("sustained_cpu", "recovered", "healthy")
    ]


def test_critical_incident_does_not_downgrade_before_recovery(tmp_path):
    home = _healthy_home(tmp_path)
    disk = iter([96.0, 90.0, 79.0])
    monitor = OperationsMonitor(
        home=home,
        clock=lambda: NOW,
        resources=lambda: ResourceSample(next(disk), 30, 0, 10),
        delegation_reader=lambda: (1, 10),
    )

    assert [(item.transition, item.state) for item in monitor.sample()] == [("opened", "critical")]
    assert monitor.sample() == []
    assert [(item.transition, item.state) for item in monitor.sample()] == [("recovered", "healthy")]


def test_malformed_sensitive_inputs_are_bounded_unknown_without_leak(tmp_path):
    home = tmp_path / "secret-home-name"
    (home / "state").mkdir(parents=True)
    (home / "cron").mkdir()
    secret = "sk-super-secret prompt=/private/chat/123 raw-job-id"
    (home / "state" / "gateway.heartbeat").write_text(secret)
    (home / "cron" / "ticker_heartbeat").write_text(secret)
    (home / "cron" / "jobs.json").write_text("{" + secret)
    (home / "state.db").write_text(secret)
    monitor = OperationsMonitor(
        home=home,
        clock=lambda: NOW,
        resources=lambda: (_ for _ in ()).throw(RuntimeError(secret)),
        delegation_reader=lambda: (_ for _ in ()).throw(RuntimeError(secret)),
    )

    signals = monitor.collect()
    assert monitor.sample() == []
    assert all(signal.state == "unknown" for signal in signals)
    assert secret not in repr(signals)
    assert str(home) not in repr(signals)


def test_cron_and_session_signals_expose_counts_not_identifiers(tmp_path):
    home = _healthy_home(tmp_path)
    jobs = [{
        "id": "raw-job-id", "enabled": True, "last_status": "failed",
        "last_error": "Bearer secret", "next_run_at": datetime.fromtimestamp(NOW - 400, timezone.utc).isoformat(),
    }]
    (home / "cron" / "jobs.json").write_text(json.dumps({"jobs": jobs}))
    with sqlite3.connect(home / "state.db") as conn:
        conn.execute("INSERT INTO sessions VALUES (?)", ("chat-id-secret",))
    monitor = OperationsMonitor(
        home=home, clock=lambda: NOW, resources=_resources, delegation_reader=lambda: (9, 10),
    )

    alerts = monitor.sample()
    projected = [item.to_dict() for item in alerts]
    assert {(item.signal, item.state) for item in alerts} == {
        ("cron_failures", "warning"),
        ("cron_lateness", "warning"),
        ("delegation_saturation", "warning"),
    }
    assert "raw-job-id" not in repr(projected)
    assert "chat-id-secret" not in repr(projected)
    assert "Bearer secret" not in repr(projected)
