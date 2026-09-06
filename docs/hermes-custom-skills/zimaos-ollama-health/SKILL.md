---
name: zimaos-ollama-health
description: Diagnose Ollama health on ZimaOS safely.
version: 1.0.0
author: Workspace Maintainers and Hermes Agent
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [ZimaOS, Ollama, Diagnostics]
    category: devops
    related_skills: [z840-model-benchmarking]
---

# ZimaOS Ollama Health Skill

Diagnose an existing Ollama deployment on ZimaOS using read-only evidence. It
does not restart containers, pull models, edit compose files, or change settings.

## When to Use

Use when Ollama is unreachable, unhealthy, slow, or failing model requests on a
user-identified ZimaOS host.

## Prerequisites

- Confirm whether Ollama is a Docker container, another managed app, or remote.
- Obtain the exact host/container/service identifier and approved log window.
- Use `terminal` only on the explicitly approved host.

## How to Run

For Docker deployments, use documented read-only commands such as `docker ps`,
`docker inspect <container>`, and `docker logs --since <duration> <container>`.
For other deployment types, inspect their documented status interface first.

## Quick Reference

Check runtime state, health status, restart count, bind address, published ports,
recent errors, storage availability, accelerator visibility, and request latency.

## Procedure

1. Resolve the deployment type and read its current state twice.
2. Inspect health/restart metadata and bounded recent logs.
3. Test only the already-configured local endpoint with a harmless request.
4. Correlate timestamps; classify network, runtime, model, resource, or unknown.
5. Recommend a change separately; do not apply it during diagnosis.

## Safety and Read-Back

Never use `docker restart`, `docker rm`, `docker compose down`, model pull/delete,
or configuration writes. Read back host, container, endpoint, and time window
before every command; redact tokens and environment values from the report.

## Pitfalls

A running container is not proof of a responsive model. Unbounded logs may leak
secrets. A cold model load must not be mislabeled as steady-state latency.

## Verification

Repeat the status and harmless endpoint check, compare timestamps with logs, and
report exact exit/status codes plus any evidence that could not be collected.
