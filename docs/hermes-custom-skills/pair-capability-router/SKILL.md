---
name: pair-capability-router
description: Route paired agents by task capability.
version: 1.0.0
author: Workspace Maintainers and Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [Delegation, Routing, Verification]
    category: autonomous-ai-agents
    related_skills: [verified-coding-delegation]
---

# Pair Capability Router Skill

Assign a producer and verifier according to task risk and model strengths. It
does not grant tools, expand scope, or treat model agreement as verification.

## When to Use

Use when two independent agents can reduce risk on coding, analysis, migration
planning, or benchmark interpretation.

## Prerequisites

- A bounded objective, acceptance checks, context budget, and permission envelope.
- Two independently identified roles; the verifier must receive evidence, not the
  producer's hidden reasoning.

## How to Run

Use `delegate_task` for isolated Hermes subagents or `terminal` for an explicitly
approved first-party coding CLI. Give each role its own brief and output contract.

## Quick Reference

Route architecture/security to the strongest reasoning model, routine edits to a
balanced coding model, and mechanical triage to a fast model. Escalate rather than
guess when capability or availability is unknown.

## Procedure

1. Classify impact, reversibility, breadth, and verification cost.
2. Select producer and verifier; record why each fits.
3. Give the producer scope and gates, then freeze its artifacts.
4. Give the verifier baseline, diff, logs, and acceptance criteria independently.
5. Accept only evidence-backed concurrence; resolve disagreements with a targeted
   test or user decision, never a majority vote.

## Safety and Read-Back

Tool permissions remain task-scoped and are never inherited merely because a
model is stronger. Read back both role briefs, writable paths, budgets, and
prohibited actions before dispatch. Never expose credentials or live config.

## Pitfalls

Using the same prompt and context for both roles creates correlated errors.
Parallel writers can overwrite each other; only one role may mutate a worktree.

## Verification

Confirm the verifier inspected the actual final diff and independent test output.
Record unresolved findings, model identities, budgets, and the final gate result.
