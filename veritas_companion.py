#!/usr/bin/env python3
"""
Veritas companion runner

Orchestrates the end-to-end loop:
    input JSON -> prompt package -> external LLM runner -> draft result JSON
    -> Veritas validation -> remediation prompt -> repeat until PASS or max iterations
    -> final *_pass.json / *_fail.json in a separate output folder.

This script is intentionally provider-agnostic. It does not hardcode OpenAI,
Ollama, Gemini, Claude, etc. Instead, you supply an external command template
with --llm-cmd.

Examples
--------
Single file, prompt-only (manual run):
    python veritas_companion.py input_folder/001_food.json --prompt-only

Single file, external runner writes to draft file:
    python veritas_companion.py input_folder/001_food.json \
      --output-folder output_folder \
      --llm-cmd 'python claude_runner.py --prompt-file "{prompt_file}" --output-file "{draft_file}"'

Folder mode:
    python veritas_companion.py ./input_folder --recursive \
      --output-folder ./output_folder \
      --llm-cmd 'python gemini_runner.py --prompt-file "{prompt_file}" --output-file "{draft_file}"'
"""
from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence


DEFAULT_MAX_ITERS = 5


@dataclass
class IterationLog:
    iteration: int
    prompt_file: str
    draft_file: str
    feedback_file: str
    llm_exit_code: Optional[int]
    used_stdout_fallback: bool
    decision: str
    score_total: int
    score_max: int
    failure_reasons: List[str]
    reviewer_summary: str


@dataclass
class FileRunSummary:
    input_file: str
    final_file: str
    final_decision: str
    iterations_used: int
    pass_status: bool
    run_dir: str
    logs: List[IterationLog]


@dataclass
class OutputPlan:
    output_dir: Path
    final_pass_file: Path
    final_fail_file: Path
    run_dir: Path


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")


def load_module(module_path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, str(module_path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load module from {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


class SafeDict(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def render_placeholders(template: str, mapping: Dict[str, str]) -> str:
    out = template
    for key, value in mapping.items():
        out = out.replace("{" + key + "}", value)
    return out


def strip_status_suffix(stem: str) -> str:
    lowered = stem.lower()
    if lowered.endswith("_pass"):
        return stem[:-5]
    if lowered.endswith("_fail"):
        return stem[:-5]
    return stem


def iter_input_json_files(target: Path, recursive: bool) -> Iterable[Path]:
    if target.is_file() and target.suffix.lower() == ".json":
        yield target
        return
    if not target.is_dir():
        return
    pattern = "**/*.json" if recursive else "*.json"
    for path in sorted(target.glob(pattern)):
        if not path.is_file():
            continue
        posix = path.as_posix()
        if "/.veritas_runs/" in posix:
            continue
        if path.stem.lower().endswith("_pass") or path.stem.lower().endswith("_fail"):
            continue
        yield path


def determine_input_root(target: Path) -> Path:
    if target.is_file():
        return target.parent
    return target


def determine_output_root(target: Path, output_folder: Optional[Path]) -> Path:
    if output_folder is not None:
        return output_folder.expanduser().resolve()
    parent = target.parent if target.is_file() else target.parent
    return (parent / "output_folder").resolve()


def relative_parent(input_file: Path, input_root: Path) -> Path:
    try:
        rel_parent = input_file.parent.resolve().relative_to(input_root.resolve())
        return rel_parent
    except Exception:
        return Path()


def build_output_plan(input_file: Path, input_root: Path, output_root: Path) -> OutputPlan:
    clean_stem = strip_status_suffix(input_file.stem)
    rel_parent = relative_parent(input_file, input_root)
    output_dir = (output_root / rel_parent).resolve()
    run_dir = (output_root / ".veritas_runs" / rel_parent / clean_stem).resolve()
    return OutputPlan(
        output_dir=output_dir,
        final_pass_file=output_dir / f"{clean_stem}_pass.json",
        final_fail_file=output_dir / f"{clean_stem}_fail.json",
        run_dir=run_dir,
    )


def build_prompt_package(
    *,
    generic_prompt_template: str,
    project_md: str,
    scoring_md: str,
    result_template_text: str,
    input_file: Path,
    input_json_text: str,
    previous_draft_text: Optional[str] = None,
    previous_feedback_text: Optional[str] = None,
    iteration: int,
    max_iterations: int,
) -> str:
    mapping = {
        "INPUT_FILE_PATH": input_file.as_posix(),
        "INPUT_FOLDER": input_file.parent.as_posix() or ".",
        "INPUT_STEM": strip_status_suffix(input_file.stem),
        "INPUT_JSON_CONTENT": input_json_text,
    }
    base = render_placeholders(generic_prompt_template, mapping)

    sections = [
        base,
        "\n\n--- BEGIN CONTROL FILE: PROJECT_VERITAS_Ver2.md ---\n",
        project_md,
        "\n--- END CONTROL FILE: PROJECT_VERITAS_Ver2.md ---\n",
        "\n--- BEGIN CONTROL FILE: VERITAS_SCORING_MODEL_v2.md ---\n",
        scoring_md,
        "\n--- END CONTROL FILE: VERITAS_SCORING_MODEL_v2.md ---\n",
        "\n--- BEGIN CONTROL FILE: RESULT_template.json ---\n",
        result_template_text,
        "\n--- END CONTROL FILE: RESULT_template.json ---\n",
        f"\n--- RUNTIME CONTEXT ---\nIteration: {iteration} of {max_iterations}\n",
    ]

    if previous_feedback_text:
        sections.extend([
            "\n--- VALIDATOR FEEDBACK FROM PREVIOUS ITERATION ---\n",
            previous_feedback_text,
            "\n--- END VALIDATOR FEEDBACK ---\n",
        ])

    if previous_draft_text:
        sections.extend([
            "\n--- PREVIOUS RESULT JSON DRAFT ---\n",
            previous_draft_text,
            "\n--- END PREVIOUS RESULT JSON DRAFT ---\n",
            (
                "\nRevise the previous draft instead of starting over. Preserve correct sections, "
                "repair only the failing hard gates / criteria, and return only the final RESULT JSON.\n"
            ),
        ])

    return "".join(sections)


def run_llm_command(
    cmd_template: str,
    placeholders: Dict[str, str],
    prompt_text: str,
    draft_file: Path,
    stdout_log: Path,
    stderr_log: Path,
    timeout_seconds: int,
) -> tuple[Optional[int], bool]:
    rendered_cmd = cmd_template.format_map(SafeDict(placeholders))
    proc = subprocess.run(
        ["bash", "-lc", rendered_cmd],
        input=prompt_text,
        text=True,
        capture_output=True,
        timeout=timeout_seconds,
    )
    write_text(stdout_log, proc.stdout or "")
    write_text(stderr_log, proc.stderr or "")

    used_stdout_fallback = False
    if not draft_file.exists() and (proc.stdout or "").strip():
        write_text(draft_file, proc.stdout)
        used_stdout_fallback = True

    return proc.returncode, used_stdout_fallback


def build_feedback_payload(result: Any, input_file: Path, draft_file: Path, iteration: int) -> Dict[str, Any]:
    return {
        "input_file": input_file.as_posix(),
        "draft_file": draft_file.as_posix(),
        "iteration": iteration,
        "validated_at": now_iso(),
        "decision": result.decision,
        "score_total": result.score_total,
        "score_max": result.score_max,
        "hard_gates": result.hard_gates,
        "criteria": result.criteria,
        "failure_reasons": result.failure_reasons,
        "reviewer_summary": result.reviewer_summary,
        "parse_error": result.parse_error,
    }


def validate_draft_with_module(validator_module: Any, draft_file: Path) -> Any:
    try:
        data = validator_module.read_json(draft_file)
    except json.JSONDecodeError as exc:
        return validator_module.ValidationResult(
            original_path=draft_file,
            final_path=draft_file,
            decision="REJECT",
            score_total=0,
            hard_gates={"g1_valid_json_and_template": False},
            failure_reasons=[f"invalid JSON: {exc.msg} at line {exc.lineno}, column {exc.colno}"],
            reviewer_summary=f"REJECT — invalid JSON: {exc.msg} at line {exc.lineno}, column {exc.colno}.",
            renamed=False,
            updated_json=False,
            parse_error=str(exc),
        )
    except Exception as exc:
        return validator_module.ValidationResult(
            original_path=draft_file,
            final_path=draft_file,
            decision="REJECT",
            score_total=0,
            hard_gates={"g1_valid_json_and_template": False},
            failure_reasons=[f"could not read JSON: {exc}"],
            reviewer_summary="REJECT — the draft could not be read as JSON.",
            renamed=False,
            updated_json=False,
            parse_error=str(exc),
        )

    if not isinstance(data, dict):
        return validator_module.ValidationResult(
            original_path=draft_file,
            final_path=draft_file,
            decision="REJECT",
            score_total=0,
            hard_gates={"g1_valid_json_and_template": False},
            failure_reasons=["top-level JSON value must be an object"],
            reviewer_summary="REJECT — top-level JSON value must be an object.",
            renamed=False,
            updated_json=False,
        )

    updated, result = validator_module.evaluate_record(data, draft_file)
    validator_module.write_json(draft_file, updated)
    result.final_path = draft_file
    result.renamed = False
    result.updated_json = True
    return result


def load_result_template(template_path: Path) -> Dict[str, Any]:
    return read_json(template_path)


def build_stub_fail_result(
    template_obj: Dict[str, Any],
    *,
    input_file: Path,
    final_file: Path,
    raw_draft_path: Optional[Path],
    max_iterations: int,
    iterations_used: int,
    decision: str,
    failure_reasons: List[str],
    reviewer_summary: str,
) -> Dict[str, Any]:
    obj = copy.deepcopy(template_obj)
    meta = obj.setdefault("veritas_meta", {})
    qc = obj.setdefault("veritas_qc", {})
    meta["input_file_path"] = input_file.as_posix()
    meta["output_file_path"] = final_file.as_posix()
    meta["output_status"] = "pass" if decision == "PASS" else "fail"
    meta["max_iterations_allowed"] = max_iterations
    meta["iteration_count_used"] = iterations_used
    meta["generated_at"] = now_iso()
    if raw_draft_path is not None:
        meta["last_raw_draft_path"] = raw_draft_path.as_posix()
    obj["food_name"] = strip_status_suffix(input_file.stem).replace("_", " ")
    obj["confidence"] = "low"
    obj["confidence_reason"] = "No valid RESULT JSON could be finalized from the LLM output."
    obj["notes"] = (
        "Companion script produced a stub fail artifact because the LLM output was missing or not parseable as the RESULT schema. "
        + (f"Raw draft preserved at {raw_draft_path.as_posix()}." if raw_draft_path else "")
    )
    qc["final_decision"] = decision
    qc["hard_gates"] = qc.get("hard_gates", {
        "g1_valid_json_and_template": False,
        "g2_approved_source_compliance": False,
        "g3_numeric_hygiene": False,
        "g4_core_macro_sanity": False,
        "g5_ontology_honesty": False,
        "g6_multilingual_naming_compliance": False,
    })
    qc["score_total"] = 0
    qc["score_max"] = 8
    qc["failure_reasons"] = failure_reasons
    existing_actions = qc.get("improvement_actions_taken")
    if not isinstance(existing_actions, list):
        existing_actions = []
    existing_actions.append("Companion script generated stub fail artifact.")
    qc["improvement_actions_taken"] = existing_actions
    qc["reviewer_summary"] = reviewer_summary
    return obj


def remove_alternate_status_file(final_file: Path) -> None:
    stem = final_file.stem
    if stem.endswith("_pass"):
        alt = final_file.with_name(stem[:-5] + "_fail" + final_file.suffix)
    elif stem.endswith("_fail"):
        alt = final_file.with_name(stem[:-5] + "_pass" + final_file.suffix)
    else:
        return
    if alt.exists():
        alt.unlink()


def finalize_from_draft(
    *,
    template_obj: Dict[str, Any],
    draft_file: Optional[Path],
    final_file: Path,
    input_file: Path,
    decision: str,
    max_iterations: int,
    iterations_used: int,
    failure_reasons: List[str],
    reviewer_summary: str,
) -> None:
    final_file.parent.mkdir(parents=True, exist_ok=True)
    remove_alternate_status_file(final_file)
    if final_file.exists():
        final_file.unlink()

    if draft_file is None or not draft_file.exists():
        stub = build_stub_fail_result(
            template_obj,
            input_file=input_file,
            final_file=final_file,
            raw_draft_path=draft_file,
            max_iterations=max_iterations,
            iterations_used=iterations_used,
            decision=decision,
            failure_reasons=failure_reasons,
            reviewer_summary=reviewer_summary,
        )
        write_json(final_file, stub)
        return

    try:
        data = read_json(draft_file)
    except Exception:
        stub = build_stub_fail_result(
            template_obj,
            input_file=input_file,
            final_file=final_file,
            raw_draft_path=draft_file,
            max_iterations=max_iterations,
            iterations_used=iterations_used,
            decision=decision,
            failure_reasons=failure_reasons,
            reviewer_summary=reviewer_summary,
        )
        write_json(final_file, stub)
        return

    meta = data.setdefault("veritas_meta", {})
    qc = data.setdefault("veritas_qc", {})
    if isinstance(meta, dict):
        meta["input_file_path"] = input_file.as_posix()
        meta["output_file_path"] = final_file.as_posix()
        meta["output_status"] = "pass" if decision == "PASS" else "fail"
        meta["max_iterations_allowed"] = max_iterations
        meta["iteration_count_used"] = iterations_used
        meta["generated_at"] = now_iso()
        meta["run_directory"] = draft_file.parent.as_posix()
        meta["last_raw_draft_path"] = draft_file.as_posix()
    if isinstance(qc, dict):
        qc["final_decision"] = decision
        existing_actions = qc.get("improvement_actions_taken")
        if not isinstance(existing_actions, list):
            existing_actions = []
        if "Companion script finalized output artifact." not in existing_actions:
            existing_actions.append("Companion script finalized output artifact.")
        qc["improvement_actions_taken"] = existing_actions
        if failure_reasons:
            qc["failure_reasons"] = failure_reasons
        if reviewer_summary:
            qc["reviewer_summary"] = reviewer_summary
    write_json(final_file, data)


def process_one_file(
    *,
    input_file: Path,
    input_root: Path,
    output_root: Path,
    args: argparse.Namespace,
    validator_module: Any,
    project_md: str,
    scoring_md: str,
    result_template_text: str,
    generic_prompt_template: str,
    result_template_obj: Dict[str, Any],
) -> FileRunSummary:
    clean_stem = strip_status_suffix(input_file.stem)
    output_plan = build_output_plan(input_file, input_root, output_root)
    output_plan.run_dir.mkdir(parents=True, exist_ok=True)

    input_json_text = read_text(input_file)
    previous_draft_text: Optional[str] = None
    previous_feedback_text: Optional[str] = None
    logs: List[IterationLog] = []

    if args.prompt_only:
        prompt_text = build_prompt_package(
            generic_prompt_template=generic_prompt_template,
            project_md=project_md,
            scoring_md=scoring_md,
            result_template_text=result_template_text,
            input_file=input_file,
            input_json_text=input_json_text,
            previous_draft_text=None,
            previous_feedback_text=None,
            iteration=1,
            max_iterations=args.max_iterations,
        )
        prompt_file = output_plan.run_dir / f"{clean_stem}__iter01_prompt.md"
        write_text(prompt_file, prompt_text)
        final_file = output_plan.final_fail_file
        finalize_from_draft(
            template_obj=result_template_obj,
            draft_file=None,
            final_file=final_file,
            input_file=input_file,
            decision="FAIL",
            max_iterations=args.max_iterations,
            iterations_used=0,
            failure_reasons=["Prompt package generated only; no LLM command was executed."],
            reviewer_summary="FAIL — prompt-only mode does not execute the LLM.",
        )
        return FileRunSummary(
            input_file=input_file.as_posix(),
            final_file=final_file.as_posix(),
            final_decision="FAIL",
            iterations_used=0,
            pass_status=False,
            run_dir=output_plan.run_dir.as_posix(),
            logs=[],
        )

    if not args.llm_cmd:
        raise RuntimeError("--llm-cmd is required unless --prompt-only is used")

    final_result = None
    last_draft_file: Optional[Path] = None

    for iteration in range(1, args.max_iterations + 1):
        prompt_file = output_plan.run_dir / f"{clean_stem}__iter{iteration:02d}_prompt.md"
        draft_file = output_plan.run_dir / f"{clean_stem}__iter{iteration:02d}_draft.json"
        feedback_file = output_plan.run_dir / f"{clean_stem}__iter{iteration:02d}_feedback.json"
        stdout_log = output_plan.run_dir / f"{clean_stem}__iter{iteration:02d}_stdout.txt"
        stderr_log = output_plan.run_dir / f"{clean_stem}__iter{iteration:02d}_stderr.txt"

        prompt_text = build_prompt_package(
            generic_prompt_template=generic_prompt_template,
            project_md=project_md,
            scoring_md=scoring_md,
            result_template_text=result_template_text,
            input_file=input_file,
            input_json_text=input_json_text,
            previous_draft_text=previous_draft_text,
            previous_feedback_text=previous_feedback_text,
            iteration=iteration,
            max_iterations=args.max_iterations,
        )
        write_text(prompt_file, prompt_text)

        final_pass_file = output_plan.final_pass_file
        final_fail_file = output_plan.final_fail_file
        placeholders = {
            "input_file": input_file.as_posix(),
            "input_folder": (input_file.parent.as_posix() or "."),
            "input_stem": clean_stem,
            "run_dir": output_plan.run_dir.as_posix(),
            "iteration": str(iteration),
            "prompt_file": prompt_file.as_posix(),
            "draft_file": draft_file.as_posix(),
            "feedback_file": feedback_file.as_posix(),
            "project_md": args.project_md.as_posix(),
            "scoring_md": args.scoring_md.as_posix(),
            "result_template": args.result_template.as_posix(),
            "validator": args.validator.as_posix(),
            "output_root": output_root.as_posix(),
            "output_folder": output_plan.output_dir.as_posix(),
            "final_pass_file": final_pass_file.as_posix(),
            "final_fail_file": final_fail_file.as_posix(),
            "final_file": final_pass_file.as_posix(),
        }

        exit_code = None
        used_stdout_fallback = False
        try:
            exit_code, used_stdout_fallback = run_llm_command(
                args.llm_cmd,
                placeholders,
                prompt_text,
                draft_file,
                stdout_log,
                stderr_log,
                args.timeout,
            )
        except subprocess.TimeoutExpired:
            timeout_msg = f"LLM command timed out after {args.timeout} seconds."
            write_text(stderr_log, timeout_msg + "\n")
            if not draft_file.exists():
                write_text(draft_file, "")

        if not draft_file.exists():
            write_text(draft_file, "")

        validation_result = validate_draft_with_module(validator_module, draft_file)
        feedback_payload = build_feedback_payload(validation_result, input_file, draft_file, iteration)
        write_json(feedback_file, feedback_payload)

        logs.append(
            IterationLog(
                iteration=iteration,
                prompt_file=prompt_file.as_posix(),
                draft_file=draft_file.as_posix(),
                feedback_file=feedback_file.as_posix(),
                llm_exit_code=exit_code,
                used_stdout_fallback=used_stdout_fallback,
                decision=validation_result.decision,
                score_total=validation_result.score_total,
                score_max=validation_result.score_max,
                failure_reasons=list(validation_result.failure_reasons),
                reviewer_summary=validation_result.reviewer_summary,
            )
        )

        final_result = validation_result
        last_draft_file = draft_file

        if validation_result.decision == "PASS":
            break

        previous_draft_text = read_text(draft_file) if draft_file.exists() else None
        previous_feedback_text = json.dumps(feedback_payload, ensure_ascii=False, indent=2)

    assert final_result is not None
    final_file = output_plan.final_pass_file if final_result.decision == "PASS" else output_plan.final_fail_file
    finalize_from_draft(
        template_obj=result_template_obj,
        draft_file=last_draft_file,
        final_file=final_file,
        input_file=input_file,
        decision=final_result.decision,
        max_iterations=args.max_iterations,
        iterations_used=len(logs),
        failure_reasons=list(final_result.failure_reasons),
        reviewer_summary=final_result.reviewer_summary,
    )

    summary = FileRunSummary(
        input_file=input_file.as_posix(),
        final_file=final_file.as_posix(),
        final_decision=final_result.decision,
        iterations_used=len(logs),
        pass_status=final_result.decision == "PASS",
        run_dir=output_plan.run_dir.as_posix(),
        logs=logs,
    )
    write_json(output_plan.run_dir / f"{clean_stem}__summary.json", asdict(summary))
    return summary


def print_summary(summary: FileRunSummary) -> None:
    print(
        f"[{summary.final_decision}] {summary.input_file} -> {summary.final_file} "
        f"| iterations={summary.iterations_used} | run_dir={summary.run_dir}"
    )
    if summary.logs:
        last = summary.logs[-1]
        if last.failure_reasons:
            for reason in last.failure_reasons[:6]:
                print(f"  - {reason}")


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    default_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="Veritas companion runner for iterative LLM -> validate -> improve workflow")
    parser.add_argument("target", help="Input JSON file or folder")
    parser.add_argument("--output-folder", type=Path, help="Separate output folder for *_pass.json, *_fail.json, and .veritas_runs")
    parser.add_argument("--recursive", action="store_true", help="When target is a folder, scan recursively")
    parser.add_argument("--llm-cmd", help=(
        "External command template. Placeholders: {prompt_file}, {draft_file}, {feedback_file}, {input_file}, "
        "{input_folder}, {input_stem}, {iteration}, {run_dir}, {project_md}, {scoring_md}, {result_template}, "
        "{validator}, {output_root}, {output_folder}, {final_pass_file}, {final_fail_file}, {final_file}."
    ))
    parser.add_argument("--prompt-only", action="store_true", help="Write the prompt package and a stub _fail.json without executing any LLM command")
    parser.add_argument("--max-iterations", type=int, default=DEFAULT_MAX_ITERS, help="Maximum improve-and-score loops (default: 5)")
    parser.add_argument("--timeout", type=int, default=900, help="Per-iteration LLM command timeout in seconds (default: 900)")
    parser.add_argument("--project-md", type=Path, default=default_dir / "PROJECT_VERITAS_Ver2.md")
    parser.add_argument("--scoring-md", type=Path, default=default_dir / "VERITAS_SCORING_MODEL_v2.md")
    parser.add_argument("--result-template", type=Path, default=default_dir / "RESULT_template.json")
    parser.add_argument("--prompt-template", type=Path, default=default_dir / "VERITAS_GENERIC_EXECUTION_PROMPT.md")
    parser.add_argument("--validator", type=Path, default=default_dir / "veritas_validator.py")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    target = Path(args.target).expanduser().resolve()
    if not target.exists():
        print(f"Target not found: {target}", file=sys.stderr)
        return 2

    for attr in ["project_md", "scoring_md", "result_template", "prompt_template", "validator"]:
        path = getattr(args, attr)
        if not Path(path).exists():
            print(f"Required file not found: {path}", file=sys.stderr)
            return 2
        setattr(args, attr, Path(path).expanduser().resolve())

    if args.max_iterations < 1:
        print("--max-iterations must be >= 1", file=sys.stderr)
        return 2

    input_root = determine_input_root(target)
    output_root = determine_output_root(target, args.output_folder)
    output_root.mkdir(parents=True, exist_ok=True)

    project_md = read_text(args.project_md)
    scoring_md = read_text(args.scoring_md)
    result_template_text = read_text(args.result_template)
    generic_prompt_template = read_text(args.prompt_template)
    result_template_obj = load_result_template(args.result_template)
    validator_module = load_module(args.validator, "veritas_validator_module")

    files = list(iter_input_json_files(target, recursive=args.recursive))
    if not files:
        print("No input JSON files found.", file=sys.stderr)
        return 2

    summaries: List[FileRunSummary] = []
    for input_file in files:
        try:
            summary = process_one_file(
                input_file=input_file,
                input_root=input_root,
                output_root=output_root,
                args=args,
                validator_module=validator_module,
                project_md=project_md,
                scoring_md=scoring_md,
                result_template_text=result_template_text,
                generic_prompt_template=generic_prompt_template,
                result_template_obj=result_template_obj,
            )
            summaries.append(summary)
            print_summary(summary)
        except Exception as exc:
            output_plan = build_output_plan(input_file, input_root, output_root)
            clean_stem = strip_status_suffix(input_file.stem)
            final_file = output_plan.final_fail_file
            finalize_from_draft(
                template_obj=result_template_obj,
                draft_file=None,
                final_file=final_file,
                input_file=input_file,
                decision="REJECT",
                max_iterations=args.max_iterations,
                iterations_used=0,
                failure_reasons=[f"Companion script error: {exc}"],
                reviewer_summary="REJECT — companion script failed before a valid result could be finalized.",
            )
            summary = FileRunSummary(
                input_file=input_file.as_posix(),
                final_file=final_file.as_posix(),
                final_decision="REJECT",
                iterations_used=0,
                pass_status=False,
                run_dir=output_plan.run_dir.as_posix(),
                logs=[],
            )
            summaries.append(summary)
            print_summary(summary)

    passed = sum(1 for s in summaries if s.final_decision == "PASS")
    failed = sum(1 for s in summaries if s.final_decision == "FAIL")
    rejected = sum(1 for s in summaries if s.final_decision == "REJECT")
    print(f"\nSummary: PASS={passed} FAIL={failed} REJECT={rejected} TOTAL={len(summaries)}")
    print(f"Output root: {output_root.as_posix()}")
    return 0 if failed == 0 and rejected == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
