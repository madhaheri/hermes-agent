---
name: verified-coding-delegation
description: Delegate coding through evidence-based gates.
version: 1.0.0
author: Workspace Maintainers and Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [Coding, Delegation, Verification]
    category: autonomous-ai-agents
    related_skills: [claude-code, codex, pair-capability-router]
---

# Verified Coding Delegation Skill

Delegate bounded coding work to first-party Claude Code CLI or Codex CLI and
accept it only after independent evidence gates. OpenRouter and local inference
are prohibited by this workflow.

## When to Use

Use for reviewable feature, fix, refactor, test, or code-review tasks where a
coding CLI is installed and the repository supplies executable acceptance checks.

## Prerequisites

- Read `AGENTS.md`, repository instructions, and
  `docs/verified-coding-delegation.md` completely with `read_file`.
- Confirm a clean or understood baseline and non-overlapping user changes.
- Use `terminal` to read the installed CLI `--help`; use only listed flags.

## How to Run

Choose the model from the specification's capability table. Launch one bounded,
non-interactive first-party CLI run with structured output and capture stdout,
stderr, exit code, duration, requested/observed model, and reported usage.

## Quick Reference

Reviews are read-only. Implementations write only inside the repository. Claude
requires turn and dollar caps; Codex requires an external timeout. Neither may
use permission bypass, extra writable directories, web search, MCP, OpenRouter,
`--oss`, or local providers.

## Procedure

1. Write objective, non-goals, allowed paths, prohibitions, and exact gates.
2. Audit context with 25% growth and output reserve; launch only at 70% or less.
3. Capture native structured output and normalize missing metrics as `null`.
4. Classify any failure using the specification; retry at most once when allowed.
5. Independently inspect status/diff and run repository-prescribed tests.
6. Commit, push, restart, configure, or schedule only under separate authority.

## Safety and Read-Back

Read back repository root, model, budget, timeout, writable paths, baseline, and
prohibited operations before launch. Abort on overlap, scope drift, unexpected
untracked files, malformed telemetry, or credential/config exposure.

## Pitfalls

Model prose is not proof. A zero exit without parseable capture is a protocol
failure. Missing token data is unknown, not zero; agreement between agents does
not replace tests.

## Verification

Require structured completion, admitted diff only, `git diff --check`, targeted
tests run by the orchestrator, review of generated/untracked files, and complete
terminal/modelUsage capture. Mark passed only when every gate succeeds.
