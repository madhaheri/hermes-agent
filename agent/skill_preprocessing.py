"""Shared SKILL.md preprocessing helpers: ``${HERMES_*}`` template tokens and
inline ``!`cmd``` shell expansion."""

import json
import logging
import re
import subprocess
from pathlib import Path
from typing import Any

from agent.compression_marker import elide
from hermes_cli._subprocess_compat import IS_WINDOWS, windows_hide_flags

logger = logging.getLogger(__name__)

# ${HERMES_SKILL_DIR} / ${HERMES_SESSION_ID} tokens. Unresolvable ones (e.g. no
# session) are left as-is so the author can spot them.
_SKILL_TEMPLATE_RE = re.compile(r"\$\{(HERMES_SKILL_DIR|HERMES_SESSION_ID)\}")
# Inline shell snippets like !`date +%Y-%m-%d` — single-line only.
_INLINE_SHELL_RE = re.compile(r"!`([^`\n]+)`")
_SKILL_ARGUMENT_RE = re.compile(
    r"(?<!\\)((?:\\\\)*)\$(ARGUMENTS(?:\[(\d+)\])?|\d+|[A-Za-z_][A-Za-z0-9_]*)"
)
_ESCAPED_SKILL_ARGUMENT_RE = re.compile(
    r"(?<!\\)\\(\$(?:ARGUMENTS(?:\[\d+\])?|\d+|[A-Za-z_][A-Za-z0-9_]*))"
)
# Cap inline-shell output so a runaway command can't blow out the context.
_INLINE_SHELL_MAX_OUTPUT = 4000


def load_skills_config() -> dict:
    """Load the ``skills`` section of config.yaml (best-effort)."""
    try:
        from hermes_cli.config import load_config_readonly
        skills_cfg = (load_config_readonly() or {}).get("skills")
        if isinstance(skills_cfg, dict):
            return skills_cfg
    except Exception:
        logger.debug("Could not read skills config", exc_info=True)
    return {}


def substitute_template_vars(content: str, skill_dir: Path | None, session_id: str | None) -> str:
    """Replace ${HERMES_SKILL_DIR} / ${HERMES_SESSION_ID}; tokens without a value stay in place."""
    if not content:
        return content
    values = {
        "HERMES_SKILL_DIR": str(skill_dir) if skill_dir else None,
        "HERMES_SESSION_ID": str(session_id) if session_id else None,
    }
    return _SKILL_TEMPLATE_RE.sub(lambda m: values[m.group(1)] or m.group(0), content)


def _split_skill_arguments(raw_arguments: str) -> list[str]:
    """Split slash-command arguments on whitespace while preserving quoted groups and Windows paths."""
    arguments: list[str] = []
    current: list[str] = []
    quote: str | None = None
    started = False
    index = 0
    while index < len(raw_arguments):
        char = raw_arguments[index]
        if quote:
            if char == quote:
                quote = None
            elif char == "\\" and index + 1 < len(raw_arguments) and raw_arguments[index + 1] in {quote, "\\"}:
                current.append(raw_arguments[index + 1])
                index += 1
            else:
                current.append(char)
        elif char in {"'", '"'}:
            quote = char
            started = True
        elif char.isspace():
            if started:
                arguments.append("".join(current))
                current = []
                started = False
        elif char == "\\" and index + 1 < len(raw_arguments) and (
            raw_arguments[index + 1].isspace() or raw_arguments[index + 1] in {"'", '"', "\\"}
        ):
            current.append(raw_arguments[index + 1])
            index += 1
            started = True
        else:
            current.append(char)
            started = True
        index += 1
    if started:
        arguments.append("".join(current))
    return arguments


def substitute_skill_arguments(
    content: str, raw_arguments: str, declared_arguments: Any = None,
) -> tuple[str, int | None, bool]:
    """Substitute Claude-compatible slash arguments after preprocessing, so argument text cannot execute shell snippets."""
    tokens = _split_skill_arguments(raw_arguments or "")
    names = (
        declared_arguments if isinstance(declared_arguments, list)
        else str(declared_arguments or "").split()
    )
    name_to_index = {
        str(name).strip(): index for index, name in enumerate(names) if str(name).strip()
    }
    first_dynamic_index: int | None = None
    consumed_argument = False

    def replace(match: re.Match) -> str:
        nonlocal first_dynamic_index, consumed_argument
        prefix = match.group(1)
        token = match.group(2)
        if token == "ARGUMENTS":
            value = raw_arguments or ""
            received = bool(value)
        elif token.startswith("ARGUMENTS["):
            index = int(match.group(3))
            received = index < len(tokens)
            value = tokens[index] if received else match.group(0)[len(prefix):]
        elif token.isdigit():
            index = int(token)
            received = index < len(tokens)
            value = tokens[index] if received else match.group(0)[len(prefix):]
        elif token in name_to_index:
            index = name_to_index[token]
            received = True
            value = tokens[index] if index < len(tokens) else ""
        else:
            return match.group(0)
        if received:
            consumed_argument = True
            if first_dynamic_index is None:
                first_dynamic_index = match.start()
        return prefix + value

    def unescape(match: re.Match) -> str:
        token = match.group(1)[1:]
        if token == "ARGUMENTS" or token.isdigit() or token in name_to_index:
            return match.group(1)
        return match.group(0)

    expanded = _SKILL_ARGUMENT_RE.sub(replace, content)
    expanded = _ESCAPED_SKILL_ARGUMENT_RE.sub(unescape, expanded)
    if first_dynamic_index is None:
        return expanded, None, consumed_argument
    prefix = _SKILL_ARGUMENT_RE.sub(replace, content[:first_dynamic_index])
    prefix = _ESCAPED_SKILL_ARGUMENT_RE.sub(unescape, prefix)
    return expanded, len(prefix), consumed_argument


def run_inline_shell(command: str, cwd: Path | None, timeout: int) -> str:
    """Run one inline-shell snippet and return its stdout (trimmed; stderr when
    stdout is empty). Failures return an ``[inline-shell ...]`` marker instead
    of raising, so one bad snippet can't wreck the whole skill message."""
    _popen_kwargs = {"creationflags": windows_hide_flags()} if IS_WINDOWS else {}
    from agent.delegation_context import delegated_child_subprocess_env
    try:
        bash = "bash"
        if IS_WINDOWS:
            # CreateProcess searches System32 before PATH and may pick WSL's
            # launcher. Reuse the terminal's native Git Bash resolution.
            from tools.environments.local import _find_bash
            bash = _find_bash()
        completed = subprocess.run(
            [bash, "-c", command],
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True, encoding='utf-8', errors='replace',
            timeout=max(1, int(timeout)),
            check=False,
            stdin=subprocess.DEVNULL,
            env=delegated_child_subprocess_env(),
            **_popen_kwargs,
        )
    except subprocess.TimeoutExpired:
        return f"[inline-shell timeout after {timeout}s: {command}]"
    except FileNotFoundError:
        return "[inline-shell error: bash not found]"
    except Exception as exc:
        # tests/conftest.py's live-system guard may block the os.kill that
        # subprocess.run uses to clean up a timed-out shell; report the timeout.
        if isinstance(exc, RuntimeError) and "live-system guard: blocked os.kill" in str(exc):
            return f"[inline-shell timeout after {timeout}s: {command}]"
        return f"[inline-shell error: {exc}]"
    output = (completed.stdout or "").rstrip("\n") or (completed.stderr or "").rstrip("\n")
    if completed.returncode != 0 and not output:
        # rc!=0 with no output at all is indistinguishable from a legit empty result; it is the
        # "interpreter never ran the command" signature (WSL stub without a distro) — say so.
        return f"[inline-shell exit {completed.returncode} with no output: {command}]"
    return elide(output, _INLINE_SHELL_MAX_OUTPUT)


def expand_inline_shell(content: str, skill_dir: Path | None, timeout: int) -> str:
    """Replace every !`cmd` snippet with its stdout, run with the skill dir as CWD."""
    if "!`" not in content:
        return content
    def _replace(match: re.Match) -> str:
        cmd = match.group(1).strip()
        return run_inline_shell(cmd, skill_dir, timeout) if cmd else ""
    return _INLINE_SHELL_RE.sub(_replace, content)


def _is_community_hub_skill(skill_dir: Path | None) -> bool:
    """Whether *skill_dir* is a hub-installed skill the scan gate classifies as community trust.

    The hub's INSTALL_POLICY blocks a community install on a caution/dangerous verdict — but
    ``--force`` (or a pre-scanner install) puts that skill on disk anyway, and the inline-shell
    DSL scans as high severity, so auto-executing it on view would re-arm exactly what the
    gate refused (#63307). Provenance is the hub lock entry (trusted/builtin entries expand;
    anything without one — bundled-synced, user-created, project/external — keeps the flag's
    contract). It covers every SKILL.md nested inside a community install and is read from
    the lock that owns the skills tree the dir lives in, not only the active profile's
    (``external_dirs`` can expose another profile's installs). A lock that exists but cannot
    be read fails closed for skills in its tree; no lock means no hub installs.
    """
    if skill_dir is None:
        return False
    try:
        from tools.skills_hub import HubLockFile
        from tools.skills_tool import _skills_dir

        target = skill_dir.resolve()
        locks = {HubLockFile().path.resolve(): _skills_dir().resolve()}
        for root in target.parents:
            if root.name == "skills" and (root / ".hub" / "lock.json").is_file():
                locks.setdefault((root / ".hub" / "lock.json").resolve(), root)
        for lock_path, root in locks.items():
            if not (target.is_relative_to(root) and lock_path.is_file()):
                continue
            try:
                installed = json.loads(lock_path.read_text(encoding="utf-8-sig"))["installed"]
                entries = [e for e in installed.values() if isinstance(e, dict)]
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                logger.debug("Unreadable hub lock %s; inline shell stays off for its tree", lock_path, exc_info=True)
                return True
            for entry in entries:
                rel = str(entry.get("install_path") or "")
                if entry.get("trust_level") == "community" and rel and target.is_relative_to((root / rel).resolve()):
                    return True
        return False
    except Exception:
        logger.debug("Could not resolve hub provenance for inline-shell trust scoping", exc_info=True)
        return True


def preprocess_skill_content(
    content: str,
    skill_dir: Path | None,
    session_id: str | None = None,
    skills_cfg: dict | None = None,
) -> str:
    """Apply configured SKILL.md template and inline-shell preprocessing."""
    if not content:
        return content
    cfg = skills_cfg if isinstance(skills_cfg, dict) else load_skills_config()
    if cfg.get("template_vars", True):
        content = substitute_template_vars(content, skill_dir, session_id)
    if cfg.get("inline_shell", False) and not _is_community_hub_skill(skill_dir):
        content = expand_inline_shell(content, skill_dir, int(cfg.get("inline_shell_timeout", 10) or 10))
    return content
