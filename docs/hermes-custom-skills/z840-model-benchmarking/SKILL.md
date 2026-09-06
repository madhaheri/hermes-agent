---
name: z840-model-benchmarking
description: Benchmark models reproducibly on an HP Z840.
version: 1.0.0
author: Workspace Maintainers and Hermes Agent
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [Benchmarking, Models, Hardware]
    category: mlops
    related_skills: [pair-capability-router]
---

# Z840 Model Benchmarking Skill

Build reproducible inference comparisons on an HP Z840 without changing model
services or machine configuration. This skill records evidence; it does not tune
drivers, download models, or select a winner from a single run.

## When to Use

Use for comparing already-installed models or runtimes on a Z840, investigating
performance regressions, or producing a hardware-specific benchmark report.

## Prerequisites

- User identifies the benchmark executable, model files, and allowed output path.
- Use `terminal` to run the executable's `--help` and copy only documented flags.
- Establish thermal/power conditions and confirm no competing workload.

## How to Run

1. Use `terminal` to capture CPU, memory, accelerator, runtime, and model identity.
2. Read the runtime help; stop if the requested metric has no documented flag.
3. Run one warm-up and at least three measured trials with identical inputs.
4. Store commands, stdout, stderr, exit codes, elapsed time, and environment notes.

## Quick Reference

Report time-to-first-token, generation rate, peak memory, errors, prompt size,
output size, quantization, context setting, and trial dispersion when available.

## Procedure

Use a fixed prompt corpus and deterministic settings where the runtime supports
them. Randomize model order when comparing multiple models. Separate load time
from generation time and never merge metrics reported in incompatible units.

## Safety and Read-Back

Do not download/delete models, alter firmware, drivers, power limits, services,
or live configuration. Before each run, read back the resolved executable path,
model path, output directory, and exact command; require all paths to remain in
the user-approved scope.

## Pitfalls

Warm-cache bias, thermal throttling, background load, and mismatched context or
quantization invalidate comparisons. Missing metrics are unknown, not zero.

## Verification

Re-run one median trial, confirm its result is within the observed spread, verify
every recorded exit code, and read back the report's model hashes/identifiers and
commands against captured terminal output.
