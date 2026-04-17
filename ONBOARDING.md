# Project Veritas — Operator Onboarding Guide

Welcome to Project Veritas! This guide is designed to help operators, engineers, and data curators run the dual-LLM food record sanitization pipeline at scale.

## Table of Contents
1. [Overview and Architecture](#1-overview-and-architecture)
2. [Environment Setup](#2-environment-setup)
3. [The Processing Workflow](#3-the-processing-workflow)
4. [Scaling: Parallel Batch Processing](#4-scaling-parallel-batch-processing)
5. [Handling Failures and Re-runs](#5-handling-failures-and-re-runs)
6. [Prompt Engineering for Quality](#6-prompt-engineering-for-quality)
7. [GitHub Repository Management](#7-github-repository-management)
8. [Cross-References](#8-cross-references)

---

## 1. Overview and Architecture

The Veritas pipeline transforms raw, unverified food database JSON records into highly structured, multilingual, and nutritionally complete artifacts. 

### The Dual-LLM Loop
We use a **dual-runner architecture** to maximise pass rates while controlling costs:
1. **Iteration 1 (Claude):** Generates the initial draft.
2. **Iteration 2 (Gemini):** If Iteration 1 fails the validator, Gemini attempts to repair it.
3. **Iteration 3 (Claude):** If Iteration 2 fails, Claude makes a final attempt.

If the file passes the validator at any stage, it is saved as `{stem}_pass.json` and processing stops. If it fails all 3 iterations, it is saved as `{stem}_fail.json`.

### The Deterministic Validator
The pipeline relies on `veritas_validator.py`, which implements the logic defined in `VERITAS_SCORING_MODEL_v2.md`. It enforces:
- **6 Hard Gates (g1–g6):** Mandatory requirements (e.g., valid JSON, macro sanity, approved sources).
- **8 Scoring Criteria (q1–q8):** Quality checks (e.g., aliases consistency, glycemic index).

**Rule of Thumb:** The validator is the ground truth. We never relax the validator to pass a file; we update the execution prompt (`VERITAS_GENERIC_EXECUTION_PROMPT.md`) to teach the LLM how to pass.

---

## 2. Environment Setup

### Prerequisites
- Python 3.11+
- `anthropic` and `openai` pip packages installed.

### API Keys
The pipeline requires two API keys:
1. **Claude (Anthropic):** Set via `export ANTHROPIC_API_KEY="sk-ant-..."`
2. **Gemini (via OpenAI-compatible endpoint):** Pre-configured in the sandbox environment.

### Directory Structure
Always maintain a clean separation between the working directory and the Git repository:

```text
/home/ubuntu/veritas_project/         # Working Directory (do not commit)
├── veritas_dual_runner.py
├── veritas_validator.py
├── VERITAS_GENERIC_EXECUTION_PROMPT.md
├── PROJECT_VERITAS_Ver2.md
├── RESULT_template.json
├── VERITAS_SCORING_MODEL_v2.md
├── batch1_input/
└── batch1_output/

/home/ubuntu/veritas/                 # Git Repository (nuvear/veritas)
├── gold_standard/                    # All PASS records
├── others/                           # All FAIL records
├── VERITAS_GENERIC_EXECUTION_PROMPT.md
└── ONBOARDING.md
```

---

## 3. The Processing Workflow

Processing a single batch involves extraction, execution, validation, and committing to GitHub.

### Step 1: Extract Input
```bash
mkdir -p /home/ubuntu/veritas_project/batch1_input
unzip /path/to/1.zip -d /home/ubuntu/veritas_project/batch1_input/
```

### Step 2: Run Pipeline
```bash
cd /home/ubuntu/veritas_project
export ANTHROPIC_API_KEY="your-key"
nohup python3 veritas_dual_runner.py ./batch1_input/1 \
  --output-folder ./batch1_output --workers 5 \
  > batch1.log 2>&1 &
```
*Note: Always use 5 workers per API key. More workers will trigger rate limits.*

### Step 3: Monitor
```bash
tail -f batch1.log
# Quick status check:
echo "Pass: $(ls batch1_output/*_pass.json | wc -l)"
echo "Fail: $(ls batch1_output/*_fail.json | grep -v metrics | wc -l)"
```

### Step 4: Push to GitHub
Once the batch is complete, copy the results to the Git repo:
```bash
cd /home/ubuntu/veritas
cp /home/ubuntu/veritas_project/batch1_output/*_pass.json gold_standard/
cp /home/ubuntu/veritas_project/batch1_output/*_fail.json others/
git add -A
git commit -m "Batch 1: 21/25 PASS (84%)"
git push origin master
```

---

## 4. Scaling: Parallel Batch Processing

When processing thousands of records (e.g., 6,600+), sequential processing is too slow. Because the LLM API is the bottleneck, we scale by running **multiple batches simultaneously using dedicated API keys**.

### The Parallel Strategy
1. Obtain 5 distinct Anthropic API keys.
2. Extract 5 batches (e.g., Batches 7–11) into separate input folders.
3. Launch 5 instances of the runner, each pointing to a different input folder and using a different API key.
4. Each runner uses 5 workers, resulting in 25 total concurrent API calls.

### Example Launch Script
```bash
cd /home/ubuntu/veritas_project

KEY7="sk-ant-...1"
KEY8="sk-ant-...2"
# ... define keys 9, 10, 11

for N in 7 8 9 10 11; do
  key_var="KEY${N}"
  export ANTHROPIC_API_KEY="${!key_var}"
  nohup python3 veritas_dual_runner.py ./batch${N}_input/${N} \
    --output-folder ./batch${N}_output --workers 5 \
    > batch${N}.log 2>&1 &
done
```
This strategy achieves a **4.25× speedup**, processing ~250 files in ~30 minutes.

---

## 5. Handling Failures and Re-runs

### The Fail Accumulator Strategy
**Never re-run failing files inline.** If a file fails, it goes into the `others/` folder. We collect all failures and re-run them in a single **consolidated pass** at the end of the pipeline.

### Why do files fail?
1. **Mandatory Gate Failures (g1–g4):** The food record has a factual error (e.g., non-approved source, missing mandatory names) that no prompt fix can resolve. These permanently belong in `others/`.
2. **Quality Gate Failures (q1–q8):** The LLM made a formatting or logic error (e.g., missing aliases, incorrect GI category). These can be fixed by updating the execution prompt.
3. **Timeout Failures (g1 empty JSON):** Complex records may cause the LLM to time out, returning empty JSON.

### Re-running Timeout Failures (Score 8/g1)
If a record passes the validator internally (score 8/8) but ultimately fails due to an empty JSON timeout on the final write, it must be re-run with **1 worker** to reduce concurrent load and prevent timeouts.

---

## 6. Prompt Engineering for Quality

When you notice a recurring failure pattern in `others/`, you must update `VERITAS_GENERIC_EXECUTION_PROMPT.md`.

### How to add a fix:
1. Identify the exact gate failing (e.g., `q4_glycemic_completeness`).
2. Add a section to the prompt titled `## Rule name (CRITICAL — gateX)`.
3. Provide explicit instructions and a worked example.
4. Push the updated prompt to GitHub so all future runs use it.

*Example Fix:*
> ## Glycemic index for low/zero-carbohydrate foods (CRITICAL — q4)
> For foods with negligible carbohydrates (pure fats, hard cheeses, eggs):
> - Set `glycemic_index: null` (NOT 0)
> - Set `gi_category: "not_applicable"`

---

## 7. GitHub Repository Management

The `nuvear/veritas` repository is the source of truth for processed data.

- **`gold_standard/`**: Contains only `_pass.json` files. This folder only ever grows.
- **`others/`**: Contains `_fail.json` files. Files here are pending a consolidated re-run.
- **Commits**: Always include the batch number, pass count, total count, and pass rate in the commit message.

*Example Commit:*
`Batch 6: 21/25 PASS (84%) — Desserts and Seafood`

---

## 8. Cross-References

For detailed logic and rules, consult the following documents in the repository:
- **`PROJECT_VERITAS_Ver2.md`**: Business rules, approved sources, and operating model.
- **`VERITAS_SCORING_MODEL_v2.md`**: The definitive pass/fail logic implemented by the validator.
- **`VERITAS_GENERIC_EXECUTION_PROMPT.md`**: The prompt used by the LLMs (update this to fix recurring failures).

---
*Document maintained by Manus AI. Last updated: April 2026.*
