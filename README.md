# Project Veritas — Food Record Quality Pipeline

## Overview

Project Veritas is a dual-LLM food record enrichment and quality enforcement pipeline. It processes raw food JSON records through a three-iteration loop using Claude and Gemini, validated by a deterministic scoring model.

## Pipeline Architecture

```
Input JSON → Claude (iter 1) → Validator → PASS → gold_standard/
                                         → FAIL → Gemini (iter 2) → Validator → PASS → gold_standard/
                                                                               → FAIL → Claude (iter 3) → Validator → PASS → gold_standard/
                                                                                                                      → FAIL/REJECT → others/
```

## Folder Structure

- `gold_standard/` — All PASS records (score_total=8, all hard gates passed)
- `others/` — FAIL or REJECT records that did not pass after 3 iterations
- `VERITAS_GENERIC_EXECUTION_PROMPT.md` — Core LLM execution prompt (includes zero-macro justification rule)

## Pilot Results (26 files)

| Run | Files | PASS | FAIL | Pass Rate |
|-----|-------|------|------|-----------|
| Pilot (original) | 26 | 20 | 6 | 76.9% |
| Re-run (zero-macro fix) | 6 | 6 | 0 | 100% |
| **Combined** | **26** | **26** | **0** | **100%** |

### Zero-Macro Fix

The 6 original failures were all zero-macro beverages (Gatorade, Monster, Red Bull, Sprite, F&N Ice Cream Soda, Heaven & Earth Jasmine Tea). Root cause: the `g4_core_macro_sanity` validator gate requires an explicit zero-justification phrase in the `notes` field when `protein_g=0` or `fat_g=0`. The fix adds a **Zero-macro justification rule** section to `VERITAS_GENERIC_EXECUTION_PROMPT.md`.

## Scoring Model

6 hard gates + 8 scored criteria. A record passes only if:
- All 6 hard gates pass
- `score_total = 8` (all 8 criteria scored)

## Cost Metrics (Pilot)

| Metric | Value |
|--------|-------|
| Cost per file (min) | $0.0835 |
| Cost per file (avg) | $0.1244 |
| Cost per file (max) | $0.2553 |
| Estimated full 6,600 files | ~$820 |

## Production Scale

- Target: 6,600 food records
- Batch size: 25–50 files per run
- Workers: 5 parallel
- Deadline: April 28, 2026
