#!/usr/bin/env python3
"""
Veritas v2 validator

Checks a candidate output JSON against PROJECT_VERITAS_Ver2.md /
VERITAS_SCORING_MODEL_v2.md / RESULT_template.json rules encoded in code,
updates the embedded veritas_qc block for valid JSON objects, and renames the
file to *_pass.json or *_fail.json.

Usage:
    python veritas_validator.py /path/to/file.json
    python veritas_validator.py /path/to/folder --recursive
    python veritas_validator.py /path/to/file.json --no-rename
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple


REQUIRED_TOP_LEVEL = [
    "veritas_meta",
    "food_name",
    "local_names",
    "aliases",
    "aliases_by_language",
    "cuisine",
    "region_of_origin",
    "food_group",
    "food_subgroup",
    "ontology",
    "classification",
    "serving",
    "nutrient_source",
    "per_100g",
    "glycemic_index",
    "gi_category",
    "glycemic_load_per_serving",
    "gi_source",
    "health_context",
    "llm_training",
    "confidence",
    "confidence_reason",
    "notes",
    "veritas_qc",
]

REQUIRED_PER100G = [
    "energy_kcal",
    "protein_g",
    "fat_g",
    "saturated_fat_g",
    "monounsaturated_fat_g",
    "polyunsaturated_fat_g",
    "trans_fat_g",
    "carbohydrate_g",
    "sugar_g",
    "dietary_fibre_g",
    "sodium_mg",
    "potassium_mg",
    "calcium_mg",
    "iron_mg",
    "cholesterol_mg",
    "vitamin_c_mg",
    "vitamin_a_mcg_rae",
    "vitamin_b1_thiamine_mg",
    "vitamin_b2_riboflavin_mg",
    "vitamin_b3_niacin_mg",
    "vitamin_b6_mg",
    "vitamin_b12_mcg",
    "folate_mcg_dfe",
    "vitamin_d_mcg",
    "vitamin_e_mg",
    "phosphorus_mg",
    "magnesium_mg",
    "zinc_mg",
    "selenium_mcg",
    "water_g",
]

CORE_MACROS = ["energy_kcal", "protein_g", "fat_g", "carbohydrate_g"]
SECONDARY_SCORING_FIELDS = ["dietary_fibre_g", "sodium_mg", "saturated_fat_g", "sugar_g"]

CONFIDENCE_ALLOWED = {"high", "medium", "low"}
CONCERN_ALLOWED = {"low", "moderate", "high"}
GI_CATEGORY_ALLOWED = {"low", "medium", "high"}
PROCESSING_LEVEL_ALLOWED = {"raw", "minimally processed", "processed", "ultra-processed"}
SOURCE_MATCH_ALLOWED = {"exact", "adapted", "estimated"}
GI_SOURCE_MATCH_ALLOWED = {"exact", "analogous_estimate"}
MATCH_TYPE_ALLOWED = {"exact", "closest_parent", "none"}

LOCAL_NAME_KEYS = {
    "chinese": ("chinese_simplified", "chinese_traditional"),
    "malay": ("malay",),
    "tamil": ("tamil",),
    "hindi": ("hindi",),
    "japanese": ("japanese",),
    "korean": ("korean",),
    "indonesian": ("indonesian",),
    "thai": ("thai",),
    "vietnamese": ("vietnamese",),
}

CUISINE_LANGUAGE_RULES = {
    "singapore": {"english", "chinese", "malay", "tamil", "hindi"},
    "japanese": {"english", "japanese"},
    "korean": {"english", "korean"},
    "indonesian": {"english", "indonesian"},
    "thai": {"english", "thai"},
    "vietnamese": {"english", "vietnamese"},
}

APPROVED_SOURCE_PATTERNS = [
    "hpb", "foodo.sg", "health promotion board",
    "icmr", "nin.res.in", "nutritive value of indian foods", "national institute of nutrition",
    "imr", "myfcd", "nutriweb.org.my", "institute for medical research",
    "fao", "infoods", "fao.org/infoods",
    "usda", "fdc.nal.usda.gov", "foundation foods", "sr legacy", "branded food",
    "ncc", "nutrition coordinating center", "food and nutrient database",
]

FORBIDDEN_SOURCE_PATTERNS = [
    "grabfood", "foodpanda", "deliveroo",
    "myfitnesspal", "cronometer", "lose it", "fatsecret",
    "allrecipes", "tasty", "yummly", "bbc good food",
    "healthline", "webmd", "medical news today",
    "wikipedia",
]

GI_APPROVED_PATTERNS = [
    "university of sydney", "glycemicindex.com", "glycaemicindex.com",
    "glycemic index foundation", "glycaemic index foundation",
    "doi.org", "pubmed", "ncbi", "journal", "sciencedirect", "springer", "wiley", "elsevier",
]

ZERO_JUSTIFICATION_PATTERNS = [
    "naturally zero", "naturally contains no", "contains no", "zero because", "not present",
    "trace", "negligible", "confirmed zero", "confirmed as zero", "source reports 0",
]

MISSING_NUTRIENT_PATTERNS = [
    "not reported", "not available", "source did not report", "source does not report",
    "unreported", "missing from source", "not listed in source",
]

PREP_KEYWORDS = [
    "steamed", "fried", "boiled", "roasted", "braised", "grilled", "baked", "cooked",
    "stir-fried", "poached", "simmered", "marinated", "served", "fermented", "blanched",
]
SENSORY_KEYWORDS = [
    "sweet", "savory", "salty", "spicy", "sour", "bitter", "crispy", "crunchy", "soft",
    "tender", "creamy", "rich", "fragrant", "aromatic", "chewy", "umami", "texture",
]
CLINICAL_KEYWORDS = [
    "calorie", "protein", "fat", "carbohydrate", "carb", "fibre", "fiber", "sodium", "glycemic",
    "blood sugar", "hypertension", "diabetes", "cardiovascular", "nutrient", "clinical",
]
CULTURAL_KEYWORDS = [
    "traditional", "popular", "common", "hawker", "street food", "festive", "breakfast",
    "lunch", "dinner", "served in", "cuisine", "singapore", "malaysia", "india", "japan",
    "korea", "indonesia", "thailand", "vietnam",
]


@dataclass
class ValidationResult:
    original_path: Path
    final_path: Path
    decision: str
    score_total: int = 0
    score_max: int = 8
    hard_gates: Dict[str, bool] = field(default_factory=dict)
    criteria: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    failure_reasons: List[str] = field(default_factory=list)
    reviewer_summary: str = ""
    renamed: bool = False
    updated_json: bool = False
    parse_error: Optional[str] = None


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, obj: Any) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")


def is_nonempty_str(value: Any) -> bool:
    return isinstance(value, str) and value.strip() != ""


def is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def norm_text(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"\s+", " ", value)
    return value


def flatten_texts(value: Any) -> List[str]:
    out: List[str] = []
    if isinstance(value, dict):
        for v in value.values():
            out.extend(flatten_texts(v))
    elif isinstance(value, list):
        for v in value:
            out.extend(flatten_texts(v))
    elif isinstance(value, str):
        out.append(value)
    return out


def combined_notes(record: Dict[str, Any]) -> str:
    parts: List[str] = []
    for key in ["notes", "confidence_reason"]:
        value = record.get(key)
        if is_nonempty_str(value):
            parts.append(value)
    for section_key in ["nutrient_source", "gi_source", "ontology", "veritas_qc"]:
        section = record.get(section_key)
        if isinstance(section, dict):
            parts.extend(flatten_texts(section))
    return " \n".join(parts).lower()


def contains_any(haystack: str, patterns: Sequence[str]) -> bool:
    haystack = haystack.lower()
    return any(p.lower() in haystack for p in patterns)


def source_text(source: Dict[str, Any]) -> str:
    if not isinstance(source, dict):
        return ""
    return " ".join(flatten_texts(source)).lower()


def source_allowed(source: Dict[str, Any], gi: bool = False) -> Tuple[bool, str]:
    text = source_text(source)
    if not text:
        return False, "source block is missing or empty"
    if contains_any(text, FORBIDDEN_SOURCE_PATTERNS):
        return False, "forbidden source detected"
    if gi:
        if contains_any(text, GI_APPROVED_PATTERNS):
            return True, "GI source matches approved patterns"
        return False, "GI source is not from University of Sydney GI Database or a peer-reviewed journal"
    if contains_any(text, APPROVED_SOURCE_PATTERNS):
        return True, "nutrient source matches approved patterns"
    return False, "nutrient source is not from an approved source list"


def derive_required_languages(record: Dict[str, Any]) -> Set[str]:
    text = f"{record.get('cuisine', '')} {record.get('region_of_origin', '')}".lower()
    required: Set[str] = {"english"}
    if "singapore" in text:
        required |= CUISINE_LANGUAGE_RULES["singapore"]
    if "japan" in text or "japanese" in text:
        required |= CUISINE_LANGUAGE_RULES["japanese"]
    if "korea" in text or "korean" in text:
        required |= CUISINE_LANGUAGE_RULES["korean"]
    if "indonesia" in text or "indonesian" in text:
        required |= CUISINE_LANGUAGE_RULES["indonesian"]
    if "thai" in text or "thailand" in text:
        required |= CUISINE_LANGUAGE_RULES["thai"]
    if "vietnam" in text or "vietnamese" in text:
        required |= CUISINE_LANGUAGE_RULES["vietnamese"]
    return required


def has_required_local_name(record: Dict[str, Any], language: str) -> bool:
    local_names = record.get("local_names")
    if not isinstance(local_names, dict):
        return False
    if language == "english":
        return is_nonempty_str(record.get("food_name"))
    keys = LOCAL_NAME_KEYS.get(language, ())
    return any(is_nonempty_str(local_names.get(k)) for k in keys)


def alias_values(record: Dict[str, Any]) -> Tuple[Set[str], Dict[str, Set[str]]]:
    flat_aliases = record.get("aliases") if isinstance(record.get("aliases"), list) else []
    flat_norm = {norm_text(a) for a in flat_aliases if is_nonempty_str(a)}
    aliases_by_language = record.get("aliases_by_language") if isinstance(record.get("aliases_by_language"), dict) else {}
    structured: Dict[str, Set[str]] = {}
    for lang, values in aliases_by_language.items():
        if isinstance(values, list):
            structured[lang] = {norm_text(v) for v in values if is_nonempty_str(v)}
        else:
            structured[lang] = set()
    return flat_norm, structured


def gi_category_expected(gi_value: float) -> Optional[str]:
    if gi_value < 0:
        return None
    if gi_value <= 55:
        return "low"
    if gi_value <= 69:
        return "medium"
    return "high"


def format_nonempty(value: Any) -> bool:
    return is_nonempty_str(value)


def count_sentences(text: str) -> int:
    return len([s for s in re.split(r"[.!?]+", text) if s.strip()])


def validate_valid_json_and_template(record: Dict[str, Any]) -> Tuple[bool, List[str]]:
    reasons: List[str] = []
    missing = [k for k in REQUIRED_TOP_LEVEL if k not in record]
    if missing:
        reasons.append(f"missing top-level sections: {', '.join(missing)}")
    if not isinstance(record.get("local_names"), dict):
        reasons.append("local_names must be an object")
    if not isinstance(record.get("aliases"), list):
        reasons.append("aliases must be an array")
    if not isinstance(record.get("aliases_by_language"), dict):
        reasons.append("aliases_by_language must be an object")
    if not isinstance(record.get("ontology"), dict):
        reasons.append("ontology must be an object")
    if not isinstance(record.get("classification"), dict):
        reasons.append("classification must be an object")
    if not isinstance(record.get("serving"), dict):
        reasons.append("serving must be an object")
    if not isinstance(record.get("nutrient_source"), dict):
        reasons.append("nutrient_source must be an object")
    if not isinstance(record.get("per_100g"), dict):
        reasons.append("per_100g must be an object")
    if not isinstance(record.get("gi_source"), dict):
        reasons.append("gi_source must be an object")
    if not isinstance(record.get("health_context"), dict):
        reasons.append("health_context must be an object")
    if not isinstance(record.get("llm_training"), dict):
        reasons.append("llm_training must be an object")
    return len(reasons) == 0, reasons


def validate_approved_sources(record: Dict[str, Any]) -> Tuple[bool, List[str]]:
    reasons: List[str] = []
    ok, msg = source_allowed(record.get("nutrient_source", {}), gi=False)
    if not ok:
        reasons.append(f"nutrient_source: {msg}")
    ok, msg = source_allowed(record.get("gi_source", {}), gi=True)
    if not ok:
        reasons.append(f"gi_source: {msg}")
    # Basic required fields in source blocks
    for block_name, block, allowed in [
        ("nutrient_source", record.get("nutrient_source", {}), SOURCE_MATCH_ALLOWED),
        ("gi_source", record.get("gi_source", {}), GI_SOURCE_MATCH_ALLOWED),
    ]:
        if isinstance(block, dict):
            for field_name in ["title", "organization", "url", "accessed_date", "source_match_type", "source_match_notes"]:
                if not format_nonempty(block.get(field_name)):
                    reasons.append(f"{block_name}.{field_name} is missing or empty")
            if format_nonempty(block.get("source_match_type")) and block.get("source_match_type") not in allowed:
                reasons.append(f"{block_name}.source_match_type must be one of {sorted(allowed)}")
    return len(reasons) == 0, reasons


def validate_numeric_hygiene(record: Dict[str, Any]) -> Tuple[bool, List[str]]:
    reasons: List[str] = []
    per = record.get("per_100g")
    notes_text = combined_notes(record)
    if not isinstance(per, dict):
        return False, ["per_100g missing or not an object"]
    missing_fields = [k for k in REQUIRED_PER100G if k not in per]
    if missing_fields:
        reasons.append(f"per_100g missing fields: {', '.join(missing_fields)}")
    for key, value in per.items():
        if not is_number(value):
            reasons.append(f"per_100g.{key} must be numeric")
            continue
        if value < 0 and value != -1:
            reasons.append(f"per_100g.{key} has unsupported negative value {value}")
        if value == -1 and not contains_any(notes_text, MISSING_NUTRIENT_PATTERNS):
            reasons.append(f"per_100g.{key} = -1 but missing-data basis is not documented")
    return len(reasons) == 0, reasons


def validate_core_macros(record: Dict[str, Any]) -> Tuple[bool, List[str]]:
    reasons: List[str] = []
    per = record.get("per_100g")
    if not isinstance(per, dict):
        return False, ["per_100g missing or not an object"]
    notes_text = combined_notes(record)
    for key in CORE_MACROS:
        if key not in per:
            reasons.append(f"per_100g.{key} is missing")
            continue
        value = per.get(key)
        if not is_number(value):
            reasons.append(f"per_100g.{key} must be numeric")
            continue
        if value == 0 and not contains_any(notes_text, ZERO_JUSTIFICATION_PATTERNS):
            reasons.append(f"per_100g.{key} is zero without documented justification")
    return len(reasons) == 0, reasons


def validate_ontology(record: Dict[str, Any]) -> Tuple[bool, List[str]]:
    reasons: List[str] = []
    ontology = record.get("ontology")
    if not isinstance(ontology, dict):
        return False, ["ontology missing or not an object"]

    foodon_uri = ontology.get("foodon_uri")
    foodex2_code = ontology.get("foodex2_code")
    snomed_ct_code = ontology.get("snomed_ct_code")

    if foodon_uri is not None and foodon_uri != "" and not re.fullmatch(r"https?://purl\.obolibrary\.org/obo/FOODON_\d+", str(foodon_uri)):
        reasons.append("ontology.foodon_uri has invalid format")
    if foodex2_code is not None and foodex2_code != "" and not re.fullmatch(r"[A-Z0-9]+", str(foodex2_code)):
        reasons.append("ontology.foodex2_code has invalid format")
    if snomed_ct_code is not None and snomed_ct_code != "" and not re.fullmatch(r"\d{6,18}", str(snomed_ct_code)):
        reasons.append("ontology.snomed_ct_code has invalid format")

    for field_name in ["foodon_match_type", "foodex2_match_type", "snomed_ct_match_type"]:
        value = ontology.get(field_name)
        if value is not None and value != "" and value not in MATCH_TYPE_ALLOWED:
            reasons.append(f"ontology.{field_name} must be one of {sorted(MATCH_TYPE_ALLOWED)}")

    anchors_present = any(is_nonempty_str(ontology.get(k)) for k in ["foodon_uri", "foodex2_code", "snomed_ct_code"])
    gap_present = any(is_nonempty_str(ontology.get(k)) for k in ["foodon_gap_note", "foodex2_gap_note", "snomed_ct_gap_note"])
    if not anchors_present and not gap_present:
        reasons.append("ontology has no anchors and no gap notes")
    return len(reasons) == 0, reasons


def validate_multilingual(record: Dict[str, Any]) -> Tuple[bool, List[str], Set[str]]:
    required_languages = derive_required_languages(record)
    reasons: List[str] = []
    for language in sorted(required_languages):
        if language == "english":
            if not is_nonempty_str(record.get("food_name")):
                reasons.append("food_name is missing for required English identity")
            continue
        if not has_required_local_name(record, language):
            reasons.append(f"mandatory local name missing for language: {language}")
    return len(reasons) == 0, reasons, required_languages


def score_q1(record: Dict[str, Any], multilingual_ok: bool) -> Tuple[bool, str]:
    name = record.get("food_name")
    if not is_nonempty_str(name):
        return False, "food_name is missing"
    cleaned = name.strip()
    if len(cleaned) < 3:
        return False, "food_name is too short"
    if not multilingual_ok:
        return False, "mandatory local-language identity is incomplete"
    return True, "canonical English name present and multilingual identity satisfied"


def score_q2(record: Dict[str, Any], source_gate_ok: bool, core_ok: bool) -> Tuple[bool, str]:
    if not core_ok:
        return False, "core macros are incomplete or invalid"
    if not source_gate_ok:
        return False, "nutrient source is not compliant"
    return True, "core macros and nutrient source integrity pass"


def score_q3(record: Dict[str, Any]) -> Tuple[bool, str]:
    per = record.get("per_100g")
    if not isinstance(per, dict):
        return False, "per_100g missing"
    missing = [k for k in SECONDARY_SCORING_FIELDS if k not in per]
    if missing:
        return False, f"missing secondary nutrient fields: {', '.join(missing)}"
    non_numeric = [k for k in SECONDARY_SCORING_FIELDS if not is_number(per.get(k))]
    if non_numeric:
        return False, f"non-numeric secondary nutrient fields: {', '.join(non_numeric)}"
    return True, "secondary nutrient fields are present and numeric"


def score_q4(record: Dict[str, Any], source_gate_ok: bool) -> Tuple[bool, str]:
    gi = record.get("glycemic_index")
    gl = record.get("glycemic_load_per_serving")
    category = record.get("gi_category")
    if not is_number(gi):
        return False, "glycemic_index must be numeric"
    if gi <= 0:
        return False, "glycemic_index must be greater than zero"
    if not is_number(gl):
        return False, "glycemic_load_per_serving must be numeric"
    if gl < 0:
        return False, "glycemic_load_per_serving must be non-negative"
    if not is_nonempty_str(category) or category not in GI_CATEGORY_ALLOWED:
        return False, "gi_category must be one of low/medium/high"
    expected = gi_category_expected(float(gi))
    if expected and category != expected:
        return False, f"gi_category '{category}' does not match glycemic_index {gi} (expected {expected})"
    if not source_gate_ok:
        return False, "GI source is not compliant"
    return True, "GI, GI category, GL, and GI source are complete"


def score_q5(record: Dict[str, Any]) -> Tuple[bool, str]:
    ontology = record.get("ontology")
    if not isinstance(ontology, dict):
        return False, "ontology missing"
    anchors = [k for k in ["foodon_uri", "foodex2_code", "snomed_ct_code"] if is_nonempty_str(ontology.get(k))]
    if not anchors:
        return False, "no ontology anchor present"
    return True, f"ontology anchor(s) present: {', '.join(anchors)}"


def score_q6(record: Dict[str, Any]) -> Tuple[bool, str]:
    llm = record.get("llm_training")
    if not isinstance(llm, dict):
        return False, "llm_training missing"
    text = llm.get("natural_language_description")
    if not is_nonempty_str(text):
        return False, "natural_language_description is missing"
    text = str(text).strip()
    if len(text) < 200:
        return False, f"natural_language_description too short ({len(text)} chars)"
    if count_sentences(text) < 2:
        return False, "natural_language_description should contain at least 2 sentences"
    lower = text.lower()
    buckets = 0
    if contains_any(lower, PREP_KEYWORDS):
        buckets += 1
    if contains_any(lower, SENSORY_KEYWORDS):
        buckets += 1
    if contains_any(lower, CLINICAL_KEYWORDS):
        buckets += 1
    if contains_any(lower, CULTURAL_KEYWORDS):
        buckets += 1
    if buckets < 3:
        return False, "natural_language_description lacks enough preparation/sensory/clinical/cultural coverage"
    return True, "natural_language_description meets minimum semantic coverage"


def score_q7(record: Dict[str, Any]) -> Tuple[bool, str]:
    health = record.get("health_context")
    if not isinstance(health, dict):
        return False, "health_context missing"
    required = {
        "hypertension": "Hypertension",
        "type2_diabetes": "Type 2 Diabetes",
        "cardiovascular_disease": "Cardiovascular Disease",
    }
    missing = [k for k in required if k not in health]
    if missing:
        return False, f"missing health conditions: {', '.join(missing)}"
    for key, human_name in required.items():
        entry = health.get(key)
        if not isinstance(entry, dict):
            return False, f"health_context.{key} must be an object"
        if entry.get("human_condition_name") != human_name:
            return False, f"health_context.{key}.human_condition_name must be '{human_name}'"
        if entry.get("concern_level") not in CONCERN_ALLOWED:
            return False, f"health_context.{key}.concern_level must be one of {sorted(CONCERN_ALLOWED)}"
        if not is_nonempty_str(entry.get("key_factor")):
            return False, f"health_context.{key}.key_factor is missing"
        guidance = entry.get("guidance")
        if not is_nonempty_str(guidance) or len(str(guidance).strip()) < 20:
            return False, f"health_context.{key}.guidance is missing or too short"
    return True, "required health-context conditions are complete"


def score_q8(record: Dict[str, Any], required_languages: Set[str]) -> Tuple[bool, str]:
    flat_aliases, structured = alias_values(record)
    if len(flat_aliases) < 2:
        return False, "aliases must contain at least 2 non-empty items"
    canonical = norm_text(str(record.get("food_name", ""))) if is_nonempty_str(record.get("food_name")) else ""
    if canonical and flat_aliases == {canonical}:
        return False, "aliases only repeat the canonical food_name"

    # Consistency: structured aliases should also surface in flat aliases.
    missing_from_flat: List[str] = []
    for lang, values in structured.items():
        for value in values:
            if value not in flat_aliases:
                missing_from_flat.append(f"{lang}:{value}")
    if missing_from_flat:
        return False, "aliases_by_language entries missing from flat aliases"

    language_gaps: List[str] = []
    for language in sorted(required_languages - {"english"}):
        structured_key = "chinese" if language == "chinese" else language
        lang_aliases = structured.get(structured_key, set())
        if lang_aliases:
            continue
        # fallback: allow local name to appear in flat aliases
        local_names = record.get("local_names") if isinstance(record.get("local_names"), dict) else {}
        local_values = []
        for key in LOCAL_NAME_KEYS.get(language, ()):
            value = local_names.get(key)
            if is_nonempty_str(value):
                local_values.append(norm_text(value))
        if not any(v in flat_aliases for v in local_values):
            language_gaps.append(language)
    if language_gaps:
        return False, f"mandatory alias coverage missing for: {', '.join(language_gaps)}"

    return True, "alias coverage is adequate and multilingual alias consistency passes"


def evaluate_record(record: Dict[str, Any], source_path: Path) -> Tuple[Dict[str, Any], ValidationResult]:
    updated = copy.deepcopy(record)
    result = ValidationResult(original_path=source_path, final_path=source_path, decision="REJECT")

    hard: Dict[str, Tuple[bool, List[str]]] = {}
    g1_ok, g1_reasons = validate_valid_json_and_template(updated)
    hard["g1_valid_json_and_template"] = (g1_ok, g1_reasons)

    g2_ok, g2_reasons = validate_approved_sources(updated)
    hard["g2_approved_source_compliance"] = (g2_ok, g2_reasons)

    g3_ok, g3_reasons = validate_numeric_hygiene(updated)
    hard["g3_numeric_hygiene"] = (g3_ok, g3_reasons)

    g4_ok, g4_reasons = validate_core_macros(updated)
    hard["g4_core_macro_sanity"] = (g4_ok, g4_reasons)

    g5_ok, g5_reasons = validate_ontology(updated)
    hard["g5_ontology_honesty"] = (g5_ok, g5_reasons)

    g6_ok, g6_reasons, required_languages = validate_multilingual(updated)
    hard["g6_multilingual_naming_compliance"] = (g6_ok, g6_reasons)

    result.hard_gates = {k: v[0] for k, v in hard.items()}

    criteria_eval = {}
    scorers = [
        ("q1_food_identity", score_q1(updated, g6_ok)),
        ("q2_core_macro_completeness_and_source_integrity", score_q2(updated, g2_ok, g4_ok)),
        ("q3_secondary_nutrient_completeness", score_q3(updated)),
        ("q4_glycemic_completeness", score_q4(updated, g2_ok)),
        ("q5_ontological_alignment", score_q5(updated)),
        ("q6_llm_ready_semantics", score_q6(updated)),
        ("q7_health_context", score_q7(updated)),
        ("q8_aliases_and_multilingual_synonym_coverage", score_q8(updated, required_languages)),
    ]
    score_total = 0
    for key, (passed, notes) in scorers:
        criteria_eval[key] = {"pass": passed, "notes": notes}
        if passed:
            score_total += 1
    result.criteria = criteria_eval
    result.score_total = score_total

    hard_failures = []
    for gate_name, (passed, reasons) in hard.items():
        if not passed:
            if reasons:
                for reason in reasons:
                    hard_failures.append(f"{gate_name}: {reason}")
            else:
                hard_failures.append(f"{gate_name}: failed")

    criterion_failures = []
    for key, entry in criteria_eval.items():
        if not entry["pass"]:
            criterion_failures.append(f"{key}: {entry['notes']}")

    if hard_failures:
        decision = "REJECT"
        failure_reasons = hard_failures + criterion_failures
    elif score_total == 8:
        decision = "PASS"
        failure_reasons = []
    else:
        decision = "FAIL"
        failure_reasons = criterion_failures

    result.decision = decision
    result.failure_reasons = failure_reasons
    result.reviewer_summary = build_reviewer_summary(decision, score_total, failure_reasons)

    # Update metadata / QC blocks on valid JSON dicts.
    meta = updated.setdefault("veritas_meta", {})
    qc = updated.setdefault("veritas_qc", {})
    if isinstance(meta, dict):
        meta["generated_at"] = now_iso()
        meta["output_status"] = "pass" if decision == "PASS" else "fail"
    if isinstance(qc, dict):
        qc["final_decision"] = decision
        qc["hard_gates"] = result.hard_gates
        qc["score_total"] = score_total
        qc["score_max"] = 8
        qc["criteria"] = criteria_eval
        qc["failure_reasons"] = failure_reasons
        existing_actions = qc.get("improvement_actions_taken")
        if not isinstance(existing_actions, list):
            existing_actions = []
        if "Validated against VERITAS_SCORING_MODEL_v2.md" not in existing_actions:
            existing_actions.append("Validated against VERITAS_SCORING_MODEL_v2.md")
        qc["improvement_actions_taken"] = existing_actions
        qc["reviewer_summary"] = result.reviewer_summary

    return updated, result


def build_reviewer_summary(decision: str, score_total: int, failure_reasons: Sequence[str]) -> str:
    if decision == "PASS":
        return f"PASS — all hard gates passed and the record scored {score_total}/8."
    if decision == "FAIL":
        if failure_reasons:
            return f"FAIL — hard gates passed but the record scored {score_total}/8. Primary issues: {'; '.join(failure_reasons[:3])}."
        return f"FAIL — hard gates passed but the record scored {score_total}/8."
    if failure_reasons:
        return f"REJECT — one or more hard gates failed. Primary issues: {'; '.join(failure_reasons[:3])}."
    return "REJECT — one or more hard gates failed."


def target_path_for_decision(path: Path, decision: str) -> Path:
    stem = path.stem
    stem = re.sub(r"_(pass|fail)$", "", stem, flags=re.IGNORECASE)
    suffix = "_pass" if decision == "PASS" else "_fail"
    return path.with_name(f"{stem}{suffix}{path.suffix}")


def rename_or_keep(path: Path, target: Path, rename_enabled: bool) -> Tuple[Path, bool]:
    if not rename_enabled or path == target:
        return target if rename_enabled else path, False
    if target.exists() and target != path:
        target.unlink()
    path.replace(target)
    return target, True


def process_json_file(path: Path, rename_enabled: bool = True) -> ValidationResult:
    try:
        data = read_json(path)
    except json.JSONDecodeError as exc:
        target = target_path_for_decision(path, "FAIL")
        final_path, renamed = rename_or_keep(path, target, rename_enabled)
        return ValidationResult(
            original_path=path,
            final_path=final_path,
            decision="REJECT",
            score_total=0,
            hard_gates={"g1_valid_json_and_template": False},
            failure_reasons=[f"invalid JSON: {exc.msg} at line {exc.lineno}, column {exc.colno}"],
            reviewer_summary=f"REJECT — invalid JSON: {exc.msg} at line {exc.lineno}, column {exc.colno}.",
            renamed=renamed,
            updated_json=False,
            parse_error=str(exc),
        )

    if not isinstance(data, dict):
        target = target_path_for_decision(path, "FAIL")
        final_path = target
        result = ValidationResult(
            original_path=path,
            final_path=final_path,
            decision="REJECT",
            score_total=0,
            hard_gates={"g1_valid_json_and_template": False},
            failure_reasons=["top-level JSON value must be an object"],
            reviewer_summary="REJECT — top-level JSON value must be an object.",
        )
        if rename_enabled:
            final_path, renamed = rename_or_keep(path, target, True)
            result.final_path = final_path
            result.renamed = renamed
        return result

    updated, result = evaluate_record(data, path)
    target = target_path_for_decision(path, result.decision)
    if isinstance(updated.get("veritas_meta"), dict):
        updated["veritas_meta"]["output_file_path"] = str(target)
    tmp_path = path.with_name(path.name + ".tmp")
    write_json(tmp_path, updated)
    result.updated_json = True
    final_path, renamed = rename_or_keep(tmp_path, target, True)
    if rename_enabled:
        # If original path differs from final target, remove original after replacing temp.
        if path.exists() and path != final_path:
            path.unlink()
        result.final_path = final_path
        result.renamed = True
    else:
        # no-rename mode: keep original path, replace original with updated contents.
        if path.exists():
            path.unlink()
        final_path = rename_or_keep(tmp_path, path, True)[0]
        result.final_path = final_path
        result.renamed = False
    return result


def iter_json_files(target: Path, recursive: bool) -> Iterable[Path]:
    if target.is_file() and target.suffix.lower() == ".json":
        yield target
        return
    if target.is_dir():
        pattern = "**/*.json" if recursive else "*.json"
        for path in sorted(target.glob(pattern)):
            if path.is_file():
                yield path


def print_result(result: ValidationResult) -> None:
    label = result.decision
    path_msg = f"{result.original_path} -> {result.final_path}" if result.renamed or result.original_path != result.final_path else str(result.final_path)
    print(f"[{label}] {path_msg} | score {result.score_total}/{result.score_max}")
    if result.failure_reasons:
        for reason in result.failure_reasons[:6]:
            print(f"  - {reason}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Validate Veritas v2 JSON and rename to _pass.json or _fail.json")
    parser.add_argument("target", help="JSON file or folder to validate")
    parser.add_argument("--recursive", action="store_true", help="When target is a folder, scan recursively")
    parser.add_argument("--no-rename", action="store_true", help="Validate and update JSON in place without renaming")
    args = parser.parse_args(argv)

    target = Path(args.target).expanduser().resolve()
    if not target.exists():
        print(f"Target not found: {target}", file=sys.stderr)
        return 2

    files = list(iter_json_files(target, recursive=args.recursive))
    if not files:
        print("No JSON files found.", file=sys.stderr)
        return 2

    results = [process_json_file(path, rename_enabled=not args.no_rename) for path in files]
    for result in results:
        print_result(result)

    passed = sum(r.decision == "PASS" for r in results)
    failed = sum(r.decision == "FAIL" for r in results)
    rejected = sum(r.decision == "REJECT" for r in results)
    print(f"\nSummary: PASS={passed} FAIL={failed} REJECT={rejected} TOTAL={len(results)}")
    return 0 if failed == 0 and rejected == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
