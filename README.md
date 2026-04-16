# Project Veritas — Dual-LLM Food Record Quality Pipeline

A production-grade pipeline that transforms raw food JSON records into richly structured, validated, and multilingual food data artifacts using a dual-LLM approach (Claude → Gemini → Claude).

## Architecture

```
Input JSON → Claude (iter 1) → Validator → PASS ✓
                                   ↓ FAIL
             Gemini (iter 2) → Validator → PASS ✓
                                   ↓ FAIL
             Claude (iter 3) → Validator → PASS ✓ / FAIL ✗
```

Each iteration builds on the previous draft and validator feedback, progressively improving the record until it passes all 6 hard gates and 8 scoring criteria.

## Files

| File | Purpose |
|------|---------|
| `veritas_dual_runner.py` | Main orchestrator — 5 parallel workers, 3-iteration loop, metrics |
| `veritas_companion.py` | Low-level per-file loop (provider-agnostic) |
| `veritas_validator.py` | Deterministic validator — 6 hard gates + 8 scoring criteria |
| `claude_runner.py` | Claude API caller (Anthropic) |
| `gemini_runner.py` | Gemini API caller (OpenAI-compatible endpoint) |
| `PROJECT_VERITAS_Ver2.md` | Project specification and ontology |
| `VERITAS_SCORING_MODEL_v2.md` | Full scoring model definition |
| `RESULT_template.json` | Output JSON schema template |
| `VERITAS_GENERIC_EXECUTION_PROMPT.md` | LLM execution prompt |

## Output Structure

```
gold_standard/    ← _pass.json files (score 8/8, all hard gates passed)
others/           ← _fail.json files (did not reach PASS after 3 iterations)
```

## Usage

```bash
# Run on a folder of input JSON files
python3 veritas_dual_runner.py /path/to/input_folder --output-folder /path/to/output --workers 5

# Resume an interrupted run
python3 veritas_dual_runner.py /path/to/input_folder --output-folder /path/to/output --resume
```

## Pilot Results (26 files)

| Metric | Value |
|--------|-------|
| Pass rate | 76.9% (20/26) |
| Passed on iter 1 (Claude) | 11 files |
| Passed on iter 2 (Gemini repair) | 2 files |
| Passed on iter 3 (Claude final) | 7 files |
| Did not pass | 6 files |
| Avg cost per file | ~$0.14 |
| Estimated cost per 1,000 files | ~$140 |
| Estimated cost per 6,600 files | ~$922 |

## Known Failure Patterns

1. **Zero-macro beverages** (sports drinks, sodas): `protein_g = 0` and `fat_g = 0` require explicit zero-justification notes in the output JSON — LLMs sometimes omit these.
2. **Approved source compliance**: Nutrient sources must come from the approved list (USDA, HPB, etc.) — generic web sources are rejected.
3. **GI source compliance**: Glycemic index values must cite approved GI databases.

## Requirements

```
anthropic
openai
```

Set environment variables:
```bash
export ANTHROPIC_API_KEY=sk-ant-...
export OPENAI_API_KEY=...  # Used for Gemini via OpenAI-compatible endpoint
```
