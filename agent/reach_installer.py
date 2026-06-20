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
# Convenience functions
# ---------------------------------------------------------------------------


def get_installer(safe: bool = False, dry_run: bool = False) -> ReachInstaller:
    return ReachInstaller(safe=safe, dry_run=dry_run)


def get_configurator() -> ReachConfigurator:
    return ReachConfigurator()