"""Bounded, read-only health checks for the local inference cluster.

The monitor executes only fixed diagnostic commands and HTTP GET requests.  Reports use a
closed vocabulary and numeric values; command output, response bodies, exceptions, model names,
credentials, and filesystem paths are never returned.  A failed or ambiguous probe is
``unknown`` rather than optimistically healthy.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

_COMMAND_TIMEOUT_S = 3.0
_HTTP_TIMEOUT_S = 3.0
_MAX_COMMAND_BYTES = 32_768
_MAX_HTTP_BYTES = 256 * 1024
_MAX_GPUS = 32
_MAX_MODELS = 10_000
_PING_MS_RE = re.compile(rb"(?:time[=<])([0-9]+(?:\.[0-9]+)?)\s*ms", re.ASCII)


@dataclass(frozen=True, slots=True)
class Host:
    name: str
    address: str
    local: bool = False


HOSTS = (
    Host("Z840", "192.168.0.7"),
    Host("ZimaOS", "192.168.0.8"),
    Host("Geekom", "127.0.0.1", local=True),
)


@dataclass(frozen=True, slots=True)
class CommandResult:
    returncode: int
    stdout: bytes
    timed_out: bool = False


@dataclass(frozen=True, slots=True)
class HttpResult:
    status: int
    body: bytes


CommandRunner = Callable[[Sequence[str], float, int], CommandResult]
HttpRunner = Callable[[str, float, int], HttpResult]


def run_command(argv: Sequence[str], timeout_s: float, output_limit: int) -> CommandResult:
    """Run one fixed diagnostic command with timeout and a hard output limit."""
    env = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "LANG": "C", "LC_ALL": "C"}
    for name in ("HOME", "SSH_AUTH_SOCK"):
        if name in os.environ:
            env[name] = os.environ[name]
    try:
        process = subprocess.Popen(
            list(argv),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=env,
        )
    except OSError:
        return CommandResult(-1, b"", timed_out=True)
    chunks = bytearray()
    exceeded = threading.Event()

    def read_bounded() -> None:
        assert process.stdout is not None
        while True:
            chunk = process.stdout.read(min(4096, output_limit + 1 - len(chunks)))
            if not chunk:
                return
            chunks.extend(chunk)
            if len(chunks) > output_limit:
                exceeded.set()
                process.kill()
                return

    reader = threading.Thread(target=read_bounded, name="cluster-monitor-output", daemon=True)
    reader.start()
    try:
        process.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        reader.join(timeout=1)
        return CommandResult(-1, b"", timed_out=True)
    reader.join(timeout=1)
    if reader.is_alive() or exceeded.is_set():
        process.kill()
        return CommandResult(process.returncode, b"")
    return CommandResult(process.returncode, bytes(chunks))


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def run_http(url: str, timeout_s: float, output_limit: int) -> HttpResult:
    """GET an allowlisted probe URL without authentication, redirects, or unbounded reads."""
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "hermes-cluster-monitor/1"})
    try:
        opener = build_opener(ProxyHandler({}), _NoRedirect())
        with opener.open(request, timeout=timeout_s) as response:  # nosec B310 - URLs are fixed below
            body = response.read(output_limit + 1)
            if len(body) > output_limit:
                return HttpResult(0, b"")
            return HttpResult(int(response.status), body)
    except HTTPError as exc:
        return HttpResult(int(exc.code), b"")
    except (OSError, URLError, TimeoutError, ValueError):
        return HttpResult(0, b"")


def _command(host: Host, argv: Sequence[str]) -> list[str]:
    if host.local:
        return list(argv)
    return [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=3",
        "-o",
        "ConnectionAttempts=1",
        "--",
        host.address,
        *argv,
    ]


def _json_object(result: HttpResult) -> Optional[dict[str, object]]:
    if result.status != 200 or not result.body:
        return None
    try:
        value = json.loads(result.body)
    except (UnicodeError, ValueError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _connectivity(host: Host, runner: CommandRunner) -> dict[str, object]:
    result = runner(
        ["ping", "-n", "-c", "1", "-W", "2", "--", host.address],
        _COMMAND_TIMEOUT_S,
        _MAX_COMMAND_BYTES,
    )
    if result.timed_out:
        return {"state": "unknown", "latency_ms": None}
    if result.returncode != 0:
        return {"state": "unhealthy", "latency_ms": None}
    match = _PING_MS_RE.search(result.stdout)
    if match is None:
        return {"state": "unknown", "latency_ms": None}
    try:
        latency = float(match.group(1))
    except (ValueError, OverflowError):
        return {"state": "unknown", "latency_ms": None}
    if not math.isfinite(latency) or latency < 0 or latency > _COMMAND_TIMEOUT_S * 1000:
        return {"state": "unknown", "latency_ms": None}
    return {"state": "healthy", "latency_ms": round(latency, 2)}


def _ollama(host: Host, runner: HttpRunner) -> dict[str, object]:
    base = f"http://{host.address}:11434"
    health = runner(f"{base}/api/version", _HTTP_TIMEOUT_S, _MAX_HTTP_BYTES)
    health_payload = _json_object(health)
    if health.status == 0:
        health_state = "unknown"
    elif health.status != 200:
        health_state = "unhealthy"
    elif health_payload is None or not isinstance(health_payload.get("version"), str):
        health_state = "unknown"
    else:
        health_state = "healthy"

    models = runner(f"{base}/api/tags", _HTTP_TIMEOUT_S, _MAX_HTTP_BYTES)
    models_payload = _json_object(models)
    entries = models_payload.get("models") if models_payload is not None else None
    model_count = len(entries) if isinstance(entries, list) and len(entries) <= _MAX_MODELS else None
    if models.status == 0:
        models_state = "unknown"
    elif models.status != 200:
        models_state = "unhealthy"
    elif model_count is None:
        models_state = "unknown"
    else:
        models_state = "healthy"
    return {
        "health": health_state,
        "health_http_status": health.status or None,
        "models_endpoint": models_state,
        "models_http_status": models.status or None,
        "model_count": model_count,
    }


def _storage(host: Host, runner: CommandRunner) -> dict[str, object]:
    result = runner(_command(host, ["df", "-P", "-B1", "/"]), _COMMAND_TIMEOUT_S, _MAX_COMMAND_BYTES)
    if result.timed_out or result.returncode != 0:
        return {
            "state": "unknown",
            "total_bytes": None,
            "available_bytes": None,
            "used_percent": None,
        }
    lines = result.stdout.splitlines()
    fields = lines[-1].split() if len(lines) >= 2 else []
    try:
        total, used, available = (int(fields[index]) for index in (1, 2, 3))
        used_percent = float(fields[4].rstrip(b"%"))
    except (IndexError, ValueError, OverflowError):
        return {
            "state": "unknown",
            "total_bytes": None,
            "available_bytes": None,
            "used_percent": None,
        }
    valid = total > 0 and 0 <= used <= total and 0 <= available <= total and 0 <= used_percent <= 100
    if not valid:
        return {
            "state": "unknown",
            "total_bytes": None,
            "available_bytes": None,
            "used_percent": None,
        }
    return {
        "state": "healthy",
        "total_bytes": total,
        "available_bytes": available,
        "used_percent": used_percent,
    }


def _gpus(host: Host, runner: CommandRunner) -> dict[str, object]:
    argv = [
        "nvidia-smi",
        "--query-gpu=memory.total,memory.used,temperature.gpu,power.draw",
        "--format=csv,noheader,nounits",
    ]
    result = runner(_command(host, argv), _COMMAND_TIMEOUT_S, _MAX_COMMAND_BYTES)
    if result.timed_out:
        return {"state": "unknown", "count": None, "devices": []}
    if result.returncode != 0:
        # NVIDIA tooling is optional; absence cannot establish either health or failure.
        return {"state": "unknown", "count": None, "devices": []}
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if not lines or len(lines) > _MAX_GPUS:
        return {"state": "unknown", "count": None, "devices": []}
    devices: list[dict[str, float | int | None]] = []
    try:
        for line in lines:
            values = [item.strip() for item in line.split(b",")]
            if len(values) != 4:
                raise ValueError
            total, used, temperature = (int(values[index]) for index in range(3))
            power = None if values[3] in {b"N/A", b"[Not Supported]"} else float(values[3])
            if total <= 0 or used < 0 or used > total or temperature < 0 or temperature > 150:
                raise ValueError
            if power is not None and (not math.isfinite(power) or power < 0 or power > 10_000):
                raise ValueError
            devices.append(
                {
                    "memory_total_mib": total,
                    "memory_used_mib": used,
                    "temperature_c": temperature,
                    "power_w": power,
                }
            )
    except (ValueError, OverflowError):
        return {"state": "unknown", "count": None, "devices": []}
    return {"state": "healthy", "count": len(devices), "devices": devices}


def _router(runner: HttpRunner) -> dict[str, object]:
    result = runner("http://127.0.0.1:11437/", _HTTP_TIMEOUT_S, _MAX_HTTP_BYTES)
    if result.status == 0:
        state = "unknown"
    elif result.status != 200:
        state = "unhealthy"
    else:
        state = "healthy" if b"Ollama is running" in result.body else "unknown"
    return {"state": state, "http_status": result.status or None}


@dataclass(slots=True)
class ClusterMonitor:
    command_runner: CommandRunner = run_command
    http_runner: HttpRunner = run_http
    clock: Callable[[], float] = time.time

    def collect(self) -> dict[str, object]:
        hosts = []
        for host in HOSTS:
            hosts.append(
                {
                    "name": host.name,
                    "address": host.address,
                    "connectivity": _connectivity(host, self.command_runner),
                    "ollama": _ollama(host, self.http_runner),
                    "gpu": _gpus(host, self.command_runner),
                    "storage": _storage(host, self.command_runner),
                }
            )
        return {
            "schema_version": 1,
            "observed_at": self.clock(),
            "capability_router": _router(self.http_runner),
            "hosts": hosts,
        }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only inference-cluster health monitor")
    parser.add_argument("--json", action="store_true", help="emit one bounded JSON document")
    args = parser.parse_args(argv)
    report = ClusterMonitor().collect()
    if args.json:
        print(json.dumps(report, separators=(",", ":"), sort_keys=True))
    else:
        router = report["capability_router"]
        print(f"capability-router: {router['state']}")
        for host in report["hosts"]:
            print(
                f"{host['name']} ({host['address']}): connectivity={host['connectivity']['state']} "
                f"ollama={host['ollama']['health']} models={host['ollama']['models_endpoint']} "
                f"gpu={host['gpu']['state']} storage={host['storage']['state']}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
