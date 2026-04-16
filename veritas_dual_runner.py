#!/usr/bin/env python3
"""
Veritas Dual-LLM Runner

Orchestrates a three-iteration quality loop using two LLMs:
    Iteration 1: Claude (generate full output)
    Iteration 2: Gemini (repair if Claude failed)
    Iteration 3: Claude (final attempt if still failed)

Runs up to 5 files in parallel. Collects per-file metrics:
    - wall-clock time
    - input/output tokens
    - estimated API cost
    - iterations used
    - final decision (PASS/FAIL/REJECT)
    - which LLM achieved the pass

Usage:
    python veritas_dual_runner.py ./to_test --output-folder ./output
    python veritas_dual_runner.py ./to_test --output-folder ./output --workers 5
"""
from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

# ── Pricing (USD per 1M tokens, as of Apr 2025) ─────────────────────────────
CLAUDE_SONNET_PRICE = {"input": 3.00, "output": 15.00}   # claude-sonnet-4-6
GEMINI_FLASH_PRICE  = {"input": 0.15, "output": 0.60}    # gemini-2.5-flash

# ── LLM runner scripts (relative to this file) ───────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
CLAUDE_RUNNER  = SCRIPT_DIR / "claude_runner.py"
GEMINI_RUNNER  = SCRIPT_DIR / "gemini_runner.py"

# ── Control files ─────────────────────────────────────────────────────────────
PROJECT_MD       = SCRIPT_DIR / "PROJECT_VERITAS_Ver2.md"
SCORING_MD       = SCRIPT_DIR / "VERITAS_SCORING_MODEL_v2.md"
RESULT_TEMPLATE  = SCRIPT_DIR / "RESULT_template.json"
PROMPT_TEMPLATE  = SCRIPT_DIR / "VERITAS_GENERIC_EXECUTION_PROMPT.md"
VALIDATOR_PY     = SCRIPT_DIR / "veritas_validator.py"

# ── Anthropic API key ─────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")


# ─────────────────────────────────────────────────────────────────────────────
# Data classes
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class IterationMetrics:
    iteration: int
    llm: str                    # "claude" | "gemini"
    wall_time_s: float
    input_tokens: int
    output_tokens: int
    cost_usd: float
    decision: str               # PASS | FAIL | REJECT
    score_total: int
    failure_reasons: List[str]


@dataclass
class FileMetrics:
    input_file: str
    final_decision: str
    iterations_used: int
    pass_iteration: Optional[int]   # which iteration achieved PASS (None if failed)
    pass_llm: Optional[str]         # "claude" | "gemini" | None
    total_wall_time_s: float
    total_input_tokens: int
    total_output_tokens: int
    total_cost_usd: float
    iterations: List[IterationMetrics] = field(default_factory=list)
    error: Optional[str] = None


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

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


def estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token."""
    return max(1, len(text) // 4)


def compute_cost(input_tokens: int, output_tokens: int, pricing: Dict[str, float]) -> float:
    return (input_tokens * pricing["input"] + output_tokens * pricing["output"]) / 1_000_000


def strip_status_suffix(stem: str) -> str:
    lower = stem.lower()
    if lower.endswith("_pass"):
        return stem[:-5]
    if lower.endswith("_fail"):
        return stem[:-5]
    return stem


def iter_input_json_files(target: Path, recursive: bool = False):
    if target.is_file() and target.suffix.lower() == ".json":
        yield target
        return
    pattern = "**/*.json" if recursive else "*.json"
    for path in sorted(target.glob(pattern)):
        if not path.is_file():
            continue
        posix = path.as_posix()
        if "/.veritas_runs/" in posix or "/__MACOSX/" in posix:
            continue
        if path.stem.lower().endswith("_pass") or path.stem.lower().endswith("_fail"):
            continue
        yield path


def load_validator():
    spec = importlib.util.spec_from_file_location("veritas_validator", str(VALIDATOR_PY))
    module = importlib.util.module_from_spec(spec)
    sys.modules["veritas_validator"] = module
    spec.loader.exec_module(module)
    return module


# ─────────────────────────────────────────────────────────────────────────────
# Prompt builder
# ─────────────────────────────────────────────────────────────────────────────

def build_prompt(
    input_file: Path,
    input_json_text: str,
    iteration: int,
    previous_draft_text: Optional[str] = None,
    previous_feedback_text: Optional[str] = None,
) -> str:
    generic = read_text(PROMPT_TEMPLATE)
    project_md = read_text(PROJECT_MD)
    scoring_md = read_text(SCORING_MD)
    result_template = read_text(RESULT_TEMPLATE)

    clean_stem = strip_status_suffix(input_file.stem)
    mapping = {
        "INPUT_FILE_PATH": input_file.as_posix(),
        "INPUT_FOLDER": input_file.parent.as_posix() or ".",
        "INPUT_STEM": clean_stem,
        "INPUT_JSON_CONTENT": input_json_text,
    }
    base = generic
    for key, value in mapping.items():
        base = base.replace("{" + key + "}", value)

    sections = [
        base,
        "\n\n--- BEGIN CONTROL FILE: PROJECT_VERITAS_Ver2.md ---\n",
        project_md,
        "\n--- END CONTROL FILE: PROJECT_VERITAS_Ver2.md ---\n",
        "\n--- BEGIN CONTROL FILE: VERITAS_SCORING_MODEL_v2.md ---\n",
        scoring_md,
        "\n--- END CONTROL FILE: VERITAS_SCORING_MODEL_v2.md ---\n",
        "\n--- BEGIN CONTROL FILE: RESULT_template.json ---\n",
        result_template,
        "\n--- END CONTROL FILE: RESULT_template.json ---\n",
        f"\n--- RUNTIME CONTEXT ---\nIteration: {iteration} of 3\n",
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


# ─────────────────────────────────────────────────────────────────────────────
# LLM runner invocation
# ─────────────────────────────────────────────────────────────────────────────

def run_llm(
    llm: str,
    prompt_file: Path,
    draft_file: Path,
    stdout_log: Path,
    stderr_log: Path,
    timeout: int = 900,
) -> Tuple[int, str]:
    """Run the appropriate LLM runner. Returns (exit_code, raw_output_text)."""
    env = os.environ.copy()
    env["ANTHROPIC_API_KEY"] = ANTHROPIC_API_KEY

    if llm == "claude":
        cmd = [
            sys.executable, str(CLAUDE_RUNNER),
            "--prompt-file", str(prompt_file),
            "--output-file", str(draft_file),
            "--model", "claude-sonnet-4-6",
            "--max-tokens", "16000",
            "--temperature", "0.2",
        ]
    elif llm == "gemini":
        cmd = [
            sys.executable, str(GEMINI_RUNNER),
            "--prompt-file", str(prompt_file),
            "--output-file", str(draft_file),
            "--model", "gemini-2.5-flash",
            "--max-tokens", "16000",
            "--temperature", "0.2",
        ]
    else:
        raise ValueError(f"Unknown LLM: {llm}")

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
        write_text(stdout_log, proc.stdout or "")
        write_text(stderr_log, proc.stderr or "")
        return proc.returncode, proc.stdout or ""
    except subprocess.TimeoutExpired:
        write_text(stderr_log, f"LLM command timed out after {timeout}s\n")
        return -1, ""


# ─────────────────────────────────────────────────────────────────────────────
# Per-file processing
# ─────────────────────────────────────────────────────────────────────────────

LLM_SEQUENCE = ["claude", "gemini", "claude"]  # iterations 1, 2, 3


def process_one_file(
    input_file: Path,
    output_root: Path,
    timeout: int = 900,
) -> FileMetrics:
    """Process a single input JSON file through the dual-LLM loop."""
    t_total_start = time.time()
    clean_stem = strip_status_suffix(input_file.stem)

    # Output paths
    output_dir = output_root
    run_dir = output_root / ".veritas_runs" / clean_stem
    run_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    final_pass_file = output_dir / f"{clean_stem}_pass.json"
    final_fail_file = output_dir / f"{clean_stem}_fail.json"

    # Load validator
    validator = load_validator()

    # Read input
    try:
        input_json_text = read_text(input_file)
        json.loads(input_json_text)  # validate it's valid JSON
    except Exception as exc:
        elapsed = time.time() - t_total_start
        return FileMetrics(
            input_file=input_file.as_posix(),
            final_decision="REJECT",
            iterations_used=0,
            pass_iteration=None,
            pass_llm=None,
            total_wall_time_s=elapsed,
            total_input_tokens=0,
            total_output_tokens=0,
            total_cost_usd=0.0,
            error=f"Could not read input JSON: {exc}",
        )

    iteration_metrics: List[IterationMetrics] = []
    previous_draft_text: Optional[str] = None
    previous_feedback_text: Optional[str] = None
    final_decision = "FAIL"
    pass_iteration: Optional[int] = None
    pass_llm: Optional[str] = None
    last_draft_file: Optional[Path] = None

    for iteration, llm in enumerate(LLM_SEQUENCE, start=1):
        t_iter_start = time.time()

        prompt_file  = run_dir / f"{clean_stem}__iter{iteration:02d}_prompt.md"
        draft_file   = run_dir / f"{clean_stem}__iter{iteration:02d}_draft.json"
        feedback_file = run_dir / f"{clean_stem}__iter{iteration:02d}_feedback.json"
        stdout_log   = run_dir / f"{clean_stem}__iter{iteration:02d}_stdout.txt"
        stderr_log   = run_dir / f"{clean_stem}__iter{iteration:02d}_stderr.txt"

        # Build prompt
        prompt_text = build_prompt(
            input_file=input_file,
            input_json_text=input_json_text,
            iteration=iteration,
            previous_draft_text=previous_draft_text,
            previous_feedback_text=previous_feedback_text,
        )
        write_text(prompt_file, prompt_text)

        # Count input tokens
        input_tokens = estimate_tokens(prompt_text)

        # Run LLM
        exit_code, _ = run_llm(llm, prompt_file, draft_file, stdout_log, stderr_log, timeout)

        # If draft not written, create empty file
        if not draft_file.exists():
            write_text(draft_file, "")

        # Count output tokens
        output_text = read_text(draft_file) if draft_file.exists() else ""
        output_tokens = estimate_tokens(output_text)

        # Compute cost
        pricing = CLAUDE_SONNET_PRICE if llm == "claude" else GEMINI_FLASH_PRICE
        cost = compute_cost(input_tokens, output_tokens, pricing)

        # Validate
        try:
            data = read_json(draft_file)
            updated_data, val_result = validator.evaluate_record(data, draft_file)
        except json.JSONDecodeError as exc:
            val_result = validator.ValidationResult(
                original_path=draft_file,
                final_path=draft_file,
                decision="REJECT",
                score_total=0,
                score_max=8,
                hard_gates={"g1_valid_json_and_template": False},
                criteria={},
                failure_reasons=[f"invalid JSON: {exc}"],
                reviewer_summary=f"REJECT — invalid JSON from {llm}.",
                renamed=False,
                updated_json=None,
                parse_error=str(exc),
            )
            updated_data = None
        except Exception as exc:
            val_result = validator.ValidationResult(
                original_path=draft_file,
                final_path=draft_file,
                decision="REJECT",
                score_total=0,
                score_max=8,
                hard_gates={},
                criteria={},
                failure_reasons=[f"validation error: {exc}"],
                reviewer_summary=f"REJECT — validation error: {exc}",
                renamed=False,
                updated_json=None,
                parse_error=str(exc),
            )
            updated_data = None

        # Write feedback
        feedback_payload = {
            "input_file": input_file.as_posix(),
            "draft_file": draft_file.as_posix(),
            "iteration": iteration,
            "llm": llm,
            "validated_at": now_iso(),
            "decision": val_result.decision,
            "score_total": val_result.score_total,
            "score_max": val_result.score_max,
            "hard_gates": val_result.hard_gates,
            "criteria": val_result.criteria if hasattr(val_result, 'criteria') else {},
            "failure_reasons": val_result.failure_reasons,
            "reviewer_summary": val_result.reviewer_summary,
        }
        write_json(feedback_file, feedback_payload)

        elapsed_iter = time.time() - t_iter_start
        iter_m = IterationMetrics(
            iteration=iteration,
            llm=llm,
            wall_time_s=round(elapsed_iter, 2),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=round(cost, 6),
            decision=val_result.decision,
            score_total=val_result.score_total,
            failure_reasons=list(val_result.failure_reasons),
        )
        iteration_metrics.append(iter_m)
        last_draft_file = draft_file
        final_decision = val_result.decision

        if val_result.decision == "PASS":
            pass_iteration = iteration
            pass_llm = llm
            # Write final pass file with updated QC block
            if updated_data is not None:
                # Stamp veritas_meta
                meta = updated_data.setdefault("veritas_meta", {})
                if isinstance(meta, dict):
                    meta["input_file_path"] = input_file.as_posix()
                    meta["output_file_path"] = final_pass_file.as_posix()
                    meta["output_status"] = "pass"
                    meta["iteration_count_used"] = iteration
                    meta["generated_at"] = now_iso()
                write_json(final_pass_file, updated_data)
            break

        # Prepare for next iteration
        previous_draft_text = output_text if output_text.strip() else None
        previous_feedback_text = json.dumps(feedback_payload, ensure_ascii=False, indent=2)

    # If not passed, write fail file
    if final_decision != "PASS":
        if last_draft_file and last_draft_file.exists():
            try:
                data = read_json(last_draft_file)
                if isinstance(data, dict):
                    meta = data.setdefault("veritas_meta", {})
                    if isinstance(meta, dict):
                        meta["input_file_path"] = input_file.as_posix()
                        meta["output_file_path"] = final_fail_file.as_posix()
                        meta["output_status"] = "fail"
                        meta["iteration_count_used"] = len(iteration_metrics)
                        meta["generated_at"] = now_iso()
                    write_json(final_fail_file, data)
                else:
                    write_text(final_fail_file, read_text(last_draft_file))
            except Exception:
                write_text(final_fail_file, read_text(last_draft_file) if last_draft_file.exists() else "")
        else:
            write_json(final_fail_file, {"error": "no draft produced", "input_file": input_file.as_posix()})

    total_elapsed = time.time() - t_total_start
    total_input  = sum(m.input_tokens  for m in iteration_metrics)
    total_output = sum(m.output_tokens for m in iteration_metrics)
    total_cost   = sum(m.cost_usd      for m in iteration_metrics)

    return FileMetrics(
        input_file=input_file.as_posix(),
        final_decision=final_decision,
        iterations_used=len(iteration_metrics),
        pass_iteration=pass_iteration,
        pass_llm=pass_llm,
        total_wall_time_s=round(total_elapsed, 2),
        total_input_tokens=total_input,
        total_output_tokens=total_output,
        total_cost_usd=round(total_cost, 6),
        iterations=iteration_metrics,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Worker wrapper (needed for ProcessPoolExecutor pickling)
# ─────────────────────────────────────────────────────────────────────────────

def _worker(args: Tuple[str, str, int]) -> Dict[str, Any]:
    input_file_str, output_root_str, timeout = args
    result = process_one_file(
        input_file=Path(input_file_str),
        output_root=Path(output_root_str),
        timeout=timeout,
    )
    return asdict(result)


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Veritas Dual-LLM Runner (Claude → Gemini → Claude)"
    )
    parser.add_argument("target", help="Input JSON file or folder")
    parser.add_argument("--output-folder", type=Path, required=True,
                        help="Output folder for _pass.json, _fail.json, and .veritas_runs/")
    parser.add_argument("--workers", type=int, default=5,
                        help="Number of parallel workers (default: 5)")
    parser.add_argument("--timeout", type=int, default=900,
                        help="Per-iteration LLM timeout in seconds (default: 900)")
    parser.add_argument("--recursive", action="store_true",
                        help="Scan target folder recursively")
    return parser.parse_args(argv)


def print_file_result(m: Dict[str, Any]) -> None:
    decision = m["final_decision"]
    fname = Path(m["input_file"]).name
    iters = m["iterations_used"]
    t = m["total_wall_time_s"]
    cost = m["total_cost_usd"]
    pass_llm = m.get("pass_llm") or "-"
    print(f"  [{decision}] {fname} | iters={iters} | {t:.1f}s | ${cost:.4f} | passed_by={pass_llm}")
    if m.get("error"):
        print(f"    ERROR: {m['error']}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    target = Path(args.target).expanduser().resolve()
    output_root = args.output_folder.expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    files = list(iter_input_json_files(target, recursive=args.recursive))
    if not files:
        print("No input JSON files found.", file=sys.stderr)
        return 2

    print(f"\nVeritas Dual-LLM Runner")
    print(f"  Target:  {target}")
    print(f"  Output:  {output_root}")
    print(f"  Files:   {len(files)}")
    print(f"  Workers: {args.workers}")
    print(f"  LLM seq: Claude → Gemini → Claude (max 3 iterations)")
    print()

    t_run_start = time.time()
    worker_args = [(str(f), str(output_root), args.timeout) for f in files]
    all_metrics: List[Dict[str, Any]] = []

    with ProcessPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(_worker, wa): wa[0] for wa in worker_args}
        for future in as_completed(futures):
            try:
                result = future.result()
                all_metrics.append(result)
                print_file_result(result)
            except Exception as exc:
                fname = Path(futures[future]).name
                print(f"  [ERROR] {fname}: {exc}")
                all_metrics.append({
                    "input_file": futures[future],
                    "final_decision": "REJECT",
                    "iterations_used": 0,
                    "pass_iteration": None,
                    "pass_llm": None,
                    "total_wall_time_s": 0.0,
                    "total_input_tokens": 0,
                    "total_output_tokens": 0,
                    "total_cost_usd": 0.0,
                    "iterations": [],
                    "error": str(exc),
                })

    total_run_time = time.time() - t_run_start

    # ── Save metrics JSON ────────────────────────────────────────────────────
    metrics_file = output_root / "pilot_metrics.json"
    write_json(metrics_file, {
        "run_at": now_iso(),
        "total_files": len(files),
        "total_run_time_s": round(total_run_time, 2),
        "files": all_metrics,
    })

    # ── Print summary ────────────────────────────────────────────────────────
    passed   = [m for m in all_metrics if m["final_decision"] == "PASS"]
    failed   = [m for m in all_metrics if m["final_decision"] == "FAIL"]
    rejected = [m for m in all_metrics if m["final_decision"] == "REJECT"]

    times  = [m["total_wall_time_s"]   for m in all_metrics if m["total_wall_time_s"] > 0]
    tokens_in  = [m["total_input_tokens"]  for m in all_metrics if m["total_input_tokens"] > 0]
    tokens_out = [m["total_output_tokens"] for m in all_metrics if m["total_output_tokens"] > 0]
    costs  = [m["total_cost_usd"]      for m in all_metrics if m["total_cost_usd"] > 0]

    pass_on_iter = {}
    for m in passed:
        k = f"iter{m['pass_iteration']}_{m['pass_llm']}"
        pass_on_iter[k] = pass_on_iter.get(k, 0) + 1

    print(f"\n{'='*60}")
    print(f"PILOT RUN SUMMARY")
    print(f"{'='*60}")
    print(f"  Total files:   {len(files)}")
    print(f"  PASS:          {len(passed)} ({100*len(passed)//len(files)}%)")
    print(f"  FAIL:          {len(failed)}")
    print(f"  REJECT:        {len(rejected)}")
    print(f"  Total time:    {total_run_time:.1f}s (wall clock, parallel)")
    print()
    print(f"  Pass breakdown by iteration:")
    for k, v in sorted(pass_on_iter.items()):
        print(f"    {k}: {v} files")
    print()
    if times:
        print(f"  Time per file:  min={min(times):.1f}s  avg={sum(times)/len(times):.1f}s  max={max(times):.1f}s")
    if tokens_in:
        print(f"  Input tokens:   min={min(tokens_in):,}  avg={int(sum(tokens_in)/len(tokens_in)):,}  max={max(tokens_in):,}")
    if tokens_out:
        print(f"  Output tokens:  min={min(tokens_out):,}  avg={int(sum(tokens_out)/len(tokens_out)):,}  max={max(tokens_out):,}")
    if costs:
        print(f"  Cost per file:  min=${min(costs):.4f}  avg=${sum(costs)/len(costs):.4f}  max=${max(costs):.4f}")
        print(f"  Total cost:     ${sum(costs):.4f}")
    print(f"\n  Metrics saved to: {metrics_file}")
    print(f"{'='*60}\n")

    return 0 if not failed and not rejected else 1


if __name__ == "__main__":
    raise SystemExit(main())
