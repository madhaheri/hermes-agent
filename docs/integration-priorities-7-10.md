# Integration priorities 7–10 audit

This audit records what can be reviewed safely without installing services,
writing credentials, changing a Hermes profile, creating cron jobs, or sending
messages. It was performed against the repository interfaces on 2026-09-07.

## 7. Cloud-only speech to text

Status: **admitted through the existing Nous path; no new provider required**.

Hermes already has a provider-neutral Python interface in
`agent/transcription_provider.py`: a provider accepts a validated audio path plus
optional model and language hints, and returns the standard `success`,
`transcript`, `provider`, and optional `error` envelope. Built-in dispatch is in
`tools/transcription_tools.py`.

The allowed authenticated route is already implemented. A stored
`stt.provider: nous` selection is normalized to the OpenAI-shaped audio handler;
`tools.transcription_cloud._resolve_openai_audio_client_config()` then resolves
only the Nous `openai-audio` Tool Gateway token and URL. It deliberately does
not fall back to a direct OpenAI key. Missing entitlement or an unreachable
gateway produces an error rather than switching to local inference.

For a cloud-only deployment, the eventual operator action is:

```yaml
stt:
  provider: nous
```

That configuration is not applied by this change. Direct Gemini, OpenRouter,
Ollama, local Whisper, command providers, and local workers were not added or
used. Existing coverage in `tests/tools/test_transcription_tools.py` verifies
managed resolution and fail-closed behavior.

## 8. Selective MCP readiness

Status: **blocked on external prerequisites; no server is configured or
started**.

Hermes' current `mcp_servers` schema supports disabled entries, HTTP or stdio
transport, secret references, exact/glob tool allowlists, disabled resources and
prompts, and `trust: untrusted`. The safe admission shape for either requested
server is therefore:

```yaml
mcp_servers:
  server_name:
    enabled: false
    url: "REPLACE_WITH_VERIFIED_HTTPS_MCP_ENDPOINT"
    headers:
      Authorization: "Bearer ${MCP_SERVER_API_KEY}"
    trust: untrusted
    tools:
      include: []
      resources: false
      prompts: false
```

An empty `include` list is intentionally deny-all. Enabling a server is unsafe
until its endpoint, authentication method, and exact read-only tool names have
been independently verified.

### GitHub

The repository explicitly excludes GitHub from the optional MCP catalog because
the hosted service requires a per-host OAuth application and rejects generic
dynamic client registration. The bundled GitHub skill is the supported current
route. Prerequisites for MCP admission are a verified endpoint, a registered
OAuth client (or separately approved least-privilege token), and a reviewed
allowlist of read-only repository/issue tools.

### Mission Control

No Mission Control MCP endpoint, authentication contract, or tool names exist
in the repository. References to Mission Control describe a reverse-proxy/UI
deployment, not an MCP server. Prerequisites are an owner-supplied HTTPS endpoint,
authentication contract, server tool discovery output, and classification of
each tool as read-only or mutating. Until then there is no grounded config to
add.

## 9. Scheduled Telegram reports

Status: **live and verified**.

The live schedule is recorded in
`docs/examples/scheduled-telegram-reports.live.json`. The three jobs are active
in `~/.hermes/cron/jobs.json`, deliver to the configured Telegram origin, and use
read-only collection with mandatory redaction:

- Infrastructure health: daily at 08:00 (`0 8 * * *`), job `7c1298574849`.
- Model performance: Mondays at 09:00 (`0 9 * * 1`), job `60264cc3f2e1`.
- Alert scan: every five minutes (`*/5 * * * *`), job `7b6ef6d36535`, silent when
  no threshold is crossed.

The delivery database confirms completed report executions are marked
`delivered`. The original `scheduled-telegram-reports.dry-run.json` remains as a
non-delivery test fixture; it is not the live source of scheduling.

## 10. Skill curation

The library contains 58 active-by-default bundled skills and 137 shipped-but-
inactive optional skills (195 total) at audit time. Exact frontmatter names and
descriptions have no duplicates.
Directory/name uniqueness is the stable deduplication key; topical similarity
alone is not sufficient to merge skills because several adjacent skills have
different prerequisites and procedures.

The mechanical metadata baseline is healthy: all skills pass the existing
required-field, name/path, description, related-skill, path-portability, and
size checks. The installed library contained 1,429 `SKILL.md` files; 1,290
lacked `## Verification` and 1,321 lacked `## Pitfalls`.

All 1,429 files now contain both sections. The modernization was performed by
`~/.hermes/scripts/modernize_skill_sections.py` after creating a compressed
backup under `~/.hermes/backups/`. The inserted guidance is deliberately
conservative: prerequisites, observed outputs, reversibility, backup handling,
and explicit treatment of unavailable checks. It does not delete skills or
change frontmatter, procedures, credentials, or routing.

The validator now enforces global uniqueness of canonical frontmatter names and
reports every colliding path. This prevents a new bundled/optional overlap from
being silently collapsed by name resolution without freezing the catalog size.
