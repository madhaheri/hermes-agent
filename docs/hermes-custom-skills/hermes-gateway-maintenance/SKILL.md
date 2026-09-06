---
name: hermes-gateway-maintenance
description: Maintain Hermes gateways with explicit gates.
version: 1.0.0
author: Workspace Maintainers and Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [Hermes, Gateway, Operations]
    category: devops
    related_skills: [safe-model-archive-management]
---

# Hermes Gateway Maintenance Skill

Diagnose and prepare bounded Hermes gateway maintenance with read-back gates.
Status inspection is safe by default; restarts and configuration changes require
separate explicit authorization.

## When to Use

Use for gateway health checks, log triage, planned maintenance, or post-change
validation across one or more named Hermes profiles.

## Prerequisites

- Identify the profile, maintenance window, affected platforms, and rollback plan.
- Use `terminal` to inspect `hermes gateway --help` on the installed version.
- Preserve per-conversation caching; defer prompt-affecting changes to a new
  session unless the user explicitly requests the documented immediate mode.

## How to Run

Start with `hermes gateway status` and bounded `hermes logs gateway -n 100`.
Use `hermes gateway list` only when cross-profile status is in scope.

## Quick Reference

Evidence includes process/service state, profile, recent gateway errors, adapter
connectivity, version skew warnings, and a pre-maintenance baseline.

## Procedure

1. Capture status and bounded logs without changing state.
2. Classify configuration, credential, adapter, dependency, or process failure.
3. Present the exact proposed mutation, impact, and rollback; await authority.
4. If separately authorized, perform only that documented operation.
5. Re-run the original status and delivery checks after the change.

## Safety and Read-Back

Do not run start/stop/restart/install/uninstall/setup, edit config or `.env`, or
schedule work without explicit authorization. Read back profile, command, affected
platforms, expected interruption, and rollback immediately before any mutation.

## Pitfalls

Process-global environment does not identify the watching client. A healthy
service process does not prove platform delivery. Avoid unbounded or secret-rich
logs.

## Verification

Compare pre/post status, inspect bounded fresh logs, and test the authorized
platform path. Report command exit codes and any unverified adapter separately.
