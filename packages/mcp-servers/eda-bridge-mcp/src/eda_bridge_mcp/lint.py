"""Static lint tools: verilator + verible-verilog-lint.

Typical runtime: ~12ms per file.
Catches ~40% of LLM-generated RTL issues at the Lint layer.
"""

from __future__ import annotations

import asyncio
import re
import shutil
from pathlib import Path


async def run_verilator_lint(files: list[str]) -> list[dict]:
    """Run verilator --lint-only on the given files.

    Returns structured findings with file/line/severity/message.
    Returns a warning item if verilator is not installed.
    """
    if not shutil.which("verilator"):
        return [{
            "tool": "verilator",
            "severity": "info",
            "message": "verilator not found in PATH — skipping Verilator lint",
        }]

    cmd = ["verilator", "--lint-only", "-Wall", "--sv", *files]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)

    findings: list[dict] = []
    # Verilator format: %Error/Warning: path/file.v:LINE:COL: message
    pattern = re.compile(r"%(Error|Warning)(?:-[A-Z]+)?: (.+):(\d+):(\d+): (.+)")
    for line in stderr.decode().splitlines():
        m = pattern.match(line)
        if m:
            findings.append({
                "tool":     "verilator",
                "severity": m.group(1).lower(),
                "file":     m.group(2),
                "line":     int(m.group(3)),
                "col":      int(m.group(4)),
                "message":  m.group(5).strip(),
            })

    return findings


async def run_verible_lint(files: list[str]) -> list[dict]:
    """Run verible-verilog-lint (Google open-source AST-based linter).

    Returns empty list if verible is not installed (optional tool).
    """
    if not shutil.which("verible-verilog-lint"):
        return []

    cmd = ["verible-verilog-lint", *files]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)

    findings: list[dict] = []
    # Verible format: path/file.v:LINE:COL: message [rule-name]
    pattern = re.compile(r"(.+):(\d+):(\d+):\s+(.+?)\s+\[(.+)\]")
    for line in stdout.decode().splitlines():
        m = pattern.match(line)
        if m:
            findings.append({
                "tool":     "verible",
                "severity": "warning",
                "file":     m.group(1),
                "line":     int(m.group(2)),
                "col":      int(m.group(3)),
                "message":  m.group(4).strip(),
                "rule":     m.group(5),
            })

    return findings


async def lint_files(files: list[str]) -> dict:
    """Run both verilator and verible lint in parallel, merge results.

    Returns:
        {
            "findings": [...],
            "error_count":   int,
            "warning_count": int,
            "score_penalty": int,   # -10 per error, -2 per warning
        }
    """
    verilator_task = run_verilator_lint(files)
    verible_task = run_verible_lint(files)

    verilator_results, verible_results = await asyncio.gather(
        verilator_task, verible_task
    )

    all_findings = verilator_results + verible_results
    errors   = sum(1 for f in all_findings if f.get("severity") == "error")
    warnings = sum(1 for f in all_findings if f.get("severity") == "warning")

    return {
        "findings":      all_findings,
        "error_count":   errors,
        "warning_count": warnings,
        "score_penalty": errors * 10 + warnings * 2,
    }
