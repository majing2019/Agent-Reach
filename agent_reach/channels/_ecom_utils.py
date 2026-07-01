# -*- coding: utf-8 -*-
"""Shared utility for parsing ecommerce-cli check output.

probe_command captures both stdout and stderr, but Playwright often
writes warnings to stderr.  This helper finds the JSON object among
the noise — handles both single-line and multi-line (pretty-printed) JSON.
"""

import json


def parse_ecom_check_output(raw_output: str) -> dict:
    """Extract the JSON status dict from ecommerce-cli output.

    Handles both single-line JSON and pretty-printed (multi-line) JSON.
    Raises ValueError if no valid JSON with a "platform" key is found.
    """
    text = raw_output.strip()

    # ===== Strategy 1: single-line JSON on any line =====
    for line in reversed(text.split("\n")):
        line = line.strip()
        if line.startswith("{") and line.endswith("}") and '"platform"' in line:
            try:
                return json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue

    # ===== Strategy 2: multi-line (pretty-printed) JSON =====
    # Find the outermost { ... } block that contains "platform"
    try:
        brace_start = text.index("{")
    except ValueError:
        raise ValueError("No JSON object found in output")

    # Simple brace counter to find matching closing brace
    depth = 0
    brace_end = -1
    for i in range(brace_start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                brace_end = i + 1
                break

    if brace_end > brace_start:
        candidate = text[brace_start:brace_end]
        if '"platform"' in candidate:
            try:
                return json.loads(candidate)
            except (json.JSONDecodeError, ValueError):
                pass

    raise ValueError("No valid JSON found in output")
