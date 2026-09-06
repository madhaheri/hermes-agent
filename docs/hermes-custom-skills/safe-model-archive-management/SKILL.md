---
name: safe-model-archive-management
description: Archive model artifacts with recovery checks.
version: 1.0.0
author: Workspace Maintainers and Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [Models, Archive, Storage]
    category: mlops
    related_skills: [z840-model-benchmarking]
---

# Safe Model Archive Management Skill

Plan and perform recoverable archival of user-approved model artifacts. This
skill defaults to inventory and dry-run planning; deletion is never implied.

## When to Use

Use to reclaim model storage, preserve an inactive model, verify an archive, or
restore an explicitly identified artifact.

## Prerequisites

- User supplies source, archive destination, retention policy, and artifact owner.
- Use `search_files` and `read_file` to identify manifests and runtime references.
- Confirm the source is not active, mounted, locked, or required by a live service.

## How to Run

Use `terminal` only for documented archive/checksum tools already present on the
host. First run their `--help`; record the exact command and never infer flags.

## Quick Reference

Inventory identity, format, size, hashes, permissions, references, free space,
archive location, restoration command, and retention/expiry decision.

## Procedure

1. Inventory without mutation and resolve symlinks/mount boundaries.
2. Estimate required destination space and create a dry-run manifest.
3. Read back exact source and destination; obtain approval for the copy/move.
4. Create the archive without deleting the source.
5. Verify listing, size, hashes, and a test extraction to a temporary location.
6. Treat source removal as a separate destructive request.

## Safety and Read-Back

Never archive a path selected by an unresolved variable, wildcard, home/workspace
root, or model name alone. Never overwrite an archive. Read back canonical paths,
byte counts, hashes, active references, and recovery steps before mutation.

## Pitfalls

Sparse files, hard links, symlinks, permissions, and runtime-managed stores can
make copied byte counts misleading. Encryption without tested key recovery is
not a backup.

## Verification

Validate the manifest and hashes, perform a test extraction, confirm the source
still exists, and record where/how recovery was proven before reporting success.
