"""Internet-reach capability registry and doctor probe system.

This module implements the "capability layer" pattern inspired by Agent-Reach
(Panniantong/Agent-Reach): instead of the agent knowing which specific tool or
CLI to call for each platform, it asks for a *capability* (e.g. ``twitter.search``,
``youtube.transcript``, ``reddit.read``) and this registry resolves it to the
best available *backend* — with ordered fallbacks, real probes, and repair
prescriptions.

Design
------
* ``Capability`` — a user intent (e.g. "read a tweet", "search Reddit").
* ``Backend`` — one concrete way to satisfy that capability (e.g. x_search tool,
  xurl CLI, twitter-cli).  Each backend has a ``probe()`` that returns a
  ``ProbeResult``.
* ``ReachRegistry`` — holds all capabilities, resolves the best backend, and
  runs the doctor sweep.

Probes are cheap (check env vars, CLI availability, tool registration) and never
make network calls.  This keeps ``hermes reach doctor`` fast and side-effect-free.

The registry is pure-Python with no heavy imports so it can be loaded by the CLI
subcommand and the in-session tool without impacting prompt caching.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field, asdict
from typing import Callable, Dict, List, Optional

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class ProbeResult:
    """Outcome of probing a single backend.

    Attributes:
        available: whether the backend is ready to use right now.
        status: one of ``"ok"``, ``"missing"``, ``"misconfigured"``, ``"unknown"``.
        detail: human-readable explanation.
        fix: optional list of commands or steps to repair.
        needs_user: True when the fix requires user interaction (credentials, login).
        backend_name: name of the backend this result is for.
    """

    available: bool
    status: str = "unknown"
    detail: str = ""
    fix: List[str] = field(default_factory=list)
    needs_user: bool = False
    backend_name: str = ""

    def icon(self) -> str:
        if self.available:
            return "✅"
        if self.status == "missing":
            return "❌"
        return "⚠️"


@dataclass
class Backend:
    """One concrete way to satisfy a capability.

    Attributes:
        name: short identifier (e.g. ``"x_search_tool"``, ``"xurl_cli"``,
            ``"twitter_cli"``).
        description: what this backend does.
        probe: callable returning a ``ProbeResult`` (fast, no network).
        functional_probe: optional callable that actually tests the backend
            with a real query (slower, makes network calls). Used by
            ``hermes reach doctor --functional``.
        tool_name: optional Hermes tool name if this backend is a built-in tool.
        cli_command: optional CLI command name if this backend is a shell tool.
        priority: lower = tried first (default 10, 20, 30…).
    """

    name: str
    description: str
    probe: Callable[[], ProbeResult]
    functional_probe: Optional[Callable[[], ProbeResult]] = None
    tool_name: Optional[str] = None
    cli_command: Optional[str] = None
    priority: int = 10

    def run_probe(self) -> ProbeResult:
        try:
            result = self.probe()
        except Exception as exc:
            result = ProbeResult(
                available=False,
                status="unknown",
                detail=f"probe error: {exc}",
                backend_name=self.name,
            )
        result.backend_name = self.name
        return result

    def run_functional_probe(self) -> ProbeResult:
        """Run the functional probe if available, else fall back to static probe."""
        if self.functional_probe is None:
            return self.run_probe()
        try:
            result = self.functional_probe()
        except Exception as exc:
            result = ProbeResult(
                available=False,
                status="unknown",
                detail=f"functional probe error: {exc}",
                backend_name=self.name,
            )
        result.backend_name = self.name
        return result


@dataclass
class Capability:
    """A user intent that maps to one or more backends.

    Attributes:
        name: dotted capability name (e.g. ``"twitter.search"``).
        description: human-readable description.
        backends: ordered list of backends (lowest priority first).
    """

    name: str
    description: str
    backends: List[Backend] = field(default_factory=list)

    def best_backend(self) -> Optional[Backend]:
        """Return the highest-priority available backend, or None."""
        for backend in sorted(self.backends, key=lambda b: b.priority):
            if backend.run_probe().available:
                return backend
        return None

    def probe_all(self) -> List[ProbeResult]:
        """Probe every backend in priority order."""
        return [
            b.run_probe() for b in sorted(self.backends, key=lambda b: b.priority)
        ]

    def probe_all_functional(self) -> List[ProbeResult]:
        """Functionally probe every backend (makes real network calls)."""
        return [
            b.run_functional_probe()
            for b in sorted(self.backends, key=lambda b: b.priority)
        ]


# ---------------------------------------------------------------------------
# Probe helpers
# ---------------------------------------------------------------------------


def _cli_available(command: str) -> bool:
    """True if *command* is on PATH."""
    return shutil.which(command) is not None


def _env_set(*names: str) -> bool:
    """True if any of the env vars is set and non-empty."""
    return any(os.getenv(n) for n in names)


def _tool_registered(tool_name: str) -> bool:
    """Check whether a Hermes tool is registered and available.

    Imports the registry lazily so this module stays light.
    """
    try:
        from tools.registry import registry

        entry = registry.get(tool_name)
        if entry is None:
            return False
        if entry.check_fn:
            return bool(entry.check_fn())
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Built-in backend probes
# ---------------------------------------------------------------------------


# --- Web page reading -------------------------------------------------------

def _probe_web_hermes() -> ProbeResult:
    if _tool_registered("web_extract"):
        return ProbeResult(True, "ok", "Hermes web_extract tool available")
    return ProbeResult(False, "missing", "web_extract tool not available")


def _probe_web_jina() -> ProbeResult:
    if _cli_available("curl"):
        return ProbeResult(
            True, "ok", "Jina Reader via curl (r.jina.ai) — no key needed"
        )
    return ProbeResult(False, "missing", "curl not found", fix=["install curl"])


def _probe_web_browser() -> ProbeResult:
    if _tool_registered("browser_navigate"):
        return ProbeResult(True, "ok", "Browser automation fallback available")
    return ProbeResult(
        False, "missing", "browser tool not enabled", fix=["hermes tools enable browser"]
    )


# --- Web search -------------------------------------------------------------

def _probe_search_hermes() -> ProbeResult:
    if _tool_registered("web_search"):
        return ProbeResult(True, "ok", "Hermes web_search tool available")
    return ProbeResult(
        False,
        "misconfigured",
        "web_search tool not available",
        fix=["hermes tools enable web", "set a search API key in .env"],
        needs_user=True,
    )


def _probe_search_exa() -> ProbeResult:
    if _env_set("EXA_API_KEY"):
        return ProbeResult(True, "ok", "Exa API key configured")
    return ProbeResult(
        False,
        "missing",
        "Exa key not set (free at exa.ai)",
        fix=["export EXA_API_KEY=... in ~/.hermes/.env"],
        needs_user=True,
    )


# --- Twitter/X --------------------------------------------------------------

def _probe_x_search_tool() -> ProbeResult:
    if _tool_registered("x_search"):
        return ProbeResult(True, "ok", "x_search tool (xAI Responses API) available")
    if _env_set("XAI_API_KEY"):
        return ProbeResult(True, "ok", "XAI_API_KEY set — x_search should work")
    return ProbeResult(
        False,
        "misconfigured",
        "x_search tool not registered (needs xAI credentials)",
        fix=["hermes auth add xai-oauth", "or set XAI_API_KEY in .env"],
        needs_user=True,
    )


def _probe_xurl_cli() -> ProbeResult:
    if _cli_available("xurl"):
        return ProbeResult(True, "ok", "xurl CLI available")
    return ProbeResult(
        False,
        "missing",
        "xurl CLI not installed",
        fix=["pipx install xurl", "or: hermes skills install xurl"],
    )


def _probe_twitter_cli() -> ProbeResult:
    if _cli_available("twitter"):
        return ProbeResult(True, "ok", "twitter-cli available (cookie-based, free)")
    return ProbeResult(
        False,
        "missing",
        "twitter-cli not installed",
        fix=["pipx install twitter-cli", "then: twitter login (browser cookie)"],
        needs_user=True,
    )


# --- YouTube ----------------------------------------------------------------

def _probe_yt_transcript_skill() -> ProbeResult:
    # transcriptapi / youtube-transcript skills are skill-level, not tools.
    # Check if the skill is installed.
    try:
        from hermes_constants import get_hermes_home

        skills_dir = get_hermes_home() / "skills"
        for name in ("transcriptapi", "youtube-content", "yt", "youtube-data"):
            if (skills_dir / name / "SKILL.md").exists():
                return ProbeResult(
                    True, "ok", f"YouTube transcript skill '{name}' installed"
                )
    except Exception:
        pass
    return ProbeResult(
        False,
        "missing",
        "no YouTube transcript skill installed",
        fix=["hermes skills install transcriptapi"],
    )


def _probe_yt_dlp() -> ProbeResult:
    if _cli_available("yt-dlp"):
        return ProbeResult(True, "ok", "yt-dlp available — subtitles + metadata")
    # yt-dlp might be installed as a Python package but not on PATH
    try:
        import importlib

        importlib.import_module("yt_dlp")
        return ProbeResult(True, "ok", "yt-dlp Python package available")
    except Exception:
        pass
    return ProbeResult(
        False,
        "missing",
        "yt-dlp not installed",
        fix=["pip install yt-dlp", "or: pipx install yt-dlp"],
    )


# --- Reddit -----------------------------------------------------------------

def _probe_reddit_opencli() -> ProbeResult:
    if _cli_available("opencli"):
        return ProbeResult(True, "ok", "OpenCLI available — browser session for Reddit")
    return ProbeResult(
        False,
        "missing",
        "OpenCLI not installed",
        fix=["pipx install opencli", "then: opencli reddit login"],
        needs_user=True,
    )


def _probe_reddit_rdt() -> ProbeResult:
    if _cli_available("rdt"):
        return ProbeResult(True, "ok", "rdt-cli available")
    return ProbeResult(
        False,
        "missing",
        "rdt-cli not installed",
        fix=[
            "pipx install 'git+https://github.com/public-clis/rdt-cli.git@5e4fb37'",
            "then: rdt login",
        ],
        needs_user=True,
    )


# --- GitHub -----------------------------------------------------------------

def _probe_gh_cli() -> ProbeResult:
    if _cli_available("gh"):
        # Check auth status
        try:
            r = subprocess.run(
                ["gh", "auth", "status"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if r.returncode == 0:
                return ProbeResult(True, "ok", "gh CLI authenticated")
            return ProbeResult(
                False,
                "misconfigured",
                "gh CLI installed but not authenticated",
                fix=["gh auth login"],
                needs_user=True,
            )
        except Exception:
            return ProbeResult(True, "ok", "gh CLI available (auth check skipped)")
    return ProbeResult(
        False,
        "missing",
        "gh CLI not installed",
        fix=["sudo apt install gh", "or: brew install gh"],
    )


def _probe_github_api() -> ProbeResult:
    if _env_set("GITHUB_TOKEN", "GH_TOKEN"):
        return ProbeResult(True, "ok", "GITHUB_TOKEN set — REST API available")
    # Unauthenticated GitHub API works at low rate limits
    if _cli_available("curl"):
        return ProbeResult(
            True, "ok", "GitHub REST API via curl (unauthenticated, rate-limited)"
        )
    return ProbeResult(False, "missing", "no GitHub API access path")


# --- RSS --------------------------------------------------------------------

def _probe_rss_feedparser() -> ProbeResult:
    try:
        import feedparser  # noqa: F401

        return ProbeResult(True, "ok", "feedparser Python package available")
    except Exception:
        pass
    return ProbeResult(
        False, "missing", "feedparser not installed", fix=["pip install feedparser"]
    )


def _probe_rss_hermes() -> ProbeResult:
    # Hermes doesn't have a dedicated RSS tool, but web_extract can read feeds
    if _tool_registered("web_extract"):
        return ProbeResult(True, "ok", "web_extract can parse RSS/Atom feeds")
    return ProbeResult(False, "missing", "web_extract tool not available")


# --- Bilibili ---------------------------------------------------------------

def _probe_bili_cli() -> ProbeResult:
    if _cli_available("bili"):
        return ProbeResult(True, "ok", "bili-cli available — search + video detail")
    return ProbeResult(
        False,
        "missing",
        "bili-cli not installed",
        fix=["pipx install bilibili-cli"],
    )


# --- XiaoHongShu ------------------------------------------------------------

def _probe_xhs_opencli() -> ProbeResult:
    if _cli_available("opencli"):
        return ProbeResult(True, "ok", "OpenCLI available — XiaoHongShu via browser session")
    return ProbeResult(
        False,
        "missing",
        "OpenCLI not installed (desktop: browser session)",
        fix=["pipx install opencli"],
        needs_user=True,
    )


def _probe_xhs_mcp() -> ProbeResult:
    try:
        from hermes_constants import get_hermes_home

        cfg = get_hermes_home() / "config.yaml"
        if cfg.exists():
            text = cfg.read_text(errors="replace")
            if "xiaohongshu" in text.lower():
                return ProbeResult(True, "ok", "xiaohongshu-mcp configured")
    except Exception:
        pass
    return ProbeResult(
        False,
        "missing",
        "xiaohongshu-mcp not configured",
        fix=[
            "download from github.com/xpzouying/xiaohongshu-mcp/releases",
            "hermes mcp add xiaohongshu --command ...",
        ],
        needs_user=True,
    )


# --- LinkedIn ---------------------------------------------------------------

def _probe_linkedin_jina() -> ProbeResult:
    if _cli_available("curl"):
        return ProbeResult(True, "ok", "LinkedIn public pages via Jina Reader")
    return ProbeResult(False, "missing", "curl not found")


def _probe_linkedin_mcp() -> ProbeResult:
    if _env_set("LINKEDIN_MCP_PORT"):
        return ProbeResult(True, "ok", "linkedin-scraper-mcp running")
    return ProbeResult(
        False,
        "missing",
        "linkedin-scraper-mcp not configured",
        fix=["pip install linkedin-scraper-mcp", "then: linkedin-scraper-mcp --login"],
        needs_user=True,
    )


# --- V2EX -------------------------------------------------------------------

def _probe_v2ex_api() -> ProbeResult:
    if _cli_available("curl"):
        return ProbeResult(True, "ok", "V2EX public JSON API via curl (no auth needed)")
    return ProbeResult(False, "missing", "curl not found")


# --- Xueqiu -----------------------------------------------------------------

def _probe_xueqiu_cookie() -> ProbeResult:
    try:
        from hermes_constants import get_hermes_home

        cfg = get_hermes_home() / ".agent-reach" / "config.json"
        if cfg.exists():
            return ProbeResult(True, "ok", "Xueqiu cookie configured")
    except Exception:
        pass
    if _env_set("XUEQIU_COOKIE"):
        return ProbeResult(True, "ok", "XUEQIU_COOKIE env set")
    return ProbeResult(
        False,
        "missing",
        "Xueqiu cookie not configured",
        fix=["log in to xueqiu.com in browser", "export cookie via Cookie-Editor"],
        needs_user=True,
    )


# --- Podcast (Xiaoyuzhou) ---------------------------------------------------

def _probe_xiaoyuzhou() -> ProbeResult:
    if _env_set("GROQ_API_KEY"):
        return ProbeResult(True, "ok", "Groq key set — podcast transcription ready")
    return ProbeResult(
        False,
        "missing",
        "Groq API key not set (free at console.groq.com)",
        fix=["export GROQ_API_KEY=gsk_... in ~/.hermes/.env"],
        needs_user=True,
    )


# ---------------------------------------------------------------------------
# Functional probes — actually test the backend with a real query.
# These make network calls and are slower; only used with --functional.
# ---------------------------------------------------------------------------


def _func_probe_web_jina() -> ProbeResult:
    """Actually fetch a test page via Jina Reader."""
    import subprocess

    try:
        r = subprocess.run(
            ["curl", "-sL", "--max-time", "10", "https://r.jina.ai/https://example.com"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if r.returncode == 0 and r.stdout and "Example Domain" in r.stdout:
            return ProbeResult(True, "ok", "Jina Reader: successfully fetched test page")
        return ProbeResult(False, "misconfigured", f"Jina Reader: unexpected response (len={len(r.stdout or '')})")
    except Exception as exc:
        return ProbeResult(False, "misconfigured", f"Jina Reader: {exc}")


def _func_probe_web_hermes() -> ProbeResult:
    """Actually call web_extract on a test URL."""
    try:
        from tools.registry import registry as tool_reg

        entry = tool_reg.get_entry("web_extract")
        if entry is None:
            return ProbeResult(False, "missing", "web_extract tool not registered")
        result = entry.handler({"urls": ["https://example.com"]}, task_id="reach_func_test")
        data = json.loads(result) if isinstance(result, str) else result
        if data.get("success") or data.get("results"):
            return ProbeResult(True, "ok", "web_extract: successfully fetched test page")
        return ProbeResult(False, "misconfigured", f"web_extract: {data.get('error', 'no results')}")
    except Exception as exc:
        return ProbeResult(False, "misconfigured", f"web_extract: {exc}")


def _func_probe_gh_cli() -> ProbeResult:
    """Actually run a GitHub API call via gh."""
    import subprocess

    try:
        r = subprocess.run(
            ["gh", "api", "repos/octocat/Hello-World", "--jq", ".full_name"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if r.returncode == 0 and r.stdout.strip():
            return ProbeResult(True, "ok", f"gh CLI: API call succeeded ({r.stdout.strip()})")
        return ProbeResult(False, "misconfigured", f"gh CLI: {r.stderr.strip()[:200]}")
    except Exception as exc:
        return ProbeResult(False, "misconfigured", f"gh CLI: {exc}")


def _func_probe_github_api() -> ProbeResult:
    """Actually call the GitHub REST API."""
    import subprocess

    try:
        r = subprocess.run(
            ["curl", "-sL", "--max-time", "10", "https://api.github.com/repos/octocat/Hello-World"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if r.returncode == 0 and '"full_name"' in r.stdout:
            return ProbeResult(True, "ok", "GitHub REST API: call succeeded")
        return ProbeResult(False, "misconfigured", "GitHub REST API: no valid response")
    except Exception as exc:
        return ProbeResult(False, "misconfigured", f"GitHub API: {exc}")


def _func_probe_yt_dlp() -> ProbeResult:
    """Actually extract metadata from a known YouTube video."""
    import subprocess

    try:
        r = subprocess.run(
            ["yt-dlp", "--dump-json", "--no-download", "--max-filesize", "0",
             "https://www.youtube.com/watch?v=dQw4w9WgXcQ"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if r.returncode == 0 and r.stdout:
            data = json.loads(r.stdout)
            return ProbeResult(True, "ok", f"yt-dlp: extracted metadata for '{data.get('title', '?')[:50]}'")
        return ProbeResult(False, "misconfigured", f"yt-dlp: {r.stderr.strip()[:200]}")
    except Exception as exc:
        return ProbeResult(False, "misconfigured", f"yt-dlp: {exc}")


def _func_probe_yt_transcript_skill() -> ProbeResult:
    """Actually try to get a transcript via the skill."""
    # Skills are not directly callable from here; check if the skill exists
    # and report that a functional test requires the agent to invoke it.
    static_result = _probe_yt_transcript_skill()
    if static_result.available:
        return ProbeResult(
            True,
            "ok",
            "YouTube transcript skill installed (functional test requires agent invocation)",
        )
    return static_result


def _func_probe_rss_feedparser() -> ProbeResult:
    """Actually parse a known RSS feed."""
    try:
        import feedparser

        feed = feedparser.parse("https://hnrss.org/frontpage")
        if feed.entries:
            return ProbeResult(
                True, "ok", f"feedparser: parsed {len(feed.entries)} entries from HN RSS"
            )
        return ProbeResult(False, "misconfigured", "feedparser: no entries in test feed")
    except Exception as exc:
        return ProbeResult(False, "misconfigured", f"feedparser: {exc}")


def _func_probe_v2ex_api() -> ProbeResult:
    """Actually call the V2EX API."""
    import subprocess

    try:
        r = subprocess.run(
            ["curl", "-sL", "--max-time", "10", "https://www.v2ex.com/api/topics/hot.json"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if r.returncode == 0 and r.stdout.startswith("["):
            topics = json.loads(r.stdout)
            return ProbeResult(True, "ok", f"V2EX API: got {len(topics)} hot topics")
        return ProbeResult(False, "misconfigured", "V2EX API: no valid JSON response")
    except Exception as exc:
        return ProbeResult(False, "misconfigured", f"V2EX: {exc}")


def _func_probe_x_search_tool() -> ProbeResult:
    """Actually try an x_search query."""
    try:
        from tools.registry import registry as tool_reg

        entry = tool_reg.get_entry("x_search")
        if entry is None:
            return ProbeResult(False, "missing", "x_search tool not registered")
        result = entry.handler({"query": "test", "max_results": 1}, task_id="reach_func_test")
        data = json.loads(result) if isinstance(result, str) else result
        if "answer" in data or "results" in data:
            return ProbeResult(True, "ok", "x_search: query succeeded")
        return ProbeResult(False, "misconfigured", f"x_search: {str(data)[:200]}")
    except Exception as exc:
        return ProbeResult(False, "misconfigured", f"x_search: {exc}")


def _func_probe_linkedin_jina() -> ProbeResult:
    """Actually try reading a public LinkedIn page via Jina."""
    import subprocess

    try:
        r = subprocess.run(
            ["curl", "-sL", "--max-time", "10", "https://r.jina.ai/https://www.linkedin.com/company/google/"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if r.returncode == 0 and r.stdout and len(r.stdout) > 100:
            return ProbeResult(True, "ok", "LinkedIn via Jina: page fetched")
        return ProbeResult(False, "misconfigured", "LinkedIn via Jina: insufficient content")
    except Exception as exc:
        return ProbeResult(False, "misconfigured", f"LinkedIn Jina: {exc}")


def _func_probe_reddit_jina() -> ProbeResult:
    """Actually try reading a Reddit page via Jina."""
    import subprocess

    try:
        r = subprocess.run(
            ["curl", "-sL", "--max-time", "10", "https://r.jina.ai/https://old.reddit.com/r/programming/hot.json"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if r.returncode == 0 and r.stdout and ("children" in r.stdout or "data" in r.stdout):
            return ProbeResult(True, "ok", "Reddit via Jina: content fetched")
        return ProbeResult(False, "misconfigured", "Reddit via Jina: no valid content")
    except Exception as exc:
        return ProbeResult(False, "misconfigured", f"Reddit Jina: {exc}")


def _func_probe_bili_cli() -> ProbeResult:
    """Actually try a Bilibili search."""
    import subprocess

    try:
        r = subprocess.run(
            ["bili", "search", "test", "--type", "video", "--limit", "1"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if r.returncode == 0 and r.stdout:
            return ProbeResult(True, "ok", "bili-cli: search returned results")
        return ProbeResult(False, "misconfigured", f"bili-cli: {r.stderr.strip()[:200]}")
    except Exception as exc:
        return ProbeResult(False, "misconfigured", f"bili-cli: {exc}")


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class ReachRegistry:
    """Holds all capabilities and provides doctor / resolve operations."""

    def __init__(self) -> None:
        self._capabilities: Dict[str, Capability] = {}
        self._register_builtin()

    def register(self, cap: Capability) -> None:
        self._capabilities[cap.name] = cap

    def get(self, name: str) -> Optional[Capability]:
        return self._capabilities.get(name)

    def all_capabilities(self) -> List[Capability]:
        return sorted(self._capabilities.values(), key=lambda c: c.name)

    def resolve(self, name: str) -> Optional[Backend]:
        """Return the best available backend for a capability, or None."""
        cap = self._capabilities.get(name)
        if cap is None:
            return None
        return cap.best_backend()

    def doctor(
        self,
        capability_name: Optional[str] = None,
        functional: bool = False,
    ) -> List[Dict]:
        """Run probes and return structured results.

        If *capability_name* is given, probe only that capability.
        Otherwise probe all.

        If *functional* is True, run functional probes (real network calls)
        instead of static availability checks.
        """
        caps: List[Capability]
        if capability_name:
            cap = self._capabilities.get(capability_name)
            if cap is None:
                return []
            caps = [cap]
        else:
            caps = self.all_capabilities()

        results: List[Dict] = []
        for cap in caps:
            cap_entry: Dict = {
                "capability": cap.name,
                "description": cap.description,
                "backends": [],
                "best": None,
                "functional": functional,
            }
            best_found = False
            probe_results = (
                cap.probe_all_functional() if functional else cap.probe_all()
            )
            for result in probe_results:
                cap_entry["backends"].append(
                    {
                        "name": result.backend_name,
                        "available": result.available,
                        "status": result.status,
                        "detail": result.detail,
                        "fix": result.fix,
                        "needs_user": result.needs_user,
                        "icon": result.icon(),
                    }
                )
                if result.available and not best_found:
                    cap_entry["best"] = result.backend_name
                    best_found = True
            results.append(cap_entry)
        return results

    def _register_builtin(self) -> None:
        """Register all built-in capabilities and their backends."""
        # -- Web page reading --
        self.register(
            Capability(
                name="web.read",
                description="Read any web page and get clean text",
                backends=[
                    Backend(
                        "hermes_web_extract",
                        "Hermes web_extract tool",
                        _probe_web_hermes,
                        functional_probe=_func_probe_web_hermes,
                        tool_name="web_extract",
                        priority=10,
                    ),
                    Backend(
                        "jina_reader",
                        "Jina Reader (r.jina.ai) — free, no key",
                        _probe_web_jina,
                        functional_probe=_func_probe_web_jina,
                        cli_command="curl",
                        priority=20,
                    ),
                    Backend(
                        "browser_fallback",
                        "Browser automation fallback",
                        _probe_web_browser,
                        tool_name="browser_navigate",
                        priority=30,
                    ),
                ],
            )
        )

        # -- Web search --
        self.register(
            Capability(
                name="web.search",
                description="Semantic web search",
                backends=[
                    Backend(
                        "hermes_web_search",
                        "Hermes web_search tool",
                        _probe_search_hermes,
                        tool_name="web_search",
                        priority=10,
                    ),
                    Backend(
                        "exa_search",
                        "Exa semantic search (free key)",
                        _probe_search_exa,
                        priority=20,
                    ),
                ],
            )
        )

        # -- Twitter/X --
        self.register(
            Capability(
                name="twitter.search",
                description="Search tweets on X/Twitter",
                backends=[
                    Backend(
                        "x_search_tool",
                        "xAI x_search Responses API tool",
                        _probe_x_search_tool,
                        functional_probe=_func_probe_x_search_tool,
                        tool_name="x_search",
                        priority=10,
                    ),
                    Backend(
                        "xurl_cli",
                        "xurl CLI (cookie-based, free)",
                        _probe_xurl_cli,
                        cli_command="xurl",
                        priority=20,
                    ),
                    Backend(
                        "twitter_cli",
                        "twitter-cli (cookie-based, free)",
                        _probe_twitter_cli,
                        cli_command="twitter",
                        priority=30,
                    ),
                ],
            )
        )
        self.register(
            Capability(
                name="twitter.read",
                description="Read a single tweet by URL",
                backends=[
                    Backend(
                        "xurl_cli",
                        "xurl CLI",
                        _probe_xurl_cli,
                        cli_command="xurl",
                        priority=10,
                    ),
                    Backend(
                        "twitter_cli",
                        "twitter-cli",
                        _probe_twitter_cli,
                        cli_command="twitter",
                        priority=20,
                    ),
                    Backend(
                        "jina_reader",
                        "Jina Reader (public pages)",
                        _probe_web_jina,
                        priority=30,
                    ),
                ],
            )
        )

        # -- YouTube --
        self.register(
            Capability(
                name="youtube.transcript",
                description="Extract YouTube video subtitles/transcript",
                backends=[
                    Backend(
                        "transcript_skill",
                        "Hermes YouTube transcript skill",
                        _probe_yt_transcript_skill,
                        functional_probe=_func_probe_yt_transcript_skill,
                        priority=10,
                    ),
                    Backend(
                        "yt_dlp",
                        "yt-dlp (subtitles + metadata)",
                        _probe_yt_dlp,
                        functional_probe=_func_probe_yt_dlp,
                        cli_command="yt-dlp",
                        priority=20,
                    ),
                ],
            )
        )
        self.register(
            Capability(
                name="youtube.search",
                description="Search YouTube videos",
                backends=[
                    Backend(
                        "yt_dlp",
                        "yt-dlp (metadata extraction)",
                        _probe_yt_dlp,
                        cli_command="yt-dlp",
                        priority=10,
                    ),
                    Backend(
                        "hermes_web_search",
                        "Hermes web_search (site:youtube.com)",
                        _probe_search_hermes,
                        tool_name="web_search",
                        priority=20,
                    ),
                ],
            )
        )

        # -- Reddit --
        self.register(
            Capability(
                name="reddit.search",
                description="Search Reddit posts and comments",
                backends=[
                    Backend(
                        "opencli",
                        "OpenCLI (browser session, desktop)",
                        _probe_reddit_opencli,
                        cli_command="opencli",
                        priority=10,
                    ),
                    Backend(
                        "rdt_cli",
                        "rdt-cli (cookie-based)",
                        _probe_reddit_rdt,
                        cli_command="rdt",
                        priority=20,
                    ),
                ],
            )
        )
        self.register(
            Capability(
                name="reddit.read",
                description="Read a Reddit post and its comments",
                backends=[
                    Backend(
                        "opencli",
                        "OpenCLI",
                        _probe_reddit_opencli,
                        cli_command="opencli",
                        priority=10,
                    ),
                    Backend(
                        "rdt_cli",
                        "rdt-cli",
                        _probe_reddit_rdt,
                        cli_command="rdt",
                        priority=20,
                    ),
                    Backend(
                        "jina_reader",
                        "Jina Reader (old.reddit.com public)",
                        _probe_web_jina,
                        functional_probe=_func_probe_reddit_jina,
                        priority=30,
                    ),
                ],
            )
        )

        # -- GitHub --
        self.register(
            Capability(
                name="github.read",
                description="Read GitHub repos, issues, PRs",
                backends=[
                    Backend(
                        "gh_cli",
                        "gh CLI (authenticated)",
                        _probe_gh_cli,
                        functional_probe=_func_probe_gh_cli,
                        cli_command="gh",
                        priority=10,
                    ),
                    Backend(
                        "github_api",
                        "GitHub REST API (token or unauthenticated)",
                        _probe_github_api,
                        functional_probe=_func_probe_github_api,
                        priority=20,
                    ),
                    Backend(
                        "jina_reader",
                        "Jina Reader (public pages)",
                        _probe_web_jina,
                        priority=30,
                    ),
                ],
            )
        )

        # -- RSS --
        self.register(
            Capability(
                name="rss.read",
                description="Read RSS/Atom feeds",
                backends=[
                    Backend(
                        "feedparser",
                        "feedparser (Python)",
                        _probe_rss_feedparser,
                        functional_probe=_func_probe_rss_feedparser,
                        priority=10,
                    ),
                    Backend(
                        "hermes_web_extract",
                        "Hermes web_extract (XML feeds)",
                        _probe_rss_hermes,
                        tool_name="web_extract",
                        priority=20,
                    ),
                    Backend(
                        "jina_reader",
                        "Jina Reader",
                        _probe_web_jina,
                        priority=30,
                    ),
                ],
            )
        )

        # -- Bilibili --
        self.register(
            Capability(
                name="bilibili.search",
                description="Search Bilibili videos",
                backends=[
                    Backend(
                        "bili_cli",
                        "bili-cli (no login needed)",
                        _probe_bili_cli,
                        functional_probe=_func_probe_bili_cli,
                        cli_command="bili",
                        priority=10,
                    ),
                ],
            )
        )

        # -- XiaoHongShu --
        self.register(
            Capability(
                name="xiaohongshu.search",
                description="Search XiaoHongShu notes",
                backends=[
                    Backend(
                        "opencli",
                        "OpenCLI (desktop browser session)",
                        _probe_xhs_opencli,
                        cli_command="opencli",
                        priority=10,
                    ),
                    Backend(
                        "xiaohongshu_mcp",
                        "xiaohongshu-mcp (server, QR login)",
                        _probe_xhs_mcp,
                        priority=20,
                    ),
                ],
            )
        )

        # -- LinkedIn --
        self.register(
            Capability(
                name="linkedin.read",
                description="Read LinkedIn profiles and job postings",
                backends=[
                    Backend(
                        "jina_reader",
                        "Jina Reader (public LinkedIn pages)",
                        _probe_linkedin_jina,
                        functional_probe=_func_probe_linkedin_jina,
                        priority=10,
                    ),
                    Backend(
                        "linkedin_mcp",
                        "linkedin-scraper-mcp (full profiles)",
                        _probe_linkedin_mcp,
                        priority=20,
                    ),
                ],
            )
        )

        # -- V2EX --
        self.register(
            Capability(
                name="v2ex.read",
                description="Read V2EX hot topics and posts",
                backends=[
                    Backend(
                        "v2ex_api",
                        "V2EX public JSON API (no auth)",
                        _probe_v2ex_api,
                        functional_probe=_func_probe_v2ex_api,
                        cli_command="curl",
                        priority=10,
                    ),
                ],
            )
        )

        # -- Xueqiu --
        self.register(
            Capability(
                name="xueqiu.read",
                description="Read Xueqiu stock quotes and posts",
                backends=[
                    Backend(
                        "xueqiu_cookie",
                        "Xueqiu (browser cookie required)",
                        _probe_xueqiu_cookie,
                        priority=10,
                    ),
                ],
            )
        )

        # -- Podcast (Xiaoyuzhou) --
        self.register(
            Capability(
                name="podcast.transcribe",
                description="Transcribe podcast audio to text (Xiaoyuzhou)",
                backends=[
                    Backend(
                        "xiaoyuzhou_groq",
                        "Groq Whisper transcription (free key)",
                        _probe_xiaoyuzhou,
                        priority=10,
                    ),
                ],
            )
        )


# ---------------------------------------------------------------------------
# Singleton + convenience
# ---------------------------------------------------------------------------

_registry: Optional[ReachRegistry] = None


def get_reach_registry() -> ReachRegistry:
    global _registry
    if _registry is None:
        _registry = ReachRegistry()
    return _registry


def format_doctor_output(results: List[Dict], json_output: bool = False) -> str:
    """Format doctor results for CLI display or JSON."""
    if json_output:
        return json.dumps(results, indent=2, ensure_ascii=False)

    lines: List[str] = []
    lines.append("")
    lines.append("  Internet Reach — Capability Doctor")
    lines.append("  " + "=" * 48)
    lines.append("")

    available_count = 0
    total_count = len(results)

    for cap in results:
        best = cap.get("best")
        if best:
            available_count += 1
            icon = "✅"
        else:
            # Check if any backend is a warning (not fully missing)
            any_warning = any(
                b["status"] == "misconfigured" for b in cap["backends"]
            )
            icon = "⚠️" if any_warning else "❌"

        lines.append(f"  {icon} {cap['capability']}")
        lines.append(f"     {cap['description']}")
        if best:
            lines.append(f"     → using: {best}")
        for b in cap["backends"]:
            b_icon = "✅" if b["available"] else ("⚠️" if b["status"] == "misconfigured" else "❌")
            lines.append(f"       {b_icon} {b['name']}: {b['detail']}")
            if not b["available"] and b["fix"]:
                for fix_step in b["fix"]:
                    lines.append(f"          fix: {fix_step}")
        lines.append("")

    lines.append(f"  {available_count}/{total_count} capabilities available")
    lines.append("")
    return "\n".join(lines)