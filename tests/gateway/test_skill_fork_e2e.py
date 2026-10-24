"""Real gateway slash-to-child regression: fork body stays out of parent history."""

import asyncio
import json
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast

import pytest

from tests.gateway.fixtures.local_recovery_probe import child_env, daemon, rpc, websocket
from tests.waiting import async_wait_until


class ForkModel(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if body.get("messages"):
            cast(Any, self.server).requests.append(body)
        child_request = "FORK_INSTRUCTION_SECRET_9A1" in json.dumps(body.get("messages", []))
        message = {"role": "assistant", "content": "CHILD_DONE" if child_request else "PARENT_DONE"}
        payload = json.dumps({
            "id": "fork-e2e",
            "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }).encode()
        kind = "application/json"
        if body.get("stream"):
            payload = (
                "data: " + json.dumps({"id": "fork-e2e", "choices": [{
                    "index": 0, "delta": message, "finish_reason": "stop",
                }]}) + "\n\ndata: [DONE]\n\n"
            ).encode()
            kind = "text/event-stream"
        try:
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError):
            pass


@pytest.mark.platforms("linux")
def test_canonical_gateway_redeems_fork_marker_in_the_turn_process(tmp_path):
    root = Path(__file__).resolve().parents[2]
    home, user = tmp_path / "state", tmp_path / "user"
    home.mkdir(mode=0o700)
    user.mkdir()
    peer = ThreadingHTTPServer(("127.0.0.1", 0), ForkModel)
    cast(Any, peer).requests = []
    peer_requests = cast(Any, peer).requests
    thread = threading.Thread(target=peer.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{peer.server_port}/v1"
    (home / "config.yaml").write_text(json.dumps({
        "gateway": {"multiplex_profiles": False},
        "model": {"provider": "custom", "default": "fork-fixture", "base_url": url},
        "auxiliary": {"title_generation": {"enabled": False}},
    }))
    skill = home / "skills" / "fork-probe"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: fork-probe\ndescription: Gateway fork fixture\ncontext: fork\n"
        "arguments: [topic]\n---\nFORK_INSTRUCTION_SECRET_9A1: $ARGUMENTS\n",
        encoding="utf-8",
    )
    env = child_env()
    env.update(
        HOME=str(user), USERPROFILE=str(user), HERMES_HOME=str(home),
        PYTHONPATH=str(root), OPENAI_API_KEY="loopback-only", OPENAI_BASE_URL=url,
        PYTHONUNBUFFERED="1",
    )

    def admission_status(request_id):
        with sqlite3.connect(f"file:{home / 'state.db'}?mode=ro", uri=True) as db:
            row = db.execute(
                "SELECT status FROM session_admissions WHERE request_id=?", (request_id,),
            ).fetchone()
        return row[0] if row else None

    async def exercise(desc):
        async with websocket(home, desc) as ws:
            created = await rpc(
                ws, "session.create", request_id="fork-e2e", source="gui", cwd=str(home),
                toolsets=["delegation"],
            )
            sid = created["result"]["session_id"]
            directive = await rpc(
                ws, "command.dispatch", session_id=sid,
                name="fork-probe", arg="verify process handoff",
            )
            assert directive["result"]["type"] == "skill", directive
            marker = directive["result"]["message"]
            assert marker.startswith("\x1eHERMES_SKILL_FORK_V1:")
            assert "FORK_INSTRUCTION_SECRET_9A1" not in marker
            assert directive["result"]["display"] == "/fork-probe verify process handoff"

            submitted = await rpc(
                ws, "prompt.submit", session_id=sid, input_id="fork-e2e",
                text=marker,
            )
            assert submitted.get("result", {}).get("status") in {"queued", "started", "streaming"}, submitted
            await async_wait_until(
                lambda: admission_status("fork-e2e") == "terminal",
                timeout=60, interval=0.05, what="forked gateway turn to finish",
            )
            resumed = await rpc(ws, "session.resume", session_id=sid)
            history = json.dumps(resumed["result"]["messages"])
            assert "/fork-probe verify process handoff" in history
            assert "CHILD_DONE" in history
            assert "PARENT_DONE" in history
            assert "HERMES_SKILL_FORK_V1" not in history
            parent_user_rows = [
                message for message in resumed["result"]["messages"]
                if message.get("role") == "user"
            ]
            assert all(
                "FORK_INSTRUCTION_SECRET_9A1" not in json.dumps(message)
                for message in parent_user_rows
            )
            assert any(
                "FORK_INSTRUCTION_SECRET_9A1: verify process handoff" in json.dumps(request.get("messages", []))
                for request in peer_requests
            ), peer_requests
            assert all(
                "FORK_INSTRUCTION_SECRET_9A1" not in json.dumps(message)
                for request in peer_requests
                for message in request.get("messages", [])
                if message.get("role") == "system"
            )

    try:
        with daemon(root, home, env, barrier=False) as (_proc, desc):
            asyncio.run(exercise(desc))
    finally:
        peer.shutdown()
        peer.server_close()
        thread.join(timeout=5)
