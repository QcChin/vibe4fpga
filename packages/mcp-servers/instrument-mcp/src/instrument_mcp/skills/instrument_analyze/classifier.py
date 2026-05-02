"""Difference classification engine for sim vs. real-measurement analysis.

Classification table (from design doc Section 5.2):

Difference Type          | Classification | Waveform Characteristic
-------------------------|----------------|-------------------------------
Rise time / overshoot    | expected       | Peak at edge, near-zero stable
Frequency deviation PPM  | expected       | Linear phase drift
HF noise / EMI           | expected       | Broadband random overlay
Systematic timing offset | suspicious     | Uniform N-cycle advance/delay
Nonlinear frequency drift| suspicious     | Nonlinear phase offset
Logic event missing      | anomalous      | Sim pulse, meas absent
Amplitude / value error  | anomalous      | Correct timing, wrong value
"""

from __future__ import annotations

from dataclasses import dataclass


CLASSIFICATION_LABELS = {
    "expected":  "Expected physical effect — RTL likely correct",
    "suspicious": "Suspicious — possible design issue, needs investigation",
    "anomalous":  "Anomalous — likely a design or implementation error",
}

LLM_CONCLUSION_PATTERNS = {
    "rise_time_overshoot":  "RTL correct; physical effect. Adjust DRIVE strength / output impedance.",
    "freq_deviation_ppm":   "Crystal oscillator precision error. No RTL change needed.",
    "hf_noise_emi":         "PCB routing or power supply issue. Not an RTL problem.",
    "systematic_offset":    "Check pipeline stages or clock division ratio in RTL.",
    "nonlinear_freq_drift": "Check division counter reset logic and overflow handling.",
    "missing_logic_event":  "Constraint file error or condition signal not met. Use ILA to verify.",
    "amplitude_error":      "Check data path truncation/shift operations (e.g. >> bit-width mismatch).",
    "dc_offset":            "Check DAC/ADC reference voltage or measurement probe calibration.",
    "hf_noise_emi":         "PCB layout / decoupling capacitor issue. No RTL fix required.",
    "unclassified":         "Manual inspection recommended. Consider capturing more cycles.",
}


@dataclass
class DiffFinding:
    diff_type: str
    classification: str   # "expected" | "suspicious" | "anomalous"
    evidence: str
    suggestion: str
    severity: str         # "info" | "warning" | "error"

    def to_dict(self) -> dict:
        return {
            "diff_type":      self.diff_type,
            "classification": self.classification,
            "label":          CLASSIFICATION_LABELS.get(self.classification, ""),
            "evidence":       self.evidence,
            "suggestion":     self.suggestion,
            "severity":       self.severity,
            "conclusion":     LLM_CONCLUSION_PATTERNS.get(self.diff_type, ""),
        }


def severity_from_classification(classification: str) -> str:
    return {
        "expected":  "info",
        "suspicious": "warning",
        "anomalous":  "error",
    }.get(classification, "info")


def enrich_findings(raw_findings: list[dict]) -> list[DiffFinding]:
    """Convert raw classifier output (from instrument-mcp) to DiffFinding objects."""
    findings: list[DiffFinding] = []
    for f in raw_findings:
        diff_type      = f.get("diff_type", "unclassified")
        classification = f.get("classification", "suspicious")
        findings.append(DiffFinding(
            diff_type=diff_type,
            classification=classification,
            evidence=f.get("evidence", ""),
            suggestion=f.get("suggestion", LLM_CONCLUSION_PATTERNS.get(diff_type, "")),
            severity=severity_from_classification(classification),
        ))
    return findings


def summarize_findings(findings: list[DiffFinding]) -> dict:
    """Aggregate finding statistics."""
    by_class: dict[str, int] = {"expected": 0, "suspicious": 0, "anomalous": 0}
    for f in findings:
        by_class[f.classification] = by_class.get(f.classification, 0) + 1

    overall = (
        "anomalous"   if by_class["anomalous"]  > 0 else
        "suspicious"  if by_class["suspicious"] > 0 else
        "expected"
    )

    return {
        "total_findings": len(findings),
        "by_classification": by_class,
        "overall": overall,
        "overall_label": CLASSIFICATION_LABELS.get(overall, ""),
    }
