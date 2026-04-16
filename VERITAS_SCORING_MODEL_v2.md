# VERITAS_SCORING_MODEL_v2.md

**Version:** 2.0  
**Date:** 2026-04-16  
**Status:** Draft

---

## 1. Purpose

This scoring model defines the official acceptance standard for Project Veritas v2.

It is designed for **single-record evaluation** and supports:
- self-scoring by the LLM,
- reviewer auditing,
- pass / fail output naming,
- iterative improvement until acceptance.

The scoring system has **two layers**:

1. **Hard gates** — structural and integrity requirements  
2. **Q1–Q8 scorecard** — content quality requirements

---

## 2. Final decision states

## 2.1 PASS
A record is `PASS` only if:
- all hard gates pass, and
- the score is **8 / 8**

## 2.2 FAIL
A record is `FAIL` if:
- all hard gates pass, but
- the score is **0–7 / 8**

## 2.3 REJECT
A record is `REJECT` if:
- any hard gate fails

`REJECT` is more severe than `FAIL`.  
A rejected file must be structurally corrected before content scoring can be trusted.

---

## 3. Hard gates

## G1 — Valid JSON and template conformity
**Pass** if:
- the file is parseable JSON,
- the output follows the RESULT template structure,
- required top-level sections are present.

**Fail / Reject** if:
- invalid JSON,
- malformed arrays / objects,
- missing required sections,
- non-JSON commentary appears in the output.

---

## G2 — Approved-source compliance
**Pass** if:
- nutrient and GI evidence come from approved sources only,
- forbidden sources are not used as primary evidence.

**Fail / Reject** if:
- a forbidden source is used,
- provenance is absent or unusable,
- the source does not support the values claimed.

---

## G3 — Numeric hygiene
**Pass** if:
- all `per_100g` values are numeric,
- no strings such as `"N/A"` appear,
- `null` is not used inside `per_100g`,
- `-1` is used only when the source truly does not report the nutrient,
- `0.0` is used only for confirmed trace / negligible amounts.

**Fail / Reject** if:
- non-numeric nutrient values appear,
- mixed data types appear,
- unsupported use of `-1` or `0.0`.

---

## G4 — Core macro sanity
Core macros:
- `energy_kcal`
- `protein_g`
- `fat_g`
- `carbohydrate_g`

**Pass** if:
- all four exist,
- all four are numeric,
- none is zero unless the record clearly documents a defensible reason.

**Fail / Reject** if:
- any core macro is missing,
- any is zero without explanation,
- any is obviously contradictory to the cited source.

---

## G5 — Ontology honesty
**Pass** if:
- ontology codes are real and defensible,
- `null` values are paired with honest gap notes where needed,
- no ontology code is invented.

**Fail / Reject** if:
- any ontology code is fabricated,
- all ontology anchors are empty and unexplained,
- the ontology section is internally inconsistent.

---

## G6 — Multilingual naming compliance
**Pass** if the record satisfies the mandatory language rules for its cuisine / origin.

**Mandatory language rules**
- **Singaporean dishes**: English, Chinese, Malay, Tamil, Hindi
- **Japanese dishes**: Japanese
- **Korean dishes**: Korean
- **Indonesian dishes**: Indonesian
- **Thai dishes**: Thai
- **Vietnamese dishes**: Vietnamese

**Notes**
- English is satisfied by `food_name`.
- For Chinese, at least one Chinese-script form must be present.
- Native-script forms are preferred over transliteration-only forms.

**Fail / Reject** if:
- any mandatory language is missing,
- the local-language name is clearly wrong,
- language coverage is incomplete for the cuisine category.

---

## 4. Scorecard

Each criterion is worth **1 point**.

Maximum score: **8**

---

## Q1 — Food identity
**Pass** if:
- `food_name` is a precise canonical English food name,
- it is not vague, promotional, or menu-style clutter,
- required local-language identity is present according to G6.

**Fail** if:
- the food name is ambiguous,
- the English name is not canonical,
- local-language identity is missing or misleading.

---

## Q2 — Core macro completeness and source integrity
**Pass** if:
- `energy_kcal`, `protein_g`, `fat_g`, and `carbohydrate_g` are complete,
- the values are supported by a named source,
- `nutrient_source` clearly states the evidence basis.

**Fail** if:
- core macros are incomplete,
- source linkage is weak,
- values are unsupported.

---

## Q3 — Secondary nutrient completeness
Required fields for scoring:
- `dietary_fibre_g`
- `sodium_mg`
- `saturated_fat_g`
- `sugar_g`

**Pass** if:
- these fields are present as numeric values,
- the values are plausible and supported.

**Fail** if:
- any are absent,
- any are non-numeric,
- any are omitted without explanation.

---

## Q4 — Glycemic completeness
**Pass** if:
- `glycemic_index` is present,
- `gi_category` is present,
- `glycemic_load_per_serving` is present,
- `gi_source` is present,
- the GI evidence comes from the University of Sydney GI Database or a peer-reviewed journal.

**Fail** if:
- GI is absent,
- GI category is absent,
- GL is absent,
- GI source is missing or invalid.

**Interpretation rule**
If the GI is estimated from the closest analogous food, that analogous food must still come from an approved GI source and the estimation basis must be documented.

---

## Q5 — Ontological alignment
**Pass** if:
- at least one ontology anchor is defensible,
- FoodEx2 is present whenever a defensible code exists,
- FoodOn and SNOMED CT are used honestly,
- nulls are documented with gap notes where required.

**Fail** if:
- all anchors are absent without justification,
- FoodEx2 is ignored where an obvious defensible match exists,
- ontology descriptions and codes do not align.

---

## Q6 — LLM-ready semantics
**Pass** if:
- `llm_training.natural_language_description` is at least 200 characters,
- it explains what the food is,
- how it is prepared,
- taste / texture,
- clinical relevance,
- key nutrients or nutritional profile.

**Fail** if:
- the text is too short,
- generic,
- vague,
- or not useful for RAG / LLM training.

---

## Q7 — Health context
The human-readable condition name must be **Type 2 Diabetes**.  
The machine key may remain `type2_diabetes`.

Required conditions:
- Hypertension
- Type 2 Diabetes
- Cardiovascular Disease

**Pass** if:
- all three are present,
- each has `concern_level`,
- each has `key_factor`,
- each has a specific clinical `guidance` sentence.

**Fail** if:
- any condition is missing,
- the note is generic,
- the guidance is non-clinical,
- the condition names do not map correctly.

---

## Q8 — Aliases and multilingual synonym coverage
**Pass** if:
- `aliases` is comprehensive,
- `aliases_by_language` is populated where relevant,
- mandatory languages have at least one local-language alias or synonym form where natural,
- alternate English names, regional variants, abbreviations, and romanizations are captured where relevant.

**Fail** if:
- aliases are sparse,
- only the canonical English name is repeated,
- multilingual alias coverage is missing,
- structured alias coverage and flat aliases are inconsistent.

---

## 5. Multilingual matrix

## 5.1 Singaporean dishes
Required:
- `food_name` in English
- Chinese local name
- Malay local name
- Tamil local name
- Hindi local name

Preferred:
- both simplified and traditional Chinese if confidently known
- romanized local pronunciation if useful

## 5.2 Japanese dishes
Required:
- Japanese native-script name

## 5.3 Korean dishes
Required:
- Korean native-script name

## 5.4 Indonesian dishes
Required:
- Indonesian name

## 5.5 Thai dishes
Required:
- Thai native-script name

## 5.6 Vietnamese dishes
Required:
- Vietnamese native-script name

---

## 6. Batch rule

A folder / batch passes only if **every file** is `PASS`.

- One failed file means the batch remains open.
- One rejected file means the batch must be corrected before acceptance.

---

## 7. Reviewer notes

1. Do not award partial credit inside a criterion.
2. If a criterion substantially fails, score it `0`.
3. If a record is structurally unsound, mark `REJECT` first.
4. Do not excuse weak provenance just because the narrative text is strong.
5. Do not excuse weak multilingual coverage for cuisines that require it.

---

## 8. Minimal reviewer checklist

- JSON valid?
- Approved sources only?
- Core macros complete?
- GI complete and approved?
- Ontology honest?
- Multilingual rule satisfied?
- Type 2 Diabetes included?
- Score 8 / 8?

If any answer is no, the file does not pass.
