"""InstrumentAnalyze Skill — sim vs. real-measurement root cause attribution.

Flow:
  1. Load measurement waveform (CSV or SCPI live)
  2. Load simulation waveform from VCD (via waveform-mcp sidecar if available)
  3. Time-domain alignment via cross-correlation
  4. Classify differences into 7 categories (pure numpy, no LLM):
     overshoot / dc_offset / noise / drift / phase / amplitude / unknown
  5. LLM root-cause attribution for suspicious/anomalous findings
  6. Return a structured report with actionable conclusions
"""

from __future__ import annotations

import csv as csv_mod
import json
from typing import Any

from vibe4fpga_platform import scratch_file

from .._llm import call_llm, parse_json_response
from .classifier import enrich_findings, summarize_findings

INSTRUMENT_ANALYZE_SYSTEM = """\
You are an FPGA hardware engineer performing simulation-to-measurement correlation analysis.

Given:
  1. Alignment result (time offset between simulation and real measurement)
  2. Classified differences (expected physical effects vs. anomalous design issues)
  3. Waveform metadata (duration, sample rate, signal name)

For each anomalous or suspicious finding, provide:
  - Root cause analysis (is this an RTL bug? constraint issue? PCB problem?)
  - Specific investigation steps
  - Whether RTL changes are needed (yes/no/maybe)

Output JSON:
{
  "overall_verdict":  "rtl_correct | rtl_suspect | rtl_fault",
  "confidence":       "high | medium | low",
  "findings": [
    {
      "diff_type":    "string",
      "root_cause":   "string — technical explanation",
      "action":       "string — what to do next",
      "rtl_change":   "required | not_needed | investigate"
    }
  ],
  "summary": "string — 2-3 sentence executive summary for the engineer"
}
"""

INSTRUMENT_ANALYZE_PROMPT = """\
Alignment result:
  Time offset:        {offset_ns:.2f} ns ({offset_samples} samples)
  Correlation peak:   {correlation_peak:.3f} (0=no match, 1=perfect)

Classified differences:
{diff_findings_json}

Signal: {signal_name}
Measurement duration: {duration_ns:.0f} ns
Clock period: {clock_period_ns:.1f} ns

Perform root cause attribution for each suspicious/anomalous finding.
"""


def _load_measurement(
    meas_file: str | None,
    scpi_resource: str | None,
    meas_channel: int,
    meas_vendor: str,
    timeout_ms: int,
) -> dict:
    """Load a measurement waveform from CSV or SCPI live capture.

    When a ``scpi_resource`` is given, the live capture result is written to a
    platform-managed scratch CSV so downstream code paths that expect a file
    on disk (e.g. the aligner) keep working on Windows without touching ``/tmp``.
    """
    # Prefer SCPI live capture when a resource string is supplied.
    if scpi_resource:
        try:
            from ...readers.scpi import capture_waveform
        except ImportError as exc:
            return {"error": f"SCPI reader unavailable: {exc}"}

        result = capture_waveform(
            scpi_resource, channel=meas_channel, timeout_ms=timeout_ms
        )
        if "error" in result:
            return result

        # Persist to a scratch CSV so callers can subsequently feed it to the
        # aligner; cleanup is handled automatically at process exit.
        csv_path = scratch_file(".csv")
        with csv_path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv_mod.writer(fh)
            for t_ns, v in zip(result["time_ns"], result["voltage_v"]):
                writer.writerow([t_ns, v])
        result["source_csv"] = str(csv_path)
        return result

    if not meas_file:
        return {"error": "No measurement source provided (meas_file or scpi_resource)"}

    try:
        from ...readers.generic import read_csv_auto
        from ...readers.rigol import read_rigol_csv
    except ImportError as exc:
        return {"error": f"CSV reader unavailable: {exc}"}

    if meas_vendor == "rigol":
        return read_rigol_csv(meas_file, channel=f"CH{meas_channel}")
    if meas_vendor == "auto":
        try:
            with open(meas_file, "r", errors="replace") as f:
                head = f.read(256)
            if "#Model" in head or "#Channel" in head or "#SampleRate" in head:
                return read_rigol_csv(meas_file, channel=f"CH{meas_channel}")
        except OSError:
            pass
    return read_csv_auto(meas_file, channel=meas_channel - 1)


def _load_sim_signal(sim_vcd_file: str, sim_signal: str) -> dict:
    """Pull a named signal out of a VCD file via the waveform-mcp parser.

    Returns ``{"sim_time_ns": [...], "sim_voltage": [...]}`` on success or
    ``{"error": "..."}`` when the sidecar is unavailable or the signal
    cannot be located.
    """
    try:
        from waveform_mcp.parser import parse_waveform as parse_vcd  # type: ignore
    except ImportError:
        return {
            "error": (
                "waveform-mcp not installed — install the sidecar "
                "(`uv tool install waveform-mcp`) to parse VCD files."
            )
        }

    vcd_meta = parse_vcd(sim_vcd_file)
    sig = vcd_meta.signals.get(sim_signal)
    if sig is None:
        matches = [n for n in vcd_meta.signals if sim_signal in n]
        if not matches:
            return {"error": f"Signal '{sim_signal}' not found in {sim_vcd_file}"}
        sig = vcd_meta.signals[matches[0]]

    sim_tv = sig.tv
    sim_time_ns = [t for t, _ in sim_tv]
    sim_voltage: list[float] = []
    for _, v in sim_tv:
        try:
            sim_voltage.append(float(int(v, 2)) if set(v).issubset("01xz") else float(v))
        except (ValueError, TypeError):
            sim_voltage.append(0.0)
    return {"sim_time_ns": sim_time_ns, "sim_voltage": sim_voltage}


async def run(
    meas_file:        str | None = None,
    sim_vcd_file:     str | None = None,
    sim_signal:       str        = "",
    meas_channel:     int        = 1,
    meas_vendor:      str        = "auto",
    scpi_resource:    str | None = None,
    clock_period_ns:  float      = 10.0,
    model:            str        = "claude",
    timeout_ms:       int        = 10000,
    alignment:        dict[str, Any] | None = None,
    diff_findings:    list[dict] | None = None,
) -> dict:
    """Run the InstrumentAnalyze skill end-to-end.

    Typical invocation paths:

    * **Pre-classified** — caller already ran ``align_with_simulation`` and
      ``classify_differences_tool`` and passes ``alignment`` + ``diff_findings``.
      Only the LLM root-cause attribution is performed.
    * **From file/VCD** — caller passes ``meas_file`` (or ``scpi_resource``)
      together with ``sim_vcd_file`` + ``sim_signal``. The skill runs the full
      load → align → classify → attribute pipeline in-process. Requires the
      waveform-mcp Python package to be importable for VCD parsing.

    Args:
        meas_file:       Path to oscilloscope CSV export.
        sim_vcd_file:    Path to VCD simulation file.
        sim_signal:      Signal name in VCD to compare against measurement.
        meas_channel:    Oscilloscope channel (1-based).
        meas_vendor:     CSV vendor hint ("auto" | "rigol" | "generic").
        scpi_resource:   VISA resource for live capture (overrides meas_file).
        clock_period_ns: Clock period for difference analysis context.
        model:           LLM model key understood by ``vibe4fpga-llm-client``.
        timeout_ms:      SCPI capture timeout (ignored for file-based loads).
        alignment:       Pre-computed alignment result (skip load/align stages).
        diff_findings:   Pre-computed classifier output (skip load/align/classify).

    Returns:
        {
            "alignment":     {...} or {"error": "..."},
            "diff_findings": [{diff_type, classification, evidence, suggestion}],
            "summary":       {total_findings, by_classification, overall},
            "llm_analysis":  {overall_verdict, confidence, findings, summary},
            "report_md":     str,
        }
    """
    # ── Steps 1–3: Load + align (only if caller didn't pre-compute) ──────────
    if alignment is None:
        meas = _load_measurement(
            meas_file,
            scpi_resource,
            meas_channel,
            meas_vendor,
            timeout_ms,
        )
        if "error" in meas:
            alignment = meas
        elif sim_vcd_file:
            sim = _load_sim_signal(sim_vcd_file, sim_signal)
            if "error" in sim:
                alignment = sim
            else:
                from ...aligner import cross_correlate_align
                alignment = cross_correlate_align(
                    sim_time_ns=sim["sim_time_ns"],
                    sim_voltage=sim["sim_voltage"],
                    meas_time_ns=meas["time_ns"],
                    meas_voltage=meas["voltage_v"],
                )
        else:
            alignment = {"error": "No sim_vcd_file provided — nothing to align against"}

    # ── Step 4: Classify differences (only if caller didn't pre-compute) ─────
    if diff_findings is None:
        diff_findings = []
        if alignment and "diff_v" in alignment and "error" not in alignment:
            # Defer import to avoid pulling the full server module at import time.
            from ...server import classify_differences_tool
            diff_findings = await classify_differences_tool(
                diff_v=alignment["diff_v"],
                time_ns=alignment["time_ns"],
                sim_v=alignment["sim_v_aligned"],
                meas_v=alignment["aligned_meas_v"],
                clock_period_ns=clock_period_ns,
            )

    findings = enrich_findings(diff_findings or [])
    summary = summarize_findings(findings)
    finding_dicts = [f.to_dict() for f in findings]

    # ── Step 5: LLM root-cause attribution ───────────────────────────────────
    llm_analysis: dict = {}
    if findings:
        time_axis = (alignment or {}).get("time_ns") or [0, 0]
        duration_ns = time_axis[-1] - time_axis[0] if len(time_axis) > 1 else 0

        prompt = INSTRUMENT_ANALYZE_PROMPT.format(
            offset_ns=(alignment or {}).get("offset_ns", 0) or 0,
            offset_samples=(alignment or {}).get("offset_samples", 0) or 0,
            correlation_peak=(alignment or {}).get("correlation_peak", 0) or 0,
            diff_findings_json=json.dumps(finding_dicts, indent=2),
            signal_name=sim_signal or "unknown",
            duration_ns=duration_ns,
            clock_period_ns=clock_period_ns,
        )

        raw = await call_llm(
            messages=[{"role": "user", "content": prompt}],
            system=INSTRUMENT_ANALYZE_SYSTEM,
            model=model,
            temperature=0.2,
        )

        try:
            llm_analysis = parse_json_response(raw)
        except json.JSONDecodeError:
            llm_analysis = {"raw": raw}

    # ── Step 6: Build Markdown report ────────────────────────────────────────
    report_lines = [
        "## InstrumentAnalyze Report",
        "",
        f"**Signal:** `{sim_signal or 'unknown'}`  |  "
        f"**Overall:** {summary['overall'].upper()}",
        "",
        "### Alignment",
        f"- Time offset: **{(alignment or {}).get('offset_ns', 'N/A')} ns**",
        f"- Correlation: **{(alignment or {}).get('correlation_peak', 'N/A')}**",
        "",
        "### Difference Summary",
        f"- Expected:   {summary['by_classification'].get('expected', 0)}",
        f"- Suspicious: {summary['by_classification'].get('suspicious', 0)}",
        f"- Anomalous:  {summary['by_classification'].get('anomalous', 0)}",
    ]

    if llm_analysis.get("summary"):
        report_lines += ["", "### LLM Analysis", llm_analysis["summary"]]

    if llm_analysis.get("findings"):
        report_lines += ["", "### Action Items"]
        for item in llm_analysis["findings"]:
            report_lines.append(
                f"- **{item.get('diff_type', '?')}**: {item.get('action', '')} "
                f"[RTL change: {item.get('rtl_change', '?')}]"
            )

    return {
        "alignment":     alignment or {},
        "diff_findings": finding_dicts,
        "summary":       summary,
        "llm_analysis":  llm_analysis,
        "report_md":     "\n".join(report_lines),
    }
