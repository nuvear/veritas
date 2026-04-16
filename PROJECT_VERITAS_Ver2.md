# PROJECT_VERITAS_Ver2.md

**Version:** 2.0  
**Date:** 2026-04-16  
**Status:** Draft

---

## 1. Purpose

Project Veritas v2 is a **food-record preparation and quality-enforcement workflow**.

It takes one **input JSON food record** and produces one **output JSON food record** that is:

1. nutritionally grounded in approved sources,
2. ontology-aligned,
3. multilingual where required,
4. semantically rich enough for LLM / RAG use, and
5. able to **self-score and iteratively improve** until it passes the Veritas standard.

This project is for **food database preparation** only.

### Out of scope
- images
- database import / update
- record matching against a live DB
- vector embedding
- file deletion or overwrite of original inputs

---

## 2. Governing files

Every execution must use these three control files together:

1. **PROJECT_VERITAS_Ver2.md** — mission, scope, processing rules, source policy, output behavior
2. **VERITAS_SCORING_MODEL_v2.md** — hard gates, Q1–Q8, pass/fail rules
3. **RESULT template JSON** — required output structure

### Precedence order
If any wording appears inconsistent, resolve in this order:

1. **RESULT template JSON** — authoritative field names and JSON structure
2. **VERITAS_SCORING_MODEL_v2.md** — authoritative pass/fail and acceptance logic
3. **PROJECT_VERITAS_Ver2.md** — authoritative process and source policy

---

## 3. Operating model

### Input
One file at a time:

`input_folder_name/input_filename.json`

The input may be incomplete, partially correct, or low quality.

### Processing sequence
1. Parse the input JSON.
2. Preserve useful facts from the input, but do not trust unsupported values blindly.
3. Normalize the record into the RESULT template structure.
4. Research and complete missing fields using approved sources only.
5. Apply ontology logic without inventing codes.
6. Apply multilingual naming and alias rules based on cuisine / region.
7. Draft the full RESULT JSON.
8. Self-score the draft using `VERITAS_SCORING_MODEL_v2.md`.
9. If the result is not `PASS`, revise the JSON and self-score again.
10. Repeat until either:
   - the file reaches `PASS`, or
   - the maximum iteration limit is reached.

### Maximum iterations
Default maximum: **5 internal improvement loops** per file.

---

## 4. Output behavior

Exactly one final output file is produced per input file.

### Input pattern
`input_folder_name/input_filename.json`

### Output pattern
- **Pass:** `input_folder_name/input_filename_pass.json`
- **Fail:** `input_folder_name/input_filename_fail.json`

### Examples
- Input: `1-50/001_hainanese_chicken_rice.json`
- Pass output: `1-50/001_hainanese_chicken_rice_pass.json`
- Fail output: `1-50/001_hainanese_chicken_rice_fail.json`

The original input file must never be overwritten.

---

## 5. Source policy

### 5.1 Source priority
Use the **highest-priority valid source** available for the specific food.

#### Primary regional sources
1. **HPB Singapore Foodo** — Singapore dishes
2. **ICMR-NIN** — Indian dishes
3. **IMR Malaysia MyFCD** — Malaysian dishes
4. **FAO/INFOODS SEA** — Southeast Asian dishes

#### Secondary global sources
5. **USDA Foundation Foods / SR Legacy** — raw ingredients, staples, Western generic foods
6. **USDA Branded Food** — packaged / commercial products only
7. **NCC Food and Nutrient Database** — generic category anchors

#### GI-specific source
8. **University of Sydney GI Database** and peer-reviewed journals

### 5.2 Forbidden sources
The following sources must not be used as primary evidence:
- delivery platforms
- calorie-tracking apps
- recipe websites
- general health-information websites
- restaurant websites
- uncited secondary summaries
- Wikipedia as a primary nutrient source

---

## 6. Record quality principles

Every output record must satisfy all of the following:

1. **Canonical English identity**
   - `food_name` must be the canonical English name.

2. **Multilingual identity**
   - Mandatory local-language names must be present based on cuisine / origin rules.
   - Mandatory local-language alias coverage must also be present.

3. **Nutritional integrity**
   - `per_100g` values must be numeric.
   - Core macros must not be zero unless explicitly justified.
   - Nutrient provenance must be named.

4. **GI integrity**
   - GI must come from the University of Sydney GI Database or a peer-reviewed journal.
   - If no direct GI exists, use the closest defensible analogous food from an approved GI source and clearly state the estimation basis.
   - A file cannot pass if GI remains unsupported and incomplete.

5. **Ontology honesty**
   - Never invent FoodOn, FoodEx2, or SNOMED CT codes.
   - `null` is allowed when justified with a gap note.
   - At least one defensible ontology anchor is required for a passing file.

6. **Clinical usefulness**
   - `health_context` must contain:
     - **Hypertension**
     - **Type 2 Diabetes**
     - **Cardiovascular Disease**
   - Each must include a specific clinical note.

7. **LLM readiness**
   - Description must be factual, specific, and long enough to support RAG.
   - Alias coverage and ingredient semantics must support multilingual retrieval.

---

## 7. Multilingual requirements

## 7.1 General rule
`food_name` is always the canonical English name.

## 7.2 Mandatory local-language names by cuisine / origin

### Singaporean dishes
Mandatory:
- English (satisfied by `food_name`)
- Chinese
- Malay
- Tamil
- Hindi

**Chinese requirement:** at least one Chinese-script form must be present.  
Preferred:
- `chinese_simplified`
- `chinese_traditional` when confidently known

### Japanese dishes
Mandatory:
- Japanese

### Korean dishes
Mandatory:
- Korean

### Indonesian dishes
Mandatory:
- Indonesian

### Thai dishes
Mandatory:
- Thai

### Vietnamese dishes
Mandatory:
- Vietnamese

## 7.3 Alias requirement
Alias coverage must include, where relevant:
- alternate English names
- local-language forms
- romanized forms
- common abbreviations
- common regional spelling variants

The flat `aliases` list is the retrieval-facing surface.  
The structured `aliases_by_language` object is the QA-facing surface.  
Both must be consistent.

---

## 8. Output content requirements

The RESULT JSON must contain these top-level sections at minimum:

- `veritas_meta`
- `food_name`
- `local_names`
- `aliases`
- `aliases_by_language`
- `cuisine`
- `region_of_origin`
- `food_group`
- `food_subgroup`
- `ontology`
- `classification`
- `serving`
- `nutrient_source`
- `per_100g`
- `glycemic_index`
- `gi_category`
- `glycemic_load_per_serving`
- `gi_source`
- `health_context`
- `llm_training`
- `confidence`
- `confidence_reason`
- `notes`
- `veritas_qc`

The output must be valid JSON only.

---

## 9. Confidence policy

Use these values only:

- `high` — exact dish found in an approved primary source
- `medium` — closest valid match used
- `low` — estimated from components or analogy

A `high` confidence file with weak provenance is invalid.

---

## 10. Self-scoring loop

Each draft must be tested against the scoring model.

### Decision states
- **PASS** — all hard gates pass and score is 8/8
- **FAIL** — hard gates pass but score is below 8/8
- **REJECT** — one or more hard gates fail

### Improvement behavior
- If `REJECT`, fix structural / provenance / ontology / multilingual hard-gate issues first.
- If `FAIL`, improve the failed Q criteria and rescore.
- Stop only when:
  - `PASS`, or
  - maximum iterations reached.

---

## 11. Final file status rules

### PASS file
A `_pass.json` output must:
- pass all hard gates,
- score 8/8,
- include final decision `PASS` in `veritas_qc`.

### FAIL file
A `_fail.json` output is allowed only when:
- the model has attempted the maximum iteration count,
- the file still does not pass,
- `veritas_qc` clearly lists every remaining failure.

A fail file is not accepted into the production-quality dataset.

---

## 12. Non-goals

This workflow does not:
- import anything into a database,
- map to existing DB IDs,
- execute SQL,
- resolve image content,
- create embeddings,
- update original source files.

---

## 13. Success definition

A file is considered successfully prepared only when:
- it conforms to the RESULT template,
- it passes `VERITAS_SCORING_MODEL_v2.md`,
- it is written as `_pass.json`.

A batch or folder is considered complete only when **every file** in that set is `_pass.json`.
