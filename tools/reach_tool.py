#!/usr/bin/env python3
"""Internet-reach capability doctor tool.

In-session tool that lets the agent probe which internet-reach capabilities are
available before starting a research task. This prevents the agent from
hallucinating "I checked Reddit/Twitter" when the access path is actually broken.

Two tools are registered:

* ``reach_doctor`` — probe all (or one) capabilities and return structured status
* ``reach_resolve`` — resolve a capability to its best available backend

Both are in the ``reach`` toolset, which is off by default (not in
``_HERMES_CORE_TOOLS``). Enable with ``hermes tools enable reach``.

Inspired by Agent-Reach (Panniantong/Agent-Reach) — adapted as a native Hermes
capability registry with ordered backend fallbacks and repair prescriptions.
"""

from __future__ import annotations

import json
import logging

from tools.registry import registry

logger = logging.getLogger(__name__)


def _reach_doctor(capability: str | None = None, task_id: str | None = None) -> str:
    """Probe internet-reach capabilities and return structured status.

    Args:
        capability: Optional capability name to probe (e.g. "twitter.search").
            If omitted, probes all capabilities.
        task_id: Internal task tracking ID.

    Returns:
        JSON string with capability status, available backends, and fix steps.
    """
    from agent.reach_capabilities import get_reach_registry

    registry_instance = get_reach_registry()
    results = registry_instance.doctor(capability)

    if not results and capability:
        return json.dumps(
            {"error": f"Unknown capability: {capability}", "available": []},
            ensure_ascii=False,
        )

    available_caps = [r for r in results if r.get("best")]
    unavailable_caps = [r for r in results if not r.get("best")]

    return json.dumps(
        {
            "total": len(results),
            "available": len(available_caps),
            "unavailable": len(unavailable_caps),
            "capabilities": results,
            "summary": f"{len(available_caps)}/{len(results)} capabilities available",
        },
        ensure_ascii=False,
        indent=2,
    )


def _reach_resolve(capability: str, task_id: str | None = None) -> str:
    """Resolve a capability to its best available backend.

    Args:
        capability: Capability name (e.g. "twitter.search", "youtube.transcript").
        task_id: Internal task tracking ID.

    Returns:
        JSON string with the resolved backend name, description, and fallback list.
    """
    from agent.reach_capabilities import get_reach_registry

    registry_instance = get_reach_registry()
    cap = registry_instance.get(capability)
    if cap is None:
        return json.dumps(
            {"error": f"Unknown capability: {capability}"},
            ensure_ascii=False,
        )

    backend = registry_instance.resolve(capability)
    all_results = cap.probe_all()

    return json.dumps(
        {
            "capability": capability,
            "description": cap.description,
            "resolved_backend": backend.name if backend else None,
            "resolved_description": backend.description if backend else None,
            "all_backends": [
                {
                    "name": r.backend_name,
                    "available": r.available,
                    "status": r.status,
                    "detail": r.detail,
                    "fix": r.fix,
                    "needs_user": r.needs_user,
                }
                for r in all_results
            ],
        },
        ensure_ascii=False,
        indent=2,
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

registry.register(
    name="reach_doctor",
    toolset="reach",
    schema={
        "name": "reach_doctor",
        "description": (
            "Probe which internet-reach capabilities (Twitter, Reddit, YouTube, "
            "GitHub, RSS, web, Bilibili, XiaoHongShu, LinkedIn, etc.) are currently "
            "available. Run this BEFORE starting a research task that requires "
            "social media or platform access to verify which sources you can "
            "actually reach. Returns structured status with repair steps for "
            "broken backends. Optionally probe a single capability by name."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "capability": {
                    "type": "string",
                    "description": (
                        "Optional: probe only this capability "
                        "(e.g. 'twitter.search', 'youtube.transcript', 'reddit.read'). "
                        "If omitted, probes all capabilities."
                    ),
                },
            },
            "required": [],
        },
    },
    handler=lambda args, **kw: _reach_doctor(
        capability=args.get("capability"),
        task_id=kw.get("task_id"),
    ),
    check_fn=lambda: True,  # always available — it's a diagnostic tool
    requires_env=[],
)

registry.register(
    name="reach_resolve",
    toolset="reach",
    schema={
        "name": "reach_resolve",
        "description": (
            "Resolve a capability name to its best available backend. "
            "Use this to find out which tool/CLI the agent should use for a "
            "specific task (e.g. 'twitter.search' → 'x_search_tool' or 'xurl_cli'). "
            "Returns the resolved backend name, description, and all fallback backends "
            "with their status."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "capability": {
                    "type": "string",
                    "description": (
                        "Capability name to resolve, e.g. 'twitter.search', "
                        "'youtube.transcript', 'reddit.read', 'github.read', "
                        "'web.read', 'rss.read'."
                    ),
                },
            },
            "required": ["capability"],
        },
    },
    handler=lambda args, **kw: _reach_resolve(
        capability=args.get("capability", ""),
        task_id=kw.get("task_id"),
    ),
    check_fn=lambda: True,
    requires_env=[],
)