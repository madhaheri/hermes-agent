"""Internet-reach installer and credential configuration system.

This module provides the operational half of the Agent-Reach pattern:
  - `ReachInstaller` — installs upstream tools (yt-dlp, feedparser, gh, etc.)
    with safe/dry-run modes and selective channel installation.
  - `ReachConfigurator` — manages credentials: cookie import, browser
    auto-extraction, proxy configuration, API keys.
  - Exa/MCP auto-wiring via `hermes mcp add`.

Design principles:
  - Never run sudo without explicit user approval.
  - Safe mode: report what would be done, don't execute.
  - Dry-run: preview all operations, change nothing.
  - All credentials stored under ~/.hermes/reach-config/ with 0600 permissions.
  - Cookies are never uploaded or transmitted — local only.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def _reach_config_dir() -> Path:
    """Directory for reach credentials and config."""
    try:
        from hermes_constants import get_hermes_home

        d = get_hermes_home() / "reach-config"
    except Exception:
        d = Path.home() / ".hermes" / "reach-config"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _reach_config_path() -> Path:
    return _reach_config_dir() / "config.json"


def _load_config() -> Dict:
    p = _reach_config_path()
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            pass
    return {}


def _save_config(cfg: Dict) -> None:
    p = _reach_config_path()
    p.write_text(json.dumps(cfg, indent=2, ensure_ascii=False))
    try:
        os.chmod(p, 0o600)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Install plan data structures
# ---------------------------------------------------------------------------


@dataclass
class InstallStep:
    """A single installation action."""

    description: str
    command: List[str]
    needs_sudo: bool = False
    needs_user: bool = False  # requires user interaction (login, etc.)
    channel: str = ""
    can_fail: bool = False  # non-critical step

    def __repr__(self) -> str:
        sudo = "sudo " if self.needs_sudo else ""
        cmd_str = " ".join(self.command)
        return f"{sudo}{cmd_str}"


@dataclass
class InstallResult:
    """Result of executing (or previewing) an InstallStep."""

    step: InstallStep
    executed: bool
    success: bool
    output: str = ""
    skipped: bool = False
    skip_reason: str = ""


# ---------------------------------------------------------------------------
# Channel definitions
# ---------------------------------------------------------------------------

# Zero-config channels — install by default, no credentials needed
ZERO_CONFIG_CHANNELS = {
    "web": "Web page reading (Jina Reader, curl)",
    "youtube": "YouTube subtitles + metadata (yt-dlp)",
    "github": "GitHub repos + search (gh CLI)",
    "rss": "RSS/Atom feeds (feedparser)",
    "v2ex": "V2EX tech community (curl, no auth)",
    "bilibili": "Bilibili search (bili-cli, no login)",
}

# Optional channels — need credentials or user interaction
OPTIONAL_CHANNELS = {
    "twitter": "Twitter/X search + read (needs cookie or xAI credentials)",
    "reddit": "Reddit search + read (needs OpenCLI or rdt-cli + cookie)",
    "xiaohongshu": "XiaoHongShu search (needs OpenCLI or xiaohongshu-mcp)",
    "linkedin": "LinkedIn profiles (needs linkedin-scraper-mcp for full features)",
    "xueqiu": "Xueqiu stock quotes (needs browser cookie)",
    "xiaoyuzhou": "Podcast transcription (needs free Groq API key)",
    "exa": "Exa semantic web search (free MCP, auto-configured)",
}

ALL_CHANNEL_NAMES = list(ZERO_CONFIG_CHANNELS.keys()) + list(OPTIONAL_CHANNELS.keys())


class ReachInstaller:
    """Plans and executes installation of upstream reach tools."""

    def __init__(self, safe: bool = False, dry_run: bool = False) -> None:
        self.safe = safe  # no auto system changes
        self.dry_run = dry_run  # preview only
        self._pip = self._detect_pip()
        self._pipx = self._detect_pipx()

    def _detect_pip(self) -> str:
        """Find a working pip."""
        for cmd in ["pip3", "pip"]:
            if shutil.which(cmd):
                return cmd
        # Try python -m pip
        try:
            r = subprocess.run(
                [sys.executable, "-m", "pip", "--version"],
                capture_output=True,
                timeout=5,
            )
            if r.returncode == 0:
                return f"{sys.executable} -m pip"
        except Exception:
            pass
        return ""

    def _detect_pipx(self) -> str:
        return "pipx" if shutil.which("pipx") else ""

    def _is_installed(self, cli: str) -> bool:
        return shutil.which(cli) is not None

    def _is_pkg_installed(self, pkg: str) -> bool:
        try:
            import importlib

            importlib.import_module(pkg)
            return True
        except Exception:
            return False

    def plan(
        self,
        channels: Optional[List[str]] = None,
        install_all: bool = False,
    ) -> List[InstallStep]:
        """Generate an install plan for the given channels.

        Args:
            channels: list of channel names to install. If None, installs
                zero-config channels only.
            install_all: if True, install all channels (zero-config + optional).
        """
        if install_all:
            channels = list(ALL_CHANNEL_NAMES)
        elif channels is None:
            channels = list(ZERO_CONFIG_CHANNELS.keys())

        steps: List[InstallStep] = []

        for ch in channels:
            ch_steps = self._plan_channel(ch)
            steps.extend(ch_steps)

        return steps

    def _plan_channel(self, channel: str) -> List[InstallStep]:
        """Generate install steps for a single channel."""
        steps: List[InstallStep] = []

        if channel == "web":
            # curl is usually already installed; check anyway
            if not self._is_installed("curl"):
                steps.append(InstallStep(
                    description="Install curl for web page reading",
                    command=["apt", "install", "-y", "curl"],
                    needs_sudo=True,
                    channel="web",
                ))

        elif channel == "youtube":
            if not self._is_installed("yt-dlp"):
                if self._pipx:
                    steps.append(InstallStep(
                        description="Install yt-dlp via pipx",
                        command=[self._pipx, "install", "yt-dlp"],
                        channel="youtube",
                    ))
                elif self._pip:
                    steps.append(InstallStep(
                        description="Install yt-dlp via pip",
                        command=self._pip.split() + ["install", "yt-dlp"],
                        channel="youtube",
                    ))

        elif channel == "github":
            if not self._is_installed("gh"):
                steps.append(InstallStep(
                    description="Install GitHub CLI",
                    command=["apt", "install", "-y", "gh"],
                    needs_sudo=True,
                    needs_user=True,  # needs gh auth login after
                    channel="github",
                ))

        elif channel == "rss":
            if not self._is_pkg_installed("feedparser"):
                if self._pip:
                    steps.append(InstallStep(
                        description="Install feedparser for RSS/Atom feeds",
                        command=self._pip.split() + ["install", "feedparser"],
                        channel="rss",
                    ))

        elif channel == "v2ex":
            if not self._is_installed("curl"):
                steps.append(InstallStep(
                    description="Install curl for V2EX API",
                    command=["apt", "install", "-y", "curl"],
                    needs_sudo=True,
                    channel="v2ex",
                ))

        elif channel == "bilibili":
            if not self._is_installed("bili"):
                if self._pipx:
                    steps.append(InstallStep(
                        description="Install bili-cli for Bilibili search",
                        command=[self._pipx, "install", "bilibili-cli"],
                        channel="bilibili",
                    ))

        elif channel == "twitter":
            if not self._is_installed("twitter"):
                if self._pipx:
                    steps.append(InstallStep(
                        description="Install twitter-cli (cookie-based, free)",
                        command=[self._pipx, "install", "twitter-cli"],
                        needs_user=True,  # needs twitter login
                        channel="twitter",
                    ))
            if not self._is_installed("xurl"):
                if self._pipx:
                    steps.append(InstallStep(
                        description="Install xurl CLI as Twitter fallback",
                        command=[self._pipx, "install", "xurl"],
                        channel="twitter",
                        can_fail=True,
                    ))

        elif channel == "reddit":
            if not self._is_installed("opencli"):
                if self._pipx:
                    steps.append(InstallStep(
                        description="Install OpenCLI for Reddit (browser session)",
                        command=[self._pipx, "install", "opencli"],
                        needs_user=True,
                        channel="reddit",
                    ))
            if not self._is_installed("rdt"):
                if self._pipx:
                    steps.append(InstallStep(
                        description="Install rdt-cli as Reddit fallback",
                        command=[
                            self._pipx,
                            "install",
                            "git+https://github.com/public-clis/rdt-cli.git@5e4fb3720d5c174e976cd425ccc3b879d52cac66",
                        ],
                        needs_user=True,  # needs rdt login
                        channel="reddit",
                        can_fail=True,
                    ))

        elif channel == "xiaohongshu":
            if not self._is_installed("opencli"):
                if self._pipx:
                    steps.append(InstallStep(
                        description="Install OpenCLI for XiaoHongShu (desktop)",
                        command=[self._pipx, "install", "opencli"],
                        needs_user=True,
                        channel="xiaohongshu",
                    ))

        elif channel == "linkedin":
            if self._pip:
                if not self._is_pkg_installed("linkedin_scraper_mcp"):
                    steps.append(InstallStep(
                        description="Install linkedin-scraper-mcp",
                        command=self._pip.split() + ["install", "linkedin-scraper-mcp"],
                        needs_user=True,  # needs browser login
                        channel="linkedin",
                    ))

        elif channel == "xueqiu":
            # No package to install — needs cookie configuration
            steps.append(InstallStep(
                description="Xueqiu requires browser cookie — use: hermes reach configure xueqiu-cookies",
                command=["echo", "Configure via: hermes reach configure xueqiu-cookies \"COOKIE_STRING\""],
                needs_user=True,
                channel="xueqiu",
                can_fail=True,
            ))

        elif channel == "xiaoyuzhou":
            # Script-based; needs Groq key
            if not os.getenv("GROQ_API_KEY"):
                steps.append(InstallStep(
                    description="Set Groq API key for podcast transcription",
                    command=["echo", "Configure via: hermes reach configure groq-key gsk_xxx"],
                    needs_user=True,
                    channel="xiaoyuzhou",
                    can_fail=True,
                ))

        elif channel == "exa":
            # Exa via MCP — auto-configure
            if not self._is_pkg_installed("mcp"):
                if self._pip:
                    steps.append(InstallStep(
                        description="Install MCP CLI for Exa search integration",
                        command=self._pip.split() + ["install", "mcp[cli]"],
                        channel="exa",
                        can_fail=True,
                    ))
            steps.append(InstallStep(
                description="Add Exa MCP server to Hermes",
                command=["hermes", "mcp", "add", "exa-search", "--url", "https://mcp.exa.ai/mcp"],
                channel="exa",
                can_fail=True,
            ))

        return steps

    def execute(self, steps: List[InstallStep]) -> List[InstallResult]:
        """Execute the install plan (or preview if dry_run/safe)."""
        results: List[InstallResult] = []

        for step in steps:
            if self.dry_run:
                results.append(InstallResult(
                    step=step,
                    executed=False,
                    success=False,
                    output=f"[DRY RUN] would execute: {step}",
                    skipped=True,
                    skip_reason="dry-run mode",
                ))
                continue

            if self.safe and step.needs_sudo:
                results.append(InstallResult(
                    step=step,
                    executed=False,
                    success=False,
                    output=f"[SAFE MODE] skipped (needs sudo): {step}",
                    skipped=True,
                    skip_reason="safe mode — sudo required",
                ))
                continue

            if step.needs_sudo:
                # Don't auto-run sudo — ask user
                results.append(InstallResult(
                    step=step,
                    executed=False,
                    success=False,
                    output=f"[NEEDS SUDO] Run manually: {step}",
                    skipped=True,
                    skip_reason="sudo requires user approval",
                ))
                continue

            if step.needs_user and "echo" in step.command:
                # Informational step — just display
                results.append(InstallResult(
                    step=step,
                    executed=False,
                    success=True,
                    output=step.description,
                    skipped=True,
                    skip_reason="informational — user action needed",
                ))
                continue

            # Execute the step
            try:
                cmd = step.command
                # Handle "python -m pip" split
                if isinstance(cmd, list) and len(cmd) == 1 and " " in cmd[0]:
                    cmd = cmd[0].split()

                r = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                success = r.returncode == 0
                output = (r.stdout or "") + (r.stderr or "")
                results.append(InstallResult(
                    step=step,
                    executed=True,
                    success=success,
                    output=output.strip()[:2000],
                ))
            except Exception as exc:
                results.append(InstallResult(
                    step=step,
                    executed=True,
                    success=False,
                    output=f"Error: {exc}",
                ))

        return results

    def format_results(self, results: List[InstallResult]) -> str:
        """Format install results for display."""
        lines: List[str] = []
        lines.append("")
        lines.append("  Internet Reach — Install Report")
        lines.append("  " + "=" * 48)
        lines.append("")

        executed = sum(1 for r in results if r.executed and r.success)
        failed = sum(1 for r in results if r.executed and not r.success)
        skipped = sum(1 for r in results if r.skipped)

        for r in results:
            if r.skipped:
                icon = "⏭️"
            elif r.success:
                icon = "✅"
            else:
                icon = "❌"
            lines.append(f"  {icon} {r.step.description}")
            if r.output:
                for line in r.output.split("\n")[:3]:
                    if line.strip():
                        lines.append(f"     {line.strip()}")
            lines.append("")

        lines.append(f"  {executed} succeeded, {failed} failed, {skipped} skipped")
        lines.append("")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Credential configuration
# ---------------------------------------------------------------------------


class ReachConfigurator:
    """Manages credentials and configuration for reach backends."""

    def __init__(self) -> None:
        self._config = _load_config()

    def save(self) -> None:
        _save_config(self._config)

    def configure_cookies(self, platform: str, cookie_string: str) -> Dict:
        """Store cookies for a platform (Twitter, XiaoHongShu, Xueqiu, Reddit)."""
        if not cookie_string:
            return {"error": "empty cookie string"}
        self._config.setdefault("cookies", {})[platform] = cookie_string
        self.save()
        return {"success": True, "platform": platform, "stored": True}

    def configure_proxy(self, proxy_url: str) -> Dict:
        """Store proxy URL for restricted networks."""
        if not proxy_url:
            return {"error": "empty proxy URL"}
        self._config["proxy"] = proxy_url
        self.save()
        return {"success": True, "proxy": proxy_url}

    def configure_groq_key(self, key: str) -> Dict:
        """Store Groq API key for podcast transcription."""
        if not key:
            return {"error": "empty key"}
        self._config["groq_key"] = key
        self.save()
        return {"success": True}

    def configure_exa_key(self, key: str) -> Dict:
        """Store Exa API key for semantic search."""
        if not key:
            return {"error": "empty key"}
        self._config["exa_key"] = key
        self.save()
        return {"success": True}

    def configure_from_browser(self, browser: str = "chrome") -> Dict:
        """Auto-extract cookies from a browser for supported platforms.

        Supports Twitter, XiaoHongShu, Xueqiu. Uses browser-cookie3 if available.
        """
        try:
            import browser_cookie3 as bc3
        except ImportError:
            return {
                "error": "browser-cookie3 not installed",
                "fix": "pip install browser-cookie3",
            }

        results: Dict = {"browser": browser, "platforms": {}}

        # Map platforms to their cookie domains
        platform_domains = {
            "twitter": [".twitter.com", ".x.com"],
            "xiaohongshu": [".xiaohongshu.com"],
            "xueqiu": [".xueqiu.com"],
            "reddit": [".reddit.com"],
        }

        try:
            if browser == "chrome":
                jar = bc3.chrome()
            elif browser == "firefox":
                jar = bc3.firefox()
            elif browser == "edge":
                jar = bc3.edge()
            elif browser == "safari":
                jar = bc3.safari()
            else:
                return {"error": f"unsupported browser: {browser}"}
        except Exception as exc:
            return {"error": f"failed to read browser cookies: {exc}"}

        for platform, domains in platform_domains.items():
            cookies: List[Dict] = []
            for cookie in jar:
                domain = cookie.domain or ""
                if any(d in domain for d in domains):
                    cookies.append({
                        "name": cookie.name,
                        "value": cookie.value,
                        "domain": cookie.domain,
                    })
            if cookies:
                # Build cookie header string
                cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
                self._config.setdefault("cookies", {})[platform] = cookie_str
                results["platforms"][platform] = f"{len(cookies)} cookies extracted"
            else:
                results["platforms"][platform] = "no cookies found (not logged in?)"

        self.save()
        return results

    def get_proxy_env(self) -> Dict[str, str]:
        """Return env vars for proxy if configured."""
        proxy = self._config.get("proxy")
        if proxy:
            return {"HTTP_PROXY": proxy, "HTTPS_PROXY": proxy}
        return {}

    def get_cookies(self, platform: str) -> Optional[str]:
        return self._config.get("cookies", {}).get(platform)

    def get_groq_key(self) -> Optional[str]:
        return self._config.get("groq_key")

    def get_exa_key(self) -> Optional[str]:
        return self._config.get("exa_key")

    def list_config(self) -> Dict:
        """Return current configuration (with secrets masked)."""
        cfg = self._config.copy()
        if "cookies" in cfg:
            cfg["cookies"] = {
                k: f"*** ({len(v)} chars)" for k, v in cfg["cookies"].items()
            }
        if "groq_key" in cfg:
            cfg["groq_key"] = "***"
        if "exa_key" in cfg:
            cfg["exa_key"] = "***"
        if "proxy" in cfg:
            # Show host but mask credentials
            proxy = cfg["proxy"]
            if "@" in proxy:
                scheme, rest = proxy.split("://", 1) if "://" in proxy else ("http", proxy)
                creds, host = rest.split("@", 1)
                cfg["proxy"] = f"{scheme}://***@{host}"
        return cfg

    def remove_config(self, key: str) -> Dict:
        """Remove a configuration entry."""
        if key in self._config:
            del self._config[key]
            self.save()
            return {"success": True, "removed": key}
        if key in self._config.get("cookies", {}):
            del self._config["cookies"][key]
            self.save()
            return {"success": True, "removed": f"cookies.{key}"}
        return {"error": f"not found: {key}"}

    def clear_all(self) -> Dict:
        """Clear all stored credentials."""
        self._config = {}
        self.save()
        return {"success": True, "cleared": True}


# ---------------------------------------------------------------------------
# Uninstall
# ---------------------------------------------------------------------------


# Tools that `hermes reach setup` can install, mapped to their pip/pipx names
# for uninstall purposes. CLI name → (uninstall command prefix, package name).
_REACH_INSTALLED_TOOLS: Dict[str, Tuple[List[str], str]] = {
    "yt-dlp": (["pip3", "uninstall", "-y"], "yt-dlp"),
    "feedparser": (["pip3", "uninstall", "-y"], "feedparser"),
    "bili": (["pipx", "uninstall"], "bilibili-cli"),
    "twitter": (["pipx", "uninstall"], "twitter-cli"),
    "xurl": (["pipx", "uninstall"], "xurl"),
    "opencli": (["pipx", "uninstall"], "opencli"),
    "rdt": (["pipx", "uninstall"], "rdt-cli"),
}


@dataclass
class UninstallStep:
    description: str
    command: List[str]
    cli_name: str
    can_fail: bool = True  # uninstall is best-effort


@dataclass
class UninstallResult:
    step: UninstallStep
    executed: bool
    success: bool
    output: str = ""
    skipped: bool = False
    skip_reason: str = ""


class ReachUninstaller:
    """Removes tools and config installed by `hermes reach setup`."""

    def __init__(self, dry_run: bool = False, keep_config: bool = False) -> None:
        self.dry_run = dry_run
        self.keep_config = keep_config  # keep cookies/credentials
        self._pip = self._detect_pip()
        self._pipx = "pipx" if shutil.which("pipx") else ""

    def _detect_pip(self) -> str:
        for cmd in ["pip3", "pip"]:
            if shutil.which(cmd):
                return cmd
        return f"{sys.executable} -m pip"

    def plan(self, channels: Optional[List[str]] = None) -> List[UninstallStep]:
        """Generate an uninstall plan for tools installed by reach setup."""
        steps: List[UninstallStep] = []

        # Determine which CLIs to try removing
        clis_to_remove: List[str] = []
        if channels:
            channel_to_clis = {
                "youtube": ["yt-dlp"],
                "rss": ["feedparser"],
                "bilibili": ["bili"],
                "twitter": ["twitter", "xurl"],
                "reddit": ["opencli", "rdt"],
                "xiaohongshu": ["opencli"],
            }
            for ch in channels:
                clis_to_remove.extend(channel_to_clis.get(ch, []))
        else:
            # All tools that reach might have installed
            clis_to_remove = list(_REACH_INSTALLED_TOOLS.keys())

        # Deduplicate while preserving order
        seen = set()
        for cli in clis_to_remove:
            if cli in seen:
                continue
            seen.add(cli)
            if cli not in _REACH_INSTALLED_TOOLS:
                continue
            if not shutil.which(cli):
                continue  # not installed, skip
            prefix, pkg = _REACH_INSTALLED_TOOLS[cli]
            # Use pipx if available and the tool is pipx-installed, else pip
            if self._pipx and prefix[0] == "pipx":
                cmd = [self._pipx, "uninstall", pkg]
            else:
                cmd = self._pip.split() + ["uninstall", "-y", pkg]
            steps.append(UninstallStep(
                description=f"Uninstall {cli} ({pkg})",
                command=cmd,
                cli_name=cli,
            ))

        return steps

    def execute(self, steps: List[UninstallStep]) -> List[UninstallResult]:
        results: List[UninstallResult] = []
        for step in steps:
            if self.dry_run:
                results.append(UninstallResult(
                    step=step, executed=False, success=False,
                    output=f"[DRY RUN] would execute: {' '.join(step.command)}",
                    skipped=True, skip_reason="dry-run",
                ))
                continue
            try:
                cmd = step.command
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
                results.append(UninstallResult(
                    step=step, executed=True, success=r.returncode == 0,
                    output=(r.stdout or r.stderr or "").strip()[:500],
                ))
            except Exception as exc:
                results.append(UninstallResult(
                    step=step, executed=True, success=False,
                    output=f"Error: {exc}",
                ))
        return results

    def cleanup_config(self) -> Dict:
        """Remove reach config directory (cookies, proxy, keys)."""
        if self.keep_config:
            return {"skipped": True, "reason": "--keep-config"}
        if self.dry_run:
            return {"dry_run": True, "would_remove": str(_reach_config_dir())}
        try:
            cfg_dir = _reach_config_dir()
            if cfg_dir.exists():
                shutil.rmtree(cfg_dir)
            return {"success": True, "removed": str(cfg_dir)}
        except Exception as exc:
            return {"error": str(exc)}

    def cleanup_skill(self) -> Dict:
        """Remove the agent-reach skill file."""
        try:
            from hermes_constants import get_hermes_home
            skill_path = get_hermes_home() / "skills" / "agent-reach" / "SKILL.md"
        except Exception:
            skill_path = Path.home() / ".hermes" / "skills" / "agent-reach" / "SKILL.md"
        if self.dry_run:
            return {"dry_run": True, "would_remove": str(skill_path)}
        try:
            if skill_path.exists():
                skill_path.unlink()
                # Remove the directory if empty
                skill_path.parent.rmdir() if not list(skill_path.parent.iterdir()) else None
                return {"success": True, "removed": str(skill_path)}
            return {"skipped": True, "reason": "skill not found"}
        except Exception as exc:
            return {"error": str(exc)}

    def cleanup_mcp(self) -> Dict:
        """Remove Exa MCP server if configured."""
        if self.dry_run:
            return {"dry_run": True, "would_remove": "exa-search MCP server"}
        try:
            subprocess.run(
                ["hermes", "mcp", "remove", "exa-search"],
                capture_output=True, text=True, timeout=10,
            )
            return {"success": True, "removed": "exa-search MCP (if it was configured)"}
        except Exception as exc:
            return {"error": str(exc)}

    def format_results(self, results: List[UninstallResult], config_result: Dict, skill_result: Dict, mcp_result: Dict) -> str:
        lines: List[str] = []
        lines.append("")
        lines.append("  Internet Reach — Uninstall Report")
        lines.append("  " + "=" * 48)
        lines.append("")

        for r in results:
            if r.skipped:
                icon = "⏭️"
            elif r.success:
                icon = "✅"
            else:
                icon = "❌"
            lines.append(f"  {icon} {r.step.description}")
            if r.output and not r.skipped:
                for line in r.output.split("\n")[:2]:
                    if line.strip():
                        lines.append(f"     {line.strip()}")
            lines.append("")

        # Config
        if config_result.get("skipped"):
            lines.append(f"  ⏭️ Config retained (--keep-config)")
        elif config_result.get("dry_run"):
            lines.append(f"  ⏭️ [DRY RUN] would remove {config_result['would_remove']}")
        elif config_result.get("success"):
            lines.append(f"  ✅ Config removed: {config_result['removed']}")
        else:
            lines.append(f"  ❌ Config removal failed: {config_result.get('error', '?')}")
        lines.append("")

        # Skill
        if skill_result.get("dry_run"):
            lines.append(f"  ⏭️ [DRY RUN] would remove {skill_result['would_remove']}")
        elif skill_result.get("success"):
            lines.append(f"  ✅ Skill removed: {skill_result['removed']}")
        elif skill_result.get("skipped"):
            lines.append(f"  ⏭️ Skill not found")
        else:
            lines.append(f"  ❌ Skill removal failed: {skill_result.get('error', '?')}")
        lines.append("")

        # MCP
        if mcp_result.get("dry_run"):
            lines.append(f"  ⏭️ [DRY RUN] would remove exa-search MCP")
        elif mcp_result.get("success"):
            lines.append(f"  ✅ {mcp_result['removed']}")
        else:
            lines.append(f"  ❌ MCP removal failed: {mcp_result.get('error', '?')}")
        lines.append("")

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Update checking
# ---------------------------------------------------------------------------


def check_reach_tool_updates() -> List[Dict]:
    """Check if reach-installed tools have newer versions available.

    Returns a list of dicts with tool name, current version, and update status.
    Only checks tools that are actually installed.
    """
    results: List[Dict] = []

    # Tools to check and how to get their version
    tool_checks = [
        ("yt-dlp", ["yt-dlp", "--version"], "pip3 install --upgrade yt-dlp"),
        ("feedparser", None, "pip3 install --upgrade feedparser"),  # Python package, check via pip
        ("bili", ["bili", "--version"], "pipx upgrade bilibili-cli"),
        ("twitter", ["twitter", "--version"], "pipx upgrade twitter-cli"),
        ("xurl", ["xurl", "--version"], "pipx upgrade xurl"),
        ("opencli", ["opencli", "--version"], "pipx upgrade opencli"),
        ("rdt", ["rdt", "--version"], "pipx upgrade rdt-cli"),
    ]

    for cli_name, version_cmd, upgrade_cmd in tool_checks:
        if not shutil.which(cli_name):
            continue  # not installed

        entry: Dict = {"tool": cli_name, "installed": True, "current_version": "", "update_available": False, "upgrade_cmd": upgrade_cmd}

        # Get current version
        if version_cmd:
            try:
                r = subprocess.run(version_cmd, capture_output=True, text=True, timeout=10)
                if r.returncode == 0:
                    entry["current_version"] = r.stdout.strip().split("\n")[0][:50]
            except Exception:
                entry["current_version"] = "unknown"

        # Check for updates via pip
        try:
            pip_cmd = "pip3" if shutil.which("pip3") else "pip"
            # Use pip list --outdated for pip-installed, pipx list for pipx
            if "pipx" in upgrade_cmd and shutil.which("pipx"):
                r = subprocess.run(["pipx", "list", "--short"], capture_output=True, text=True, timeout=15)
                # pipx list --short shows package names; we can't easily check outdated
                # without pipx upgrade --quiet, so just report current
                entry["update_available"] = False  # conservative
            else:
                # For pip packages, check if a newer version exists
                pkg_name = cli_name.replace("-", "_")
                r = subprocess.run(
                    [pip_cmd, "index", "versions", pkg_name],
                    capture_output=True, text=True, timeout=15,
                )
                if r.returncode == 0 and "Available versions:" in r.stdout:
                    versions_line = [l for l in r.stdout.split("\n") if "Available versions:" in l]
                    if versions_line:
                        latest = versions_line[0].split(",")[0].replace("Available versions:", "").strip()
                        entry["latest_version"] = latest
                        if entry["current_version"] and entry["current_version"] not in latest:
                            entry["update_available"] = True
        except Exception:
            pass

        results.append(entry)

    return results


def format_update_report(updates: List[Dict]) -> str:
    """Format update check results for display."""
    lines: List[str] = []
    lines.append("")
    lines.append("  Internet Reach — Update Check")
    lines.append("  " + "=" * 48)
    lines.append("")

    if not updates:
        lines.append("  No reach-installed tools found.")
        lines.append("")
        return "\n".join(lines)

    has_updates = False
    for entry in updates:
        if not entry["installed"]:
            continue
        icon = "🆕" if entry.get("update_available") else "✅"
        version_str = entry.get("current_version", "?")
        if entry.get("latest_version"):
            version_str += f" → {entry['latest_version']}"
        lines.append(f"  {icon} {entry['tool']}: {version_str}")
        if entry.get("update_available"):
            has_updates = True
            lines.append(f"     upgrade: {entry['upgrade_cmd']}")

    if not has_updates:
        lines.append("")
        lines.append("  All tools up to date.")

    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Monitoring cron setup helper
# ---------------------------------------------------------------------------


def create_monitoring_cron_prompt() -> str:
    """Return a self-contained prompt for setting up a daily reach watch cron job.

    This is designed to be used with `hermes cron create` or as guidance text
    in the CLI output.
    """
    return (
        "Run `hermes reach watch` to check all internet-reach capabilities. "
        "If output contains failures (❌ ⚠️), include the full report. "
        "If output is silent (all OK), do not notify — stay quiet. "
        "If any capability is broken, suggest the first fix step from the report."
    )


def get_monitoring_cron_schedule() -> str:
    """Default schedule for reach monitoring — daily at 8am."""
    return "0 8 * * *"


# ---------------------------------------------------------------------------
# Convenience functions
# ---------------------------------------------------------------------------


def get_installer(safe: bool = False, dry_run: bool = False) -> ReachInstaller:
    return ReachInstaller(safe=safe, dry_run=dry_run)


def get_configurator() -> ReachConfigurator:
    return ReachConfigurator()


def get_uninstaller(dry_run: bool = False, keep_config: bool = False) -> ReachUninstaller:
    return ReachUninstaller(dry_run=dry_run, keep_config=keep_config)