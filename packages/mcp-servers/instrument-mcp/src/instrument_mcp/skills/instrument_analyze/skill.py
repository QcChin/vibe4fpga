"""InstrumentAnalyze Skill — sim vs. real-measurement root cause attribution.

Flow:
  1. Load measurement waveform (CSV or SCPI live)
  2. Load simulation waveform from VCD (via instrument-mcp or direct)
  3. Time-domain alignment via cross-correlation
  4. Classify differences (5 categories: expected / suspicious / anomalous)
  5. LLM root cause attribution with full context
  6. Return structured report with actionable conclusions
"""

from __future__ import annotations

import json
import re

import httpx

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


async def run(
    meas_file: str | None = None,
    sim_vcd_file: str | None = None,
    sim_signal: str = "",
    meas_channel: int = 1,
    meas_vendor: str = "auto",
    scpi_resource: str | None = None,
    clock_period_ns: float = 10.0,
    router_url: str = "http://localhost:8765",
    model: str = "claude",
    instrument_mcp_url: str | None = None,
) -> dict:
    """Run the InstrumentAnalyze skill.

    Args:
        meas_file:          Path to oscilloscope CSV export.
        sim_vcd_file:       Path to VCD simulation file.
        sim_signal:         Signal name in VCD to compare against measurement.
        meas_channel:       Oscilloscope channel (1-based).
        meas_vendor:        CSV vendor hint ("auto" | "rigol" | "generic").
        scpi_resource:      VISA resource for live capture (overrides meas_file).
        clock_period_ns:    Clock period for difference analysis context.
        router_url:         LLM Router URL.
        instrument_mcp_url: instrument-mcp URL (uses direct import if None).

    Returns:
        {
            "alignment":       {offset_ns, correlation_peak, ...},
            "diff_findings":   [{diff_type, classification, evidence, suggestion}],
            "summary":         {total_findings, by_classification, overall},
            "llm_analysis":    {overall_verdict, confidence, findings, summary},
            "report_md":       str,
        }
    """
    # ── Step 1 & 2: Load data via instrument-mcp or direct ────────────────────
    alignment: dict = {}
    raw_diff_findings: list[dict] = []

    if instrument_mcp_url:
        async with httpx.AsyncClient(timeout=120.0) as client:
            if scpi_resource:
                # Live capture
                r = await client.post(
                    f"{instrument_mcp_url}/tools/capture_live_waveform",
                    json={"resource_string": scpi_resource, "channel": meas_channel},
                )
                if r.status_code == 200:
                    meas_data = r.json()
                    # Write to temp CSV and use align endpoint
                    import tempfile, csv as csv_mod
                    with tempfile.NamedTemporaryFile(
                        mode="w", suffix=".csv", delete=False
                    ) as tmp:
                        writer = csv_mod.writer(tmp)
                        for t_ns, v in zip(meas_data["time_ns"], meas_data["voltage_v"]):
                            writer.writerow([t_ns, v])
                        meas_file = tmp.name

            if meas_file and sim_vcd_file:
                r = await client.post(
                    f"{instrument_mcp_url}/tools/align_with_simulation",
                    json={
                        "meas_file":    meas_file,
                        "sim_vcd_file": sim_vcd_file,
                        "sim_signal":   sim_signal,
                        "meas_channel": meas_channel,
                        "meas_vendor":  meas_vendor,
                    },
                )
                if r.status_code == 200:
                    alignment = r.json()

                # Classify differences
                if alignment and "diff_v" in alignment:
                    r2 = await client.post(
                        f"{instrument_mcp_url}/tools/classify_differences_tool",
                        json={
                            "diff_v":          alignment["diff_v"],
                            "time_ns":         alignment["time_ns"],
                            "sim_v":           alignment["sim_v_aligned"],
                            "meas_v":          alignment["aligned_meas_v"],
                            "clock_period_ns": clock_period_ns,
                        },
                    )
                    if r2.status_code == 200:
                        raw_diff_findings = r2.json()

    else:
        # Direct implementation (no MCP server)
        try:
            from instrument_mcp.aligner import cross_correlate_align
            from instrument_mcp.readers.generic import read_csv_auto
            from instrument_mcp.readers.rigol import read_rigol_csv
            from waveform_mcp.parser import parse_waveform as parse_vcd

            if meas_file:
                meas_data = read_csv_auto(meas_file, channel=meas_channel - 1)
            else:
                return {"error": "No measurement source provided (meas_file or scpi_resource)"}

            if sim_vcd_file:
                vcd_meta = parse_vcd(sim_vcd_file)
                sig = vcd_meta.signals.get(sim_signal)
                if sig is None:
                    matches = [n for n in vcd_meta.signals if sim_signal in n]
                    sig = vcd_meta.signals[matches[0]] if matches else None
                if sig:
                    sim_tv = sig.tv
                    sim_time_ns = [t for t, _ in sim_tv]
                    sim_voltage = []
                    for _, v in sim_tv:
                        try:
                            sim_voltage.append(float(int(v, 2)) if set(v).issubset("01xz") else float(v))
                        except (ValueError, TypeError):
                            sim_voltage.append(0.0)

                    alignment = cross_correlate_align(
                        sim_time_ns=sim_time_ns,
                        sim_voltage=sim_voltage,
                        meas_time_ns=meas_data["time_ns"],
                        meas_voltage=meas_data["voltage_v"],
                    )

            # Classify
            if alignment and "diff_v" in alignment:
                from instrument_mcp.server import classify_differences_tool
                import asyncio
                raw_diff_findings = await classify_differences_tool(
                    diff_v=alignment["diff_v"],
                    time_ns=alignment["time_ns"],
                    sim_v=alignment["sim_v_aligned"],
                    meas_v=alignment["aligned_meas_v"],
                    clock_period_ns=clock_period_ns,
                )
        except ImportError as exc:
            alignment = {"error": f"instrument_mcp or waveform_mcp not available: {exc}"}

    # ── Step 4: Enrich findings ───────────────────────────────────────────────
    findings = enrich_findings(raw_diff_findings)
    summary  = summarize_findings(findings)
    finding_dicts = [f.to_dict() for f in findings]

    # ── Step 5: LLM root cause attribution ───────────────────────────────────
    llm_analysis: dict = {}
    if findings:
        duration_ns = (alignment.get("time_ns") or [0, 0])
        duration_ns = duration_ns[-1] - duration_ns[0] if len(duration_ns) > 1 else 0

        prompt = INSTRUMENT_ANALYZE_PROMPT.format(
            offset_ns=alignment.get("offset_ns", 0),
            offset_samples=alignment.get("offset_samples", 0),
            correlation_peak=alignment.get("correlation_peak", 0),
            diff_findings_json=json.dumps(finding_dicts, indent=2),
            signal_name=sim_signal or "unknown",
            duration_ns=duration_ns,
            clock_period_ns=clock_period_ns,
        )

        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f"{router_url}/chat",
                json={
                    "messages":    [{"role": "user", "content": prompt}],
                    "system":      INSTRUMENT_ANALYZE_SYSTEM,
                    "model":       model,
                    "temperature": 0.2,
                    "stream":      False,
                },
            )
            if resp.status_code == 200:
                raw = resp.json()["content"]
                raw = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.MULTILINE)
                raw = re.sub(r"```\s*$", "", raw.strip(), flags=re.MULTILINE)
                try:
                    llm_analysis = json.loads(raw.strip())
                except json.JSONDecodeError:
                    llm_analysis = {"raw": raw}

    # ── Step 6: Build Markdown report ────────────────────────────────────────
    report_lines = [
        "## InstrumentAnalyze Report",
        "",
        f"**Signal:** `{sim_signal or 'unknown'}`  |  "
        f"**Overall:** {summary['overall'].upper()}",
        "",
        f"### Alignment",
        f"- Time offset: **{alignment.get('offset_ns', 'N/A')} ns**",
        f"- Correlation: **{alignment.get('correlation_peak', 'N/A')}**",
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
        for f in llm_analysis["findings"]:
            report_lines.append(
                f"- **{f.get('diff_type', '?')}**: {f.get('action', '')} "
                f"[RTL change: {f.get('rtl_change', '?')}]"
            )

    return {
        "alignment":     alignment,
        "diff_findings": finding_dicts,
        "summary":       summary,
        "llm_analysis":  llm_analysis,
        "report_md":     "\n".join(report_lines),
    }
