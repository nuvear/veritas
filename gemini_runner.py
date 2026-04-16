#!/usr/bin/env python3
"""
Gemini runner for Veritas companion workflows.

Reads a prompt from --prompt-file (or stdin), calls Gemini 2.5 Flash via the
OpenAI-compatible API endpoint, and writes the model's response to --output-file.
If the response contains extra text around the JSON, the runner attempts to
extract the first top-level JSON object and writes the cleaned JSON when possible.

Mirrors the interface of claude_runner.py for drop-in compatibility.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Optional

from openai import OpenAI


DEFAULT_MODEL = "gemini-2.5-flash"
DEFAULT_MAX_TOKENS = 16000
DEFAULT_TEMPERATURE = 0.2


def read_prompt(prompt_file: Optional[str]) -> str:
    if not prompt_file or prompt_file == "-":
        return sys.stdin.read()
    return Path(prompt_file).read_text(encoding="utf-8")


def write_text(path: str, text: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def extract_json_candidate(text: str) -> Optional[str]:
    stripped = text.strip()
    if not stripped:
        return None

    # Strip markdown code fences
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if len(lines) >= 3:
            stripped = "\n".join(lines[1:-1]).strip()

    # Try direct parse
    try:
        json.loads(stripped)
        return stripped
    except Exception:
        pass

    # Find first top-level JSON object
    start = stripped.find("{")
    if start == -1:
        return None

    depth = 0
    in_string = False
    escape = False
    for idx in range(start, len(stripped)):
        ch = stripped[idx]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                candidate = stripped[start:idx + 1]
                try:
                    json.loads(candidate)
                    return candidate
                except Exception:
                    return None
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Gemini runner for Veritas")
    parser.add_argument("--prompt-file", required=False,
                        help="Path to prompt file; use - or omit to read stdin")
    parser.add_argument("--output-file", required=True,
                        help="Path to write the model output")
    parser.add_argument("--model", default=DEFAULT_MODEL,
                        help=f"Gemini model name (default: {DEFAULT_MODEL})")
    parser.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS,
                        help=f"Maximum output tokens (default: {DEFAULT_MAX_TOKENS})")
    parser.add_argument("--temperature", type=float, default=DEFAULT_TEMPERATURE,
                        help=f"Sampling temperature (default: {DEFAULT_TEMPERATURE})")
    parser.add_argument("--raw-output-file",
                        help="Optional file to store the raw model text before JSON cleanup")
    args = parser.parse_args()

    prompt = read_prompt(args.prompt_file)

    # Use OpenAI-compatible client with Gemini endpoint
    client = OpenAI()  # base_url and api_key pre-configured via environment

    response = client.chat.completions.create(
        model=args.model,
        max_tokens=args.max_tokens,
        temperature=args.temperature,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = response.choices[0].message.content or ""
    raw = raw.strip()

    if args.raw_output_file:
        write_text(args.raw_output_file, raw)

    candidate = extract_json_candidate(raw)
    if candidate is not None:
        try:
            data = json.loads(candidate)
            write_text(args.output_file,
                       json.dumps(data, ensure_ascii=False, indent=2) + "\n")
            return 0
        except Exception:
            pass

    write_text(args.output_file, raw)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
