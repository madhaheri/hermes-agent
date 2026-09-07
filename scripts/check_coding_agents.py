#!/usr/bin/env python3
"""Fail-closed preflight for the authenticated coding-agent CLIs.

This check is intentionally read-only by default. Use --smoke to make one
bounded Claude request and verify the complete non-interactive path.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from typing import Any


def run(command: list[str], timeout: int = 20) -> dict[str, Any]:
    try:
        p = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "error": type(exc).__name__, "stdout": "", "stderr": str(exc)}
    return {"ok": p.returncode == 0, "returncode": p.returncode, "stdout": p.stdout.strip(), "stderr": p.stderr.strip()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--smoke", action="store_true", help="make one bounded Claude API smoke request")
    args = parser.parse_args()

    result: dict[str, Any] = {"schemaVersion": 1, "agents": {}, "ok": True}
    for name, commands in {
        "claude": [["claude", "--version"], ["claude", "auth", "status"]],
        "codex": [["codex", "--version"], ["codex", "login", "status"]],
    }.items():
        path = shutil.which(name)
        agent: dict[str, Any] = {"executable": path, "ok": bool(path), "checks": []}
        if path:
            for command in commands:
                check = run(command)
                # Auth output is retained only as a boolean; never emit account data.
                if command[-1] == "status":
                    if name == "claude":
                        try:
                            auth = json.loads(check["stdout"])
                            check["authenticated"] = auth.get("loggedIn") is True
                        except (json.JSONDecodeError, KeyError):
                            check["authenticated"] = False
                    else:
                        check["authenticated"] = check["ok"]
                    check.pop("stdout", None)
                    check.pop("stderr", None)
                agent["checks"].append({"command": command, **check})
                agent["ok"] = agent["ok"] and check.get("ok", False) and check.get("authenticated", True)
        result["agents"][name] = agent
        result["ok"] = result["ok"] and agent["ok"]

    if args.smoke and result["agents"].get("claude", {}).get("ok"):
        smoke = run(
            [
                "claude", "-p", "Reply with exactly CLAUDE_CODE_SMOKE_OK and nothing else.",
                "--no-session-persistence", "--max-turns", "1", "--max-budget-usd", "0.10",
                "--output-format", "json",
            ],
            timeout=180,
        )
        if smoke.get("ok"):
            try:
                payload = json.loads(smoke["stdout"])
                smoke["result"] = payload.get("result")
                smoke["is_error"] = payload.get("is_error")
                smoke["ok"] = payload.get("result") == "CLAUDE_CODE_SMOKE_OK" and payload.get("is_error") is False
            except json.JSONDecodeError:
                smoke["ok"] = False
        smoke.pop("stdout", None)
        smoke.pop("stderr", None)
        result["agents"]["claude"]["smoke"] = smoke
        result["ok"] = result["ok"] and smoke["ok"]
    elif args.smoke:
        result["agents"]["claude"]["smoke"] = {"ok": False, "skipped": True, "reason": "preflight failed"}
        result["ok"] = False

    print(json.dumps(result, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
