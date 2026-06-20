"""``hermes reach`` subcommand parser.

Provides the ``hermes reach`` command group:
  * ``hermes reach doctor``  — probe all internet-reach capabilities
  * ``hermes reach doctor <cap>`` — probe a single capability
  * ``hermes reach doctor --functional`` — actually test with real queries
  * ``hermes reach list``    — list all capabilities and their backends
  * ``hermes reach resolve <cap>`` — show which backend would be used
  * ``hermes reach watch``   — quick health check (for cron jobs)
  * ``hermes reach setup``   — install upstream tools for reach capabilities
  * ``hermes reach configure`` — manage credentials (cookies, proxy, keys)

Handler is injected from main.py to avoid import cycles.
"""

from __future__ import annotations

from typing import Callable


def build_reach_parser(subparsers, *, cmd_reach: Callable) -> None:
    """Attach the ``reach`` subcommand to ``subparsers``."""
    reach_parser = subparsers.add_parser(
        "reach",
        help="Internet-reach capability doctor — check which platforms you can access",
        description=(
            "The reach capability layer probes all configured internet-reach "
            "backends (Twitter, Reddit, YouTube, GitHub, RSS, web, Bilibili, "
            "XiaoHongShu, LinkedIn, etc.) and shows which are ready, which are "
            "broken, and how to fix them.\n\n"
            "Inspired by Agent-Reach (Panniantong/Agent-Reach) — adapted as a "
            "native Hermes capability registry with ordered backend fallbacks."
        ),
    )
    reach_sub = reach_parser.add_subparsers(dest="reach_action")

    # doctor
    doctor_p = reach_sub.add_parser(
        "doctor",
        help="Probe all capabilities and show status",
        description="Run real probes against every backend and report status.",
    )
    doctor_p.add_argument(
        "capability",
        nargs="?",
        default=None,
        help="Optional: probe only this capability (e.g. twitter.search)",
    )
    doctor_p.add_argument(
        "--json",
        action="store_true",
        help="Output raw JSON instead of formatted text",
    )
    doctor_p.add_argument(
        "--functional",
        action="store_true",
        help="Run functional probes (actually test with real queries — slower, makes network calls)",
    )
    doctor_p.set_defaults(func=cmd_reach)

    # list
    list_p = reach_sub.add_parser(
        "list",
        help="List all capabilities and their backends",
        description="Show all registered capabilities without probing.",
    )
    list_p.set_defaults(func=cmd_reach)

    # resolve
    resolve_p = reach_sub.add_parser(
        "resolve",
        help="Show which backend would be used for a capability",
        description="Resolve a capability to its best available backend.",
    )
    resolve_p.add_argument(
        "capability",
        help="Capability name (e.g. twitter.search, youtube.transcript)",
    )
    resolve_p.set_defaults(func=cmd_reach)

    # watch (quick health for cron)
    watch_p = reach_sub.add_parser(
        "watch",
        help="Quick health check — silent if all OK, report if not",
        description=(
            "Minimal output for scheduled monitoring. Prints nothing if all "
            "probed capabilities are healthy; prints failures if any are down. "
            "Suitable for cron jobs."
        ),
    )
    watch_p.set_defaults(func=cmd_reach)

    # setup (install upstream tools)
    setup_p = reach_sub.add_parser(
        "setup",
        help="Install upstream tools for reach capabilities",
        description=(
            "Install upstream CLIs and packages (yt-dlp, feedparser, gh, "
            "bili-cli, twitter-cli, rdt-cli, OpenCLI, etc.) so the agent "
            "can access more platforms. Uses pipx or pip. Safe mode and "
            "dry-run available."
        ),
    )
    setup_p.add_argument(
        "channels",
        nargs="*",
        default=None,
        help="Channels to install (e.g. youtube twitter reddit). Default: zero-config channels only.",
    )
    setup_p.add_argument(
        "--all",
        action="store_true",
        help="Install all channels (zero-config + optional)",
    )
    setup_p.add_argument(
        "--safe",
        action="store_true",
        help="Safe mode: no auto system changes, only report what would be done",
    )
    setup_p.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview all operations without executing anything",
    )
    setup_p.set_defaults(func=cmd_reach)

    # configure (manage credentials)
    config_p = reach_sub.add_parser(
        "configure",
        help="Manage credentials: cookies, proxy, API keys",
        description=(
            "Store and manage credentials for reach backends. "
            "Supported: twitter-cookies, reddit-cookies, xhs-cookies, "
            "xueqiu-cookies, proxy, groq-key, exa-key, from-browser."
        ),
    )
    config_p.add_argument(
        "key",
        nargs="?",
        default=None,
        help="Configuration key (e.g. twitter-cookies, proxy, groq-key, from-browser)",
    )
    config_p.add_argument(
        "value",
        nargs="?",
        default=None,
        help="Value to set (for from-browser: browser name like 'chrome')",
    )
    config_p.add_argument(
        "--list",
        action="store_true",
        help="List current configuration (secrets masked)",
    )
    config_p.add_argument(
        "--remove",
        action="store_true",
        help="Remove a configuration key",
    )
    config_p.add_argument(
        "--clear-all",
        action="store_true",
        help="Clear all stored credentials",
    )
    config_p.set_defaults(func=cmd_reach)