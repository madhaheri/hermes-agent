#!/usr/bin/env python3
"""Native RSS/Atom feed reader tool.

Registers a ``read_rss`` tool in the ``web`` toolset. Uses ``feedparser`` to
parse RSS/Atom feeds and return structured JSON. Auto-discovered via the
module-level ``registry.register()`` call. ``feedparser`` is a lazy
dependency — ``check_fn`` probes for it so the tool is only advertised to the
model when the package is importable.
"""

from __future__ import annotations

import json
import logging
from urllib.parse import urlparse

from tools.registry import registry

logger = logging.getLogger(__name__)


def _check_feedparser() -> bool:
    """Return True when the ``feedparser`` package is importable."""
    try:
        import feedparser  # noqa: F401
    except ImportError:
        return False
    return True


def _read_rss(url: str, limit: int = 10) -> str:
    """Parse an RSS/Atom feed and return a JSON string with feed metadata and items."""
    if not url or not isinstance(url, str):
        return json.dumps({"error": "A non-empty 'url' string is required."})

    parsed_url = urlparse(url.strip())
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        return json.dumps(
            {"error": f"Invalid URL (must be http/https): {url!r}"},
            ensure_ascii=False,
        )

    if not isinstance(limit, int) or limit < 1:
        limit = 10

    # Lazy import so check_fn stays authoritative.
    try:
        import feedparser
    except ImportError:
        return json.dumps(
            {"error": "feedparser is not installed. Run: pip install feedparser"},
            ensure_ascii=False,
        )

    try:
        feed = feedparser.parse(url)
    except Exception as exc:
        logger.warning("read_rss: feedparser.parse raised for %s: %s", url, exc)
        return json.dumps(
            {"error": f"Failed to parse feed: {type(exc).__name__}: {exc}"},
            ensure_ascii=False,
        )

    # feedparser swallows network/parse errors silently and populates
    # ``bozo`` + ``bozo_exception`` instead of raising.
    if feed.bozo and not feed.entries:
        exc = getattr(feed, "bozo_exception", None)
        detail = f"{type(exc).__name__}: {exc}" if exc else "unknown parse error"
        return json.dumps(
            {"error": f"Feed parse error for {url}: {detail}"},
            ensure_ascii=False,
        )

    if not feed.entries:
        return json.dumps({"error": f"No feed entries found at {url}"}, ensure_ascii=False)

    feed_meta = getattr(feed, "feed", {})
    items = []
    for entry in feed.entries[:limit]:
        items.append({
            "title": entry.get("title", ""),
            "link": entry.get("link", ""),
            "summary": entry.get("summary") or entry.get("description") or "",
            "published": entry.get("published") or entry.get("updated") or "",
        })

    result = {
        "url": url,
        "feed_title": feed_meta.get("title", ""),
        "feed_description": feed_meta.get("description", ""),
        "item_count": len(items),
        "items": items,
    }
    return json.dumps(result, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Registration — top-level call enables auto-discovery by the registry.
# ---------------------------------------------------------------------------

registry.register(
    name="read_rss",
    toolset="web",
    schema={
        "name": "read_rss",
        "description": (
            "Parse an RSS or Atom feed URL and return the feed title, "
            "description, and the most recent items (title, link, summary, "
            "published date). Useful for monitoring blogs, news sites, and "
            "any source with an RSS/Atom feed. Requires the 'feedparser' "
            "Python package."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": (
                        "The RSS or Atom feed URL to parse "
                        "(e.g. 'https://hnrss.org/frontpage')."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of feed items to return (default 10).",
                },
            },
            "required": ["url"],
        },
    },
    handler=lambda args, **kw: _read_rss(
        url=args.get("url", ""),
        limit=args.get("limit", 10) or 10,
    ),
    check_fn=_check_feedparser,
    requires_env=[],
)