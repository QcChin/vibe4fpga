"""RTL file scanner — walks project directory and extracts module structure.

Supports: Verilog (.v), SystemVerilog (.sv), VHDL (.vhd/.vhdl)
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

RTL_EXTENSIONS = {".v", ".sv", ".vhd", ".vhdl"}

# ── Regex patterns ────────────────────────────────────────────────────────────
# Match module declaration; only the keyword + name. Robust against multi-line
# `#( parameter [(W-1):0] ... )` blocks that defeat a regex with `[^)]*`.
MODULE_DECL_RE = re.compile(r"^[ \t]*module\s+(\w+)\b", re.MULTILINE)

# Wire/reg/logic declarations: wire [W-1:0] signal_name
SIGNAL_DECL_RE = re.compile(
    r"^\s*(?:wire|reg|logic|input|output|inout)\s+"
    r"(?:(?:signed|unsigned)\s+)?(?:\[[^\]]+\]\s+)?(\w+)\s*[;,)]",
    re.MULTILINE,
)
# Module instantiation: `<inst_type> [#(...)] <inst_name> (`
# Anchored on leading whitespace (any indent, tabs OK). Parameter block is
# pre-stripped by `_strip_inst_param_blocks` before this regex runs.
MODULE_INST_RE = re.compile(
    r"^[ \t]+(\w+)\s+(\w+)\s*\(",
    re.MULTILINE,
)

# Strip Verilog `//` line and `/* ... */` block comments.
_COMMENT_RE = re.compile(r"//[^\n]*|/\*.*?\*/", re.DOTALL)


def _strip_comments(text: str) -> str:
    return _COMMENT_RE.sub(" ", text)


def _strip_inst_param_blocks(text: str) -> str:
    """Erase `#( ... balanced ... )` blocks so the instantiation regex doesn't
    have to cope with nested parens inside parameter expressions like
    ``parameter [(WIDTH-1):0]``.
    """
    out = []
    i = 0
    n = len(text)
    while i < n:
        if text[i] == "#" and i + 1 < n and text[i + 1] == "(":
            depth = 1
            j = i + 2
            while j < n and depth > 0:
                c = text[j]
                if c == "(":
                    depth += 1
                elif c == ")":
                    depth -= 1
                j += 1
            out.append(" ")  # collapse the whole block to one space
            i = j
        else:
            out.append(text[i])
            i += 1
    return "".join(out)

# ── Data models ───────────────────────────────────────────────────────────────

@dataclass
class ModuleInfo:
    name: str
    file_path: str
    line: int
    signals: list[str] = field(default_factory=list)
    instantiates: list[str] = field(default_factory=list)  # child module names


@dataclass
class ScanResult:
    project_path: str
    file_count: int
    module_count: int
    modules: dict[str, ModuleInfo]
    files: list[str]


# ── Core scanner ──────────────────────────────────────────────────────────────

def scan(project_path: str) -> ScanResult:
    """Scan RTL file tree, extract module declarations and instantiation graph."""
    root = Path(project_path)
    files = sorted(
        f for f in root.rglob("*") if f.suffix.lower() in RTL_EXTENSIONS
    )

    # Pass 1: collect module declarations
    modules: dict[str, ModuleInfo] = {}
    file_module_map: dict[str, str] = {}  # file_path → primary module name

    for fp in files:
        try:
            content = fp.read_text(errors="replace")
        except OSError:
            continue

        stripped = _strip_comments(content)
        for m in MODULE_DECL_RE.finditer(stripped):
            mod_name = m.group(1)
            line_no = stripped[: m.start()].count("\n") + 1
            signals = [s.group(1) for s in SIGNAL_DECL_RE.finditer(stripped)]
            info = ModuleInfo(
                name=mod_name,
                file_path=str(fp),
                line=line_no,
                signals=signals,
            )
            modules[mod_name] = info
            file_module_map.setdefault(str(fp), mod_name)

    known_modules = set(modules.keys())

    # Pass 2: resolve instantiations
    # Keywords that look like module names but aren't instantiations
    HDL_KEYWORDS = {
        "module", "endmodule", "if", "else", "begin", "end", "for", "while",
        "always", "always_ff", "always_comb", "always_latch", "initial",
        "assign", "wire", "reg", "logic", "input", "output", "inout",
        "parameter", "localparam", "generate", "endgenerate", "genvar",
        "case", "endcase", "casez", "casex", "function", "endfunction",
        "task", "endtask", "return", "typedef", "enum", "struct", "union",
        "package", "endpackage", "import", "export", "interface",
        "endinterface", "modport", "class", "endclass", "extends",
        "implements", "virtual", "extern", "automatic", "static", "const",
        "ref", "default", "defparam", "specify", "endspecify",
    }

    for fp in files:
        try:
            content = fp.read_text(errors="replace")
        except OSError:
            continue

        parent_name = file_module_map.get(str(fp))
        if not parent_name or parent_name not in modules:
            continue

        cleaned = _strip_inst_param_blocks(_strip_comments(content))

        seen: set[str] = set()
        for m in MODULE_INST_RE.finditer(cleaned):
            module_type = m.group(1)
            if (
                module_type in known_modules
                and module_type != parent_name
                and module_type not in HDL_KEYWORDS
                and module_type not in seen
            ):
                modules[parent_name].instantiates.append(module_type)
                seen.add(module_type)

    return ScanResult(
        project_path=project_path,
        file_count=len(files),
        module_count=len(modules),
        modules=modules,
        files=[str(f) for f in files],
    )


def to_dict(result: ScanResult) -> dict:
    return {
        "project_path": result.project_path,
        "file_count": result.file_count,
        "module_count": result.module_count,
        "modules": {
            name: {
                "file": info.file_path,
                "line": info.line,
                "signals": info.signals[:50],  # cap to avoid huge payloads
                "instantiates": info.instantiates,
            }
            for name, info in result.modules.items()
        },
        "files": result.files,
    }


def build_hierarchy(scan_result: ScanResult, top_module: str | None = None) -> dict:
    """Return module hierarchy as a nested tree JSON."""
    modules = scan_result.modules

    # Detect root modules (not instantiated by any other module)
    instantiated: set[str] = set()
    for info in modules.values():
        instantiated.update(info.instantiates)

    roots = [n for n in modules if n not in instantiated]
    if top_module:
        roots = [top_module] if top_module in modules else roots

    def build_tree(name: str, visited: frozenset) -> dict:
        if name not in modules:
            return {"name": name, "missing": True, "children": []}
        if name in visited:
            return {"name": name, "cyclic": True, "children": []}
        info = modules[name]
        return {
            "name": name,
            "file": info.file_path,
            "line": info.line,
            "children": [
                build_tree(child, visited | {name})
                for child in info.instantiates
            ],
        }

    return {
        "roots": roots,
        "trees": [build_tree(r, frozenset()) for r in roots],
    }


def search_signal(scan_result: ScanResult, signal_name: str) -> list[dict]:
    """Cross-file search for signal definitions and references."""
    results = []
    pattern = re.compile(rf"\b{re.escape(signal_name)}\b")

    for fp_str in scan_result.files:
        fp = Path(fp_str)
        try:
            lines = fp.read_text(errors="replace").splitlines()
        except OSError:
            continue

        for line_no, line_text in enumerate(lines, start=1):
            if pattern.search(line_text):
                results.append({
                    "file": fp_str,
                    "line": line_no,
                    "text": line_text.strip(),
                })

    return results


def analyze_naming_conventions(scan_result: ScanResult) -> dict:
    """Statistical naming convention analysis with confidence scores.

    Only returns conventions where confidence > 0.70.
    """
    conventions: dict = {}

    # ── Clock signal naming ───────────────────────────────────────────────────
    clk_counter: Counter = Counter()
    rst_counter: Counter = Counter()

    for fp_str in scan_result.files:
        fp = Path(fp_str)
        try:
            content = fp.read_text(errors="replace")
        except OSError:
            continue

        for m in re.finditer(r"\b(clk\w*|clock\w*)\b", content, re.IGNORECASE):
            clk_counter[m.group(1).lower()] += 1
        for m in re.finditer(r"\b(rst\w*|reset\w*|arst\w*|nrst\w*)\b", content, re.IGNORECASE):
            rst_counter[m.group(1).lower()] += 1

    if clk_counter:
        top, count = clk_counter.most_common(1)[0]
        conf = count / sum(clk_counter.values())
        if conf > 0.70:
            conventions["clock_signal"] = {"pattern": top, "confidence": round(conf, 2)}

    if rst_counter:
        top, count = rst_counter.most_common(1)[0]
        conf = count / sum(rst_counter.values())
        if conf > 0.70:
            conventions["reset_signal"] = {"pattern": top, "confidence": round(conf, 2)}

    # ── Module name prefixes ──────────────────────────────────────────────────
    mod_names = list(scan_result.modules.keys())
    if len(mod_names) >= 3:
        prefix_counter: Counter = Counter()
        for name in mod_names:
            # Look for common 2-3 char prefixes separated by _ or lowercase boundary
            m = re.match(r"^([a-z]{2,4}_)", name)
            if m:
                prefix_counter[m.group(1)] += 1
        if prefix_counter:
            top, count = prefix_counter.most_common(1)[0]
            conf = count / len(mod_names)
            if conf > 0.70:
                conventions["module_prefix"] = {"pattern": top, "confidence": round(conf, 2)}

    # ── Signal suffix patterns (e.g., _r for registered, _n for active-low) ──
    all_signals = [s for info in scan_result.modules.values() for s in info.signals]
    if all_signals:
        suffix_counter: Counter = Counter()
        for sig in all_signals:
            m = re.search(r"(_[rniod])$", sig)
            if m:
                suffix_counter[m.group(1)] += 1
        if suffix_counter:
            top, count = suffix_counter.most_common(1)[0]
            conf = count / len(all_signals)
            if conf > 0.70:
                conventions["signal_suffix"] = {"pattern": top, "confidence": round(conf, 2)}

    return conventions
