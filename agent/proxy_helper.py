"""Proxy configuration helper.

Reads ``proxy.url`` from config.yaml and provides env vars for subprocess
calls. Used by terminal, execute_code, and reach tools to inject
HTTP_PROXY/HTTPS_PROXY when the user has configured a proxy.
"""

from __future__ import annotations

import os
from typing import Dict


def load_proxy_config() -> Dict[str, str]:
    """Read proxy config and return env vars to inject.

    Returns a dict suitable for ``os.environ.update()`` or
    ``subprocess(..., env={**os.environ, **proxy_env})``.

    Reads ``proxy.url`` and ``proxy.apply_to_all`` from config.yaml.
    Also checks the reach-config store (set by ``hermes reach configure proxy``).
    """
    env: Dict[str, str] = {}

    # 1. Check config.yaml proxy.url
    try:
        from hermes_cli.config import load_config

        cfg = load_config()
        proxy_cfg = cfg.get("proxy", {}) or {}
        url = proxy_cfg.get("url", "").strip()
        apply_all = proxy_cfg.get("apply_to_all", False)
    except Exception:
        url = ""
        apply_all = False

    # 2. Fall back to reach-config store
    if not url:
        try:
            from agent.reach_installer import _load_config

            reach_cfg = _load_config()
            url = reach_cfg.get("proxy", "").strip()
        except Exception:
            pass

    # 3. Fall back to existing env vars (may have been set manually)
    if not url:
        if os.getenv("HTTP_PROXY"):
            url = os.getenv("HTTP_PROXY")
        elif os.getenv("http_proxy"):
            url = os.getenv("http_proxy")

    if url:
        env["HTTP_PROXY"] = url
        env["HTTPS_PROXY"] = url
        env["http_proxy"] = url
        env["https_proxy"] = url
        if apply_all:
            env["ALL_PROXY"] = url
            env["all_proxy"] = url

    return env


def inject_proxy_env(target_env: Dict[str, str] | None = None) -> Dict[str, str]:
    """Inject proxy env vars into a target env dict (or os.environ if None).

    Returns the updated dict. Does NOT mutate os.environ when a target is given.
    """
    proxy_env = load_proxy_config()
    if target_env is None:
        # Mutate os.environ in place
        os.environ.update(proxy_env)
        return dict(os.environ)
    else:
        target_env.update(proxy_env)
        return target_env