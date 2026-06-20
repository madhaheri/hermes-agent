"""``hermes reach`` subcommand parser.

Provides the ``hermes reach`` command group:
  * ``hermes reach doctor``  — probe all internet-reach capabilities
  * ``hermes reach doctor <cap>`` — probe a single capability
  * ``hermes reach list``    — list all capabilities and their backends
  * ``hermes reach resolve <cap>`` — show which backend would be used
  * ``hermes reach watch``   — quick health check (for cron jobs)

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