# Verified coding delegation

This specification governs non-interactive coding work delegated from Hermes to
Claude Code CLI or Codex CLI. It is an orchestration contract, not a provider
configuration guide. Delegates use only their first-party authenticated routes:
Anthropic for Claude Code and OpenAI for Codex. OpenRouter, `--oss`, local
providers, Ollama, and other inference proxies are out of scope.

## Admission and routing

Before launch, record the repository root, clean/dirty baseline, requested files,
acceptance tests, time/turn/cost limits, and allowed side effects. Refuse a run
whose worktree contains overlapping user changes or whose success criteria cannot
be tested.

| Model brief | Route when | Required brief emphasis |
|---|---|---|
| Claude Opus | Cross-cutting design, subtle debugging, security review | Invariants, alternatives rejected, smallest safe patch |
| Claude Sonnet | Normal feature or bug-fix implementation | Exact scope, tests, repository conventions |
| Claude Haiku | Bounded mechanical edit or fast read-only triage | Enumerated files, no scope expansion, short result |
| Claude Fable | Very large-context audit across many artifacts | Evidence index, context partitioning, no speculative edits |
| Codex Sol | Difficult implementation or reasoning-heavy review | Reproduction, root cause, complete verification |
| Codex Terra | Routine implementation, focused refactor, test repair | Narrow diff, existing patterns, targeted tests |

Model names are resolved by the installed first-party CLI. Confirm availability
with its help/model UI; do not silently substitute a provider or local model.

Every task brief must include: objective; non-goals; repository root; permitted
paths; prohibited operations; relevant `AGENTS.md`/project instructions; expected
artifacts; exact acceptance commands; and the structured completion schema. Ask
the model to report uncertainty rather than invent missing commands or facts.

## Context-budget audit

Estimate inputs before launch: project instructions, supplied task text, selected
source/test files, expected tool output, and output reserve. Record the model's
reported context window when available. Admit only when estimated input plus a
25% tool-growth allowance and output reserve is at most 70% of the window. At
70–85%, reduce supplied material or split the task. Above 85%, do not launch.
Never claim exact token usage when a CLI does not report it.

After completion, compare reported input/cache/output usage with the estimate.
Flag missing usage as `telemetry_missing`, not zero. For Claude JSON results,
preserve `usage`, `modelUsage`, `total_cost_usd`, `num_turns`, and `session_id`.
For Codex JSONL, preserve all raw events plus derived totals only from fields
actually present.

## Bounded execution

Run in the repository root with no additional writable directory. Preserve the
baseline and never enable network search, MCP servers, plugins, local inference,
or provider overrides unless the task explicitly requires and authorizes them.

Claude Code uses print mode, JSON output, a turn and dollar cap, no session
persistence, no permission prompts, and a narrow tool allowlist. A review starts
read-only with `--restricted --permission-mode plan`; an implementation may use
`--permission-mode acceptEdits` with only the file and command tools needed.
Never use `--dangerously-skip-permissions`.

```text
claude -p --model <model> --output-format json --max-turns <n> \
  --max-budget-usd <usd> --no-session-persistence \
  --permission-prompts none --allowedTools <allowlist> '<brief>'
```

Codex uses a repository root, an explicit model, ephemeral state, JSONL output,
and the narrowest sandbox. Reviews use `read-only`; implementations use
`workspace-write`. Never use `danger-full-access`,
`--dangerously-bypass-approvals-and-sandbox`, `--add-dir`, `--oss`, or
`--local-provider`.

```text
codex exec -C <repo> --model <model> --sandbox <read-only|workspace-write> \
  --ephemeral --json '<brief>'
```

The orchestrator imposes an external wall-clock timeout because Codex `exec`
does not expose a turn or cost cap. Capture stdout and stderr separately and
retain the process exit code. Secrets and raw prompts must not be copied into
summary artifacts.

## Capture contract

Store one JSON record per run, alongside the raw CLI output, using this shape:

```json
{
  "schemaVersion": 1,
  "runner": "claude|codex",
  "modelRequested": "string",
  "modelObserved": "string|null",
  "startedAt": "RFC3339",
  "durationMs": 0,
  "exitCode": 0,
  "terminal": {"stdoutPath": "string", "stderrPath": "string"},
  "modelUsage": {"source": "reported|derived|missing", "inputTokens": null, "cachedInputTokens": null, "outputTokens": null, "costUSD": null},
  "status": "passed|failed",
  "failureClass": null,
  "verification": [{"command": "string", "exitCode": 0, "resultPath": "string"}]
}
```

Use `null` for unavailable metrics. Claude's native `modelUsage` object is
retained unchanged in the raw result; the normalized object above is additive.
Codex event types and token fields are version-dependent, so parsers must ignore
unknown events and must not manufacture totals.

## Failure classification

Assign exactly one primary class: `admission_failed`, `auth`, `permission`,
`context_budget`, `rate_limit`, `timeout`, `cli_protocol`, `delegate_error`,
`verification_failed`, `scope_violation`, or `telemetry_missing`. Preserve the
raw evidence and do not retry permission, scope, or verification failures.
Retry auth/rate-limit/transient protocol failures at most once, within the
original budget, and only after a read-only diagnosis. A model-written claim of
success never overrides a nonzero exit, malformed capture, or failed gate.

## Verification gates

Acceptance requires all gates:

1. CLI exit and protocol status indicate completion; structured output parses.
2. `git status --short` and `git diff --check` show only admitted paths and no
   whitespace errors; no unexpected untracked or generated files exist.
3. The orchestrator, not the delegate, runs the repository-prescribed targeted
   tests and any required lint/type checks from the task brief.
4. Diff review confirms the stated root cause, no disabled tests, no secret or
   credential material, and no live config/service/scheduler changes.
5. Usage and terminal capture are present, or the run fails as
   `telemetry_missing`.

Only then mark `passed`. Committing, pushing, restarting services, changing live
configuration, or scheduling work requires separate explicit authorization.
