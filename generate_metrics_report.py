#!/usr/bin/env python3
"""Generate a comprehensive metrics report from the pilot run using per-iteration feedback files."""
import json
import re
from pathlib import Path
from statistics import mean, median
from collections import Counter
from datetime import datetime

RUNS_DIR = Path('/home/ubuntu/veritas_project/pilot_output/.veritas_runs')
OUTPUT_DIR = Path('/home/ubuntu/veritas_project/pilot_output')
PASS_DIR = OUTPUT_DIR
FAIL_DIR = OUTPUT_DIR

# Claude pricing: $3/$15 per M input/output tokens
CLAUDE_IN_PRICE = 3.0 / 1_000_000
CLAUDE_OUT_PRICE = 15.0 / 1_000_000
# Gemini 2.5 Flash pricing: $0.15/$0.60 per M input/output tokens
GEMINI_IN_PRICE = 0.15 / 1_000_000
GEMINI_OUT_PRICE = 0.60 / 1_000_000

def estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token."""
    return len(text) // 4

def get_llm_for_iter(iteration: int) -> str:
    if iteration == 1:
        return 'claude'
    elif iteration == 2:
        return 'gemini'
    else:
        return 'claude'

def compute_cost(llm: str, input_tokens: int, output_tokens: int) -> float:
    if llm == 'gemini':
        return input_tokens * GEMINI_IN_PRICE + output_tokens * GEMINI_OUT_PRICE
    else:
        return input_tokens * CLAUDE_IN_PRICE + output_tokens * CLAUDE_OUT_PRICE

def load_food_metrics(food_dir: Path):
    """Load all iteration data for a food from its run directory."""
    stem = food_dir.name
    iterations = []
    
    for iter_num in [1, 2, 3]:
        feedback_file = food_dir / f"{stem}__iter0{iter_num}_feedback.json"
        prompt_file = food_dir / f"{stem}__iter0{iter_num}_prompt.md"
        draft_file = food_dir / f"{stem}__iter0{iter_num}_draft.json"
        stdout_file = food_dir / f"{stem}__iter0{iter_num}_stdout.txt"
        
        if not feedback_file.exists():
            break
            
        feedback = json.loads(feedback_file.read_text())
        
        # Estimate tokens from prompt and draft
        prompt_text = prompt_file.read_text() if prompt_file.exists() else ""
        draft_text = draft_file.read_text() if draft_file.exists() else ""
        
        input_tokens = estimate_tokens(prompt_text)
        output_tokens = estimate_tokens(draft_text)
        
        # Try to get timing from stdout
        wall_time = 0.0
        if stdout_file.exists():
            stdout_text = stdout_file.read_text()
            # Look for timing info if present
            time_match = re.search(r'wall_time[_:]?\s*([\d.]+)', stdout_text)
            if time_match:
                wall_time = float(time_match.group(1))
        
        llm = get_llm_for_iter(iter_num)
        cost = compute_cost(llm, input_tokens, output_tokens)
        
        iterations.append({
            'iteration': iter_num,
            'llm': llm,
            'decision': feedback.get('decision', 'UNKNOWN'),
            'score_total': feedback.get('score_total', 0),
            'score_max': feedback.get('score_max', 8),
            'failure_reasons': feedback.get('failure_reasons', []),
            'input_tokens': input_tokens,
            'output_tokens': output_tokens,
            'cost_usd': cost,
            'wall_time_s': wall_time,
        })
    
    if not iterations:
        return None
    
    # Determine final decision
    final_iter = iterations[-1]
    final_decision = final_iter['decision']
    
    # Determine which LLM passed
    pass_llm = None
    for it in iterations:
        if it['decision'] == 'PASS':
            pass_llm = it['llm'] if it['iteration'] < 3 else 'claude_final'
            final_decision = 'PASS'
            break
    
    total_input_tokens = sum(i['input_tokens'] for i in iterations)
    total_output_tokens = sum(i['output_tokens'] for i in iterations)
    total_cost = sum(i['cost_usd'] for i in iterations)
    total_time = sum(i['wall_time_s'] for i in iterations)
    
    # Get input filename from feedback
    input_file = iterations[0].get('input_file', stem) if iterations else stem
    
    return {
        'stem': stem,
        'input_file': stem,
        'final_decision': final_decision,
        'iterations_used': len(iterations),
        'pass_llm': pass_llm,
        'total_input_tokens': total_input_tokens,
        'total_output_tokens': total_output_tokens,
        'total_cost_usd': total_cost,
        'total_wall_time_s': total_time,
        'iterations': iterations,
    }

def main():
    summaries = []
    for food_dir in sorted(RUNS_DIR.iterdir()):
        if food_dir.is_dir():
            s = load_food_metrics(food_dir)
            if s:
                summaries.append(s)

    if not summaries:
        print("No run data found.")
        return

    n = len(summaries)
    
    # Collect metrics
    times = [s['total_wall_time_s'] for s in summaries]
    input_tokens = [s['total_input_tokens'] for s in summaries]
    output_tokens = [s['total_output_tokens'] for s in summaries]
    costs = [s['total_cost_usd'] for s in summaries]
    decisions = [s['final_decision'] for s in summaries]
    
    pass_count = decisions.count('PASS')
    fail_count = decisions.count('FAIL')
    reject_count = decisions.count('REJECT')
    
    # Iteration breakdown
    pass_llm_counts = Counter(s['pass_llm'] for s in summaries if s['pass_llm'])
    iter_dist = Counter(s['iterations_used'] for s in summaries)
    
    # Failure reasons
    failure_reasons_all = []
    for s in summaries:
        if s['final_decision'] != 'PASS':
            for it in s['iterations']:
                failure_reasons_all.extend(it.get('failure_reasons', []))
    top_failures = Counter(failure_reasons_all).most_common(10)
    
    # Build report dict
    report = {
        "generated_at": datetime.now().isoformat(),
        "pilot_summary": {
            "total_files": n,
            "pass": pass_count,
            "fail": fail_count,
            "reject": reject_count,
            "pass_rate_pct": round(pass_count / n * 100, 1),
        },
        "iteration_breakdown": {
            "passed_on_iter_1_claude": pass_llm_counts.get('claude', 0),
            "passed_on_iter_2_gemini": pass_llm_counts.get('gemini', 0),
            "passed_on_iter_3_claude_final": pass_llm_counts.get('claude_final', 0),
            "did_not_pass": fail_count + reject_count,
            "files_needing_1_iter": iter_dist.get(1, 0),
            "files_needing_2_iters": iter_dist.get(2, 0),
            "files_needing_3_iters": iter_dist.get(3, 0),
        },
        "time_seconds": {
            "min": round(min(times), 1) if times else 0,
            "max": round(max(times), 1) if times else 0,
            "avg": round(mean(times), 1) if times else 0,
            "median": round(median(times), 1) if times else 0,
            "total_pilot": round(sum(times), 1),
        },
        "input_tokens": {
            "min": min(input_tokens),
            "max": max(input_tokens),
            "avg": round(mean(input_tokens)),
            "total": sum(input_tokens),
        },
        "output_tokens": {
            "min": min(output_tokens),
            "max": max(output_tokens),
            "avg": round(mean(output_tokens)),
            "total": sum(output_tokens),
        },
        "cost_usd": {
            "min": round(min(costs), 4),
            "max": round(max(costs), 4),
            "avg": round(mean(costs), 4),
            "total_pilot": round(sum(costs), 4),
            "estimated_per_100_files": round(mean(costs) * 100, 2),
            "estimated_per_1000_files": round(mean(costs) * 1000, 2),
            "estimated_per_6600_files": round(mean(costs) * 6600, 2),
        },
        "top_failure_reasons": [
            {"reason": r, "count": c} for r, c in top_failures
        ],
        "per_file_results": [
            {
                "file": s['stem'][:60],
                "decision": s['final_decision'],
                "iterations": s['iterations_used'],
                "pass_llm": s['pass_llm'],
                "time_s": round(s['total_wall_time_s'], 1),
                "cost_usd": round(s['total_cost_usd'], 4),
                "input_tokens": s['total_input_tokens'],
                "output_tokens": s['total_output_tokens'],
            }
            for s in summaries
        ]
    }
    
    # Save JSON
    report_file = OUTPUT_DIR / 'pilot_metrics_report.json'
    report_file.write_text(json.dumps(report, indent=2))
    print(f"Saved JSON: {report_file}")
    
    # Print human-readable summary
    print("\n" + "="*65)
    print("  VERITAS DUAL-LLM PILOT — METRICS REPORT")
    print("="*65)
    print(f"\n  Files processed: {n}")
    print(f"  PASS:   {pass_count:3d}  ({pass_count/n*100:.1f}%)")
    print(f"  FAIL:   {fail_count:3d}  ({fail_count/n*100:.1f}%)")
    print(f"  REJECT: {reject_count:3d}  ({reject_count/n*100:.1f}%)")
    
    print(f"\n  ITERATION BREAKDOWN:")
    print(f"    Passed iter 1 (Claude):       {pass_llm_counts.get('claude', 0)}")
    print(f"    Passed iter 2 (Gemini repair): {pass_llm_counts.get('gemini', 0)}")
    print(f"    Passed iter 3 (Claude final):  {pass_llm_counts.get('claude_final', 0)}")
    print(f"    Did not pass (all 3 iters):    {fail_count + reject_count}")
    print(f"    Files using 1 iter:  {iter_dist.get(1,0)}")
    print(f"    Files using 2 iters: {iter_dist.get(2,0)}")
    print(f"    Files using 3 iters: {iter_dist.get(3,0)}")
    
    print(f"\n  TIME (seconds per file):")
    if times and max(times) > 0:
        print(f"    Min: {min(times):.1f}s  Max: {max(times):.1f}s  Avg: {mean(times):.1f}s  Median: {median(times):.1f}s")
    else:
        print(f"    (timing data not captured in this run)")
    
    print(f"\n  TOKENS (per file, estimated):")
    print(f"    Input  — Min: {min(input_tokens):,}  Max: {max(input_tokens):,}  Avg: {round(mean(input_tokens)):,}")
    print(f"    Output — Min: {min(output_tokens):,}  Max: {max(output_tokens):,}  Avg: {round(mean(output_tokens)):,}")
    
    print(f"\n  COST (USD per file, estimated):")
    print(f"    Min: ${min(costs):.4f}  Max: ${max(costs):.4f}  Avg: ${mean(costs):.4f}")
    print(f"    Total pilot (26 files): ${sum(costs):.4f}")
    print(f"    Estimated per 100 files: ${mean(costs)*100:.2f}")
    print(f"    Estimated per 1,000 files: ${mean(costs)*1000:.2f}")
    print(f"    Estimated per 6,600 files: ${mean(costs)*6600:.2f}")
    
    print(f"\n  TOP FAILURE REASONS (files that did not pass):")
    for r, c in top_failures[:8]:
        print(f"    [{c}x] {r}")
    
    print(f"\n  PER-FILE RESULTS:")
    print(f"  {'File':<52} {'Decision':<8} {'Iters':<6} {'Cost':<9}")
    print(f"  {'-'*52} {'-'*8} {'-'*6} {'-'*9}")
    for p in report['per_file_results']:
        fname = p['file'][:49] + '...' if len(p['file']) > 52 else p['file']
        print(f"  {fname:<52} {p['decision']:<8} {p['iterations']:<6} ${p['cost_usd']:<8.4f}")
    
    print("\n" + "="*65)

if __name__ == '__main__':
    main()
