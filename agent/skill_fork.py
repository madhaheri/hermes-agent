"""Cache-safe dispatch for explicitly invoked ``context: fork`` skills."""

from __future__ import annotations

import contextvars
import base64
import binascii
import json
import logging
import secrets
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Generator

_MARKER_PREFIX = "\x1eHERMES_SKILL_FORK_V1:"
_PENDING_LIMIT = 64
_PENDING_TTL_SECONDS = 600.0
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ForkedSkillRequest:
    """A one-shot skill task that will run in a fresh delegated child."""

    name: str
    invocation: str
    goal: str
    context: str
    failure_reason: str = ""
    raw_body: str | None = None
    skill_dir: str | None = None
    declared_arguments: Any = None
    task_id: str | None = None
    runtime_note: str = ""


@dataclass
class _ActiveFork:
    request: ForkedSkillRequest
    goal_marker: str
    consumed: bool = False


_pending_lock = threading.Lock()
_pending: OrderedDict[str, tuple[float, ForkedSkillRequest]] = OrderedDict()
_active_fork: contextvars.ContextVar[_ActiveFork | None] = contextvars.ContextVar(
    "hermes_skill_fork", default=None,
)


def register_forked_skill(request: ForkedSkillRequest) -> str:
    """Register a request and return a body-free marker redeemable in another process."""
    token = secrets.token_urlsafe(18)
    now = time.monotonic()
    with _pending_lock:
        _prune_pending(now)
        _pending[token] = (now, request)
        while len(_pending) > _PENDING_LIMIT:
            _pending.popitem(last=False)
    payload = base64.urlsafe_b64encode(json.dumps({
        "invocation": request.invocation, "created_at": time.time(),
    }, ensure_ascii=False).encode()).decode().rstrip("=")
    return f"{_MARKER_PREFIX}{token}:{payload}"


def _prune_pending(now: float) -> None:
    """Discard expired markers while the registry lock is held."""
    while _pending:
        _token, (created, _request) = next(iter(_pending.items()))
        if now - created <= _PENDING_TTL_SECONDS:
            break
        _pending.popitem(last=False)


def _marker_token(message: Any) -> str | None:
    """Exact internal marker only; ordinary user text can never prefix-match it."""
    if not isinstance(message, str) or not message.startswith(_MARKER_PREFIX):
        return None
    token = message[len(_MARKER_PREFIX):].partition(":")[0]
    return token if token and not any(char.isspace() for char in token) else None


def _find_fork_marker(message: Any) -> str | None:
    """Find a serialized marker even when a surface prepended turn-local context."""
    if not isinstance(message, str):
        return None
    start = message.find(_MARKER_PREFIX)
    if start < 0:
        return None
    end = start + len(_MARKER_PREFIX)
    while end < len(message) and not message[end].isspace():
        end += 1
    marker = message[start:end]
    return marker if _marker_token(marker) else None


def forked_skill_invocation(message: Any) -> str | None:
    """Decode only the explicit slash command and arguments; the marker never carries a skill body."""
    if not isinstance(message, str) or not message.startswith(_MARKER_PREFIX):
        return None
    _token, separator, encoded = message[len(_MARKER_PREFIX):].partition(":")
    if not separator or not encoded:
        return None
    try:
        decoded = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        payload = json.loads(decoded)
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return None
    created_at = payload.get("created_at") if isinstance(payload, dict) else None
    invocation = payload.get("invocation") if isinstance(payload, dict) else None
    if not isinstance(created_at, (int, float)) or time.time() - created_at > _PENDING_TTL_SECONDS:
        return None
    if not isinstance(invocation, str) or not invocation.startswith("/") or len(invocation) > 65_536:
        return None
    return invocation


def normalize_forked_user_text(value: Any) -> Any:
    """Replace only the internal marker in persisted/display text, never with skill instructions."""
    if isinstance(value, str):
        marker = _find_fork_marker(value)
        if marker is None:
            return value
        request = peek_forked_skill(marker)
        replacement = request.invocation if request else forked_skill_invocation(marker)
        replacement = replacement or "This isolated skill request expired before execution."
        return value.replace(marker, replacement, 1)
    if isinstance(value, list):
        return [normalize_forked_user_text(item) for item in value]
    if isinstance(value, dict):
        return {
            key: normalize_forked_user_text(item) if key in {"content", "text"} else item
            for key, item in value.items()
        }
    return value


def peek_forked_skill(message: Any) -> ForkedSkillRequest | None:
    """Read a pending request without consuming it (used for display projection)."""
    token = _marker_token(message)
    if token is None:
        return None
    with _pending_lock:
        _prune_pending(time.monotonic())
        entry = _pending.get(token)
        return entry[1] if entry else None


def _consume_forked_skill(message: Any) -> ForkedSkillRequest | None:
    token = _marker_token(message)
    if token is None:
        return None
    with _pending_lock:
        _prune_pending(time.monotonic())
        entry = _pending.pop(token, None)
        return entry[1] if entry else None


def prepare_forked_skill_turn(
    user_message: Any,
    persist_user_message: Any,
    title_user_message: str | None,
    prelude: Generator | None,
    task_id: str | None = None,
) -> tuple[Any, Any, str | None, Generator | None]:
    """Replace an opaque marker with a safe user row and a synchronous delegation prelude."""
    marker = _find_fork_marker(user_message)
    if marker is None:
        return user_message, persist_user_message, title_user_message, prelude
    request = _consume_forked_skill(marker)
    if request is None and (invocation_hint := forked_skill_invocation(marker)):
        from agent.skill_commands import rebuild_forked_skill_marker
        regenerated = rebuild_forked_skill_marker(invocation_hint, task_id=task_id)
        request = _consume_forked_skill(regenerated)
    if request is None:
        message = "This isolated skill request expired before execution. Ask the user to invoke the skill again."
        safe_user_message = user_message.replace(marker, message, 1)
        return (
            safe_user_message,
            _replace_fork_marker(persist_user_message, marker, message),
            "Expired isolated skill request" if title_user_message is None or marker in title_user_message else title_user_message,
            prelude,
        )
    request = _materialize_forked_skill(request)
    invocation = request.invocation
    safe_user_message = user_message.replace(marker, invocation, 1)
    stored_message = _replace_fork_marker(persist_user_message, marker, invocation)
    title = invocation if title_user_message is None else title_user_message.replace(marker, invocation, 1)
    if request.failure_reason:
        return (
            f"{safe_user_message}\n\n[Hermes did not run this skill: {request.failure_reason}. "
            "Explain the limitation and do not perform the task inline.]",
            stored_message,
            title,
            prelude,
        )
    fork_prelude = _fork_prelude(request)
    return safe_user_message, stored_message, title, _chain_preludes(prelude, fork_prelude)


def _replace_fork_marker(value: Any, marker: str, replacement: str) -> Any:
    if value is None:
        return replacement
    return value.replace(marker, replacement, 1) if isinstance(value, str) else value


def _materialize_forked_skill(request: ForkedSkillRequest) -> ForkedSkillRequest:
    """Preprocess and interpolate only after the turn reaches its executing process."""
    if request.failure_reason or request.raw_body is None:
        return request
    try:
        from agent.skill_preprocessing import (
            load_skills_config, preprocess_skill_content, substitute_skill_arguments,
        )

        body = preprocess_skill_content(
            request.raw_body, Path(request.skill_dir) if request.skill_dir else None,
            request.task_id, skills_cfg=load_skills_config(),
        ).strip()
        body, _boundary, _consumed = substitute_skill_arguments(
            body, request.context, request.declared_arguments,
        )
        if request.runtime_note and body:
            body = f"{body}\n\n[Runtime note: {request.runtime_note}]"
        if not body:
            return replace(request, failure_reason="the skill has no executable instructions")
        try:
            from tools.skill_usage import bump_use
            bump_use(request.name, task_id=request.task_id)
        except Exception:
            logger.warning("Could not record usage for forked skill %s", request.name, exc_info=True)
        return replace(request, goal=body, raw_body=None)
    except Exception as exc:
        logger.warning("Could not prepare forked skill %s", request.name, exc_info=True)
        return replace(request, failure_reason=f"could not prepare instructions ({type(exc).__name__})")


def _chain_preludes(existing: Generator | None, forked: Generator) -> Generator:
    if existing is not None:
        yield from existing
    yield from forked


def _fork_prelude(request: ForkedSkillRequest) -> Generator:
    """Ask the normal tool executor to dispatch one sanitized delegate_task call."""
    marker = f"Run the explicitly invoked skill /{request.name} in an isolated child."
    active = _ActiveFork(request=request, goal_marker=marker)
    token = _active_fork.set(active)
    try:
        yield (
            "Run the explicitly invoked skill in an isolated delegated context; return its result.",
            "delegate_task",
            {"goal": marker},
        )
    finally:
        _active_fork.reset(token)


def claim_forked_delegate(goal: Any) -> ForkedSkillRequest | None:
    """Claim the one scripted delegate call; subsequent model calls are ordinary delegation."""
    active = _active_fork.get()
    if active is None or active.consumed or goal != active.goal_marker:
        return None
    active.consumed = True
    return active.request


def dispatch_forked_delegate(parent_agent: Any, function_args: dict[str, Any]) -> str | None:
    """Run the claimed skill task synchronously through Hermes's existing delegate runtime."""
    request = claim_forked_delegate(function_args.get("goal"))
    if request is None:
        return None
    from tools.delegate_tool import delegate_task

    return delegate_task(
        goal=request.goal, context=request.context or None,
        role="leaf", background=False, parent_agent=parent_agent,
    )
