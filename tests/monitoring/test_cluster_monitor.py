import json
from io import BytesIO

from agent.monitoring import cluster_monitor
from agent.monitoring.cluster_monitor import ClusterMonitor, CommandResult, HttpResult


DF = b"Filesystem 1-blocks Used Available Capacity Mounted on\n/dev/root 1000 250 750 25% /\n"
GPU = b"24576, 1024, 42, 85.5\n"


def _commands(argv, _timeout, limit):
    assert limit == cluster_monitor._MAX_COMMAND_BYTES
    if argv[0] == "ping":
        return CommandResult(0, b"64 bytes: time=1.25 ms\n")
    command = argv[argv.index("--") + 2 :] if argv[0] == "ssh" else argv
    return CommandResult(0, GPU if command[0] == "nvidia-smi" else DF)


def _http(url, _timeout, limit):
    assert limit == cluster_monitor._MAX_HTTP_BYTES
    if url.endswith("/api/version"):
        return HttpResult(200, b'{"version":"1.2.3"}')
    if url.endswith("/api/tags"):
        return HttpResult(200, b'{"models":[{"name":"private-model"}]}')
    return HttpResult(200, b"Ollama is running")


class _Process:
    def __init__(self):
        self.stdout = BytesIO(b"")
        self.returncode = 0

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.returncode = -9


def test_run_command_preserves_only_ssh_authentication_environment(monkeypatch):
    captured = {}
    monkeypatch.setenv("HOME", "/private/auth-home")
    monkeypatch.setenv("SSH_AUTH_SOCK", "/private/agent.sock")
    monkeypatch.setenv("MONITOR_SECRET", "must-not-be-inherited")

    def popen(argv, **kwargs):
        captured.update(kwargs)
        return _Process()

    monkeypatch.setattr(cluster_monitor.subprocess, "Popen", popen)

    assert cluster_monitor.run_command(["ssh", "example"], 1, 100) == CommandResult(0, b"")
    assert captured["env"] == {
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "LANG": "C",
        "LC_ALL": "C",
        "HOME": "/private/auth-home",
        "SSH_AUTH_SOCK": "/private/agent.sock",
    }
    assert "MONITOR_SECRET" not in repr(captured["env"])


def test_run_command_omits_absent_optional_ssh_environment(monkeypatch):
    captured = {}
    monkeypatch.delenv("HOME", raising=False)
    monkeypatch.delenv("SSH_AUTH_SOCK", raising=False)

    def popen(argv, **kwargs):
        captured.update(kwargs)
        return _Process()

    monkeypatch.setattr(cluster_monitor.subprocess, "Popen", popen)

    assert cluster_monitor.run_command(["true"], 1, 100) == CommandResult(0, b"")
    assert captured["env"] == {
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "LANG": "C",
        "LC_ALL": "C",
    }


def test_router_probes_root_and_classifies_fixed_health_literal():
    calls = []

    def runner(url, timeout, limit):
        calls.append((url, timeout, limit))
        return HttpResult(200, b"service says: Ollama is running\n")

    assert cluster_monitor._router(runner) == {"state": "healthy", "http_status": 200}
    assert calls == [("http://127.0.0.1:11437/", 3.0, cluster_monitor._MAX_HTTP_BYTES)]


def test_router_treats_other_success_bodies_as_unknown_and_non_200_as_unhealthy():
    assert cluster_monitor._router(lambda *_args: HttpResult(200, b'{"status":"healthy"}')) == {
        "state": "unknown",
        "http_status": 200,
    }
    assert cluster_monitor._router(lambda *_args: HttpResult(200, b"\xffmalformed")) == {
        "state": "unknown",
        "http_status": 200,
    }
    assert cluster_monitor._router(lambda *_args: HttpResult(503, b"Ollama is running")) == {
        "state": "unhealthy",
        "http_status": 503,
    }


def test_success_covers_fixed_cluster_without_model_names():
    report = ClusterMonitor(
        command_runner=_commands, http_runner=_http, clock=lambda: 123.0
    ).collect()

    assert [(host["name"], host["address"]) for host in report["hosts"]] == [
        ("Z840", "192.168.0.7"),
        ("ZimaOS", "192.168.0.8"),
        ("Geekom", "127.0.0.1"),
    ]
    assert report["capability_router"] == {"state": "healthy", "http_status": 200}
    assert all(
        host["connectivity"] == {"state": "healthy", "latency_ms": 1.25}
        for host in report["hosts"]
    )
    assert all(host["ollama"]["model_count"] == 1 for host in report["hosts"])
    assert all(host["gpu"]["devices"][0]["temperature_c"] == 42 for host in report["hosts"])
    assert all(host["storage"]["available_bytes"] == 750 for host in report["hosts"])
    assert "private-model" not in json.dumps(report)


def test_timeouts_are_unknown_not_healthy():
    def timed_out(_argv, _timeout, _limit):
        return CommandResult(-1, b"ignored", timed_out=True)

    report = ClusterMonitor(
        command_runner=timed_out,
        http_runner=lambda *_args: HttpResult(0, b""),
    ).collect()

    assert report["capability_router"]["state"] == "unknown"
    for host in report["hosts"]:
        assert host["connectivity"]["state"] == "unknown"
        assert host["ollama"]["health"] == "unknown"
        assert host["ollama"]["models_endpoint"] == "unknown"
        assert host["ollama"]["models_endpoint"] == "unknown"
        assert host["gpu"]["state"] == "unknown"
        assert host["storage"]["state"] == "unknown"


def test_malformed_success_responses_are_unknown():
    malformed = b"not valid: 999999999999999999 /private/data"
    report = ClusterMonitor(
        command_runner=lambda *_args: CommandResult(0, malformed),
        http_runner=lambda *_args: HttpResult(200, malformed),
    ).collect()

    assert report["capability_router"]["state"] == "unknown"
    for host in report["hosts"]:
        assert host["connectivity"]["state"] == "unknown"
        assert host["ollama"]["health"] == "unknown"
        assert host["gpu"]["state"] == "unknown"
        assert host["storage"]["state"] == "unknown"


def test_secret_and_path_material_never_reaches_json_report(capsys, monkeypatch):
    sensitive = b"Bearer sk-secret /home/alice/private/model.gguf"
    monitor = ClusterMonitor(
        command_runner=lambda *_args: CommandResult(1, sensitive),
        http_runner=lambda *_args: HttpResult(503, sensitive),
        clock=lambda: 456.0,
    )
    monkeypatch.setattr(cluster_monitor, "ClusterMonitor", lambda: monitor)

    assert cluster_monitor.main(["--json"]) == 0
    output = capsys.readouterr().out
    decoded = json.loads(output)
    assert decoded["schema_version"] == 1
    assert "sk-secret" not in output
    assert "/home/alice" not in output
    assert "model.gguf" not in output
