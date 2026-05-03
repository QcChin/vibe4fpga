"""SCPI over LAN/USB via pyvisa — universal instrument direct connection.

Supports all instruments that implement SCPI (Standard Commands for
Programmable Instruments) via VISA resource strings:
  LAN:  "TCPIP0::192.168.1.5::INSTR"
  USB:  "USB0::0x1AB1::0x04CE::DS1ZA::INSTR"  (Rigol)
  GPIB: "GPIB0::5::INSTR"

Common SCPI waveform capture commands (instrument-agnostic):
  *IDN?                          — identify instrument
  :WAV:SOUR CH1                  — select channel (Rigol/Keysight style)
  :WAV:MODE NORM                 — waveform mode
  :WAV:FORM ASCII                — ASCII data format
  :WAV:DATA?                     — download waveform data
  :WAV:XINC?                     — time increment per point (seconds)
  :WAV:XORI?                     — time origin
  :WAV:YINC?                     — voltage increment per point
  :WAV:YORI?                     — voltage origin
  :WAV:YREF?                     — voltage reference level
  TRIG:STAT?                     — trigger status
"""

from __future__ import annotations

from vibe4fpga_platform import scratch_file


def _require_pyvisa():
    """Import pyvisa with a structured error message when no backend is loaded.

    Per risk R3 in the refactor plan: NI-VISA and Keysight VISA drivers are
    vendor-specific and Windows-only. We do *not* swallow the ImportError —
    callers see a ``RuntimeError`` with driver pointers so the host can
    surface it to the user verbatim.
    """
    try:
        import pyvisa
        return pyvisa
    except ImportError as exc:
        raise RuntimeError(
            "pyvisa backend not available. Install one of:\n"
            "  * NI-VISA:       https://www.ni.com/visa (Windows admin install)\n"
            "  * Keysight VISA: https://www.keysight.com/find/iosuite (Windows)\n"
            "  * Pure-Python:   pip install pyvisa pyvisa-py pyusb libusb1"
        ) from exc


def connect(resource_string: str, timeout_ms: int = 5000) -> dict:
    """Open a VISA connection and identify the instrument.

    Args:
        resource_string: VISA resource string, e.g. "TCPIP0::192.168.1.5::INSTR"
        timeout_ms:      Query timeout in milliseconds.

    Returns:
        {"connected": bool, "idn": str, "resource": str}
    """
    pyvisa = _require_pyvisa()
    rm = pyvisa.ResourceManager()
    try:
        instr = rm.open_resource(resource_string)
        instr.timeout = timeout_ms
        idn = instr.query("*IDN?").strip()
        instr.close()
        return {"connected": True, "idn": idn, "resource": resource_string}
    except Exception as exc:
        return {"connected": False, "error": str(exc), "resource": resource_string}


def list_instruments() -> list[str]:
    """List all VISA-accessible instruments on the system."""
    pyvisa = _require_pyvisa()
    rm = pyvisa.ResourceManager()
    try:
        return list(rm.list_resources())
    except Exception:
        return []


def capture_waveform(
    resource_string: str,
    channel: int = 1,
    timeout_ms: int = 10000,
) -> dict:
    """Capture a waveform from the instrument via SCPI.

    Uses the Rigol/Keysight/SIGLENT compatible :WAVeform: subsystem.
    Falls back to SAVe:WAVEform CSV for Tektronix.

    Args:
        resource_string: VISA resource string.
        channel:         Channel number (1-based).
        timeout_ms:      SCPI timeout.

    Returns:
        {time_ns, voltage_v, sample_rate_hz, duration_ns, channel, idn}
    """
    pyvisa = _require_pyvisa()
    rm = pyvisa.ResourceManager()

    # Wrap open_resource in try/except — instruments may be off or unreachable
    try:
        instr = rm.open_resource(resource_string)
    except Exception as exc:
        return {"error": f"Cannot open VISA resource '{resource_string}': {exc}"}

    instr.timeout = timeout_ms

    try:
        idn = instr.query("*IDN?").strip()
        ch_name = f"CH{channel}"

        # Try Rigol/Keysight WAV: subsystem first
        try:
            instr.write(f":WAV:SOUR {ch_name}")
            instr.write(":WAV:MODE NORM")
            instr.write(":WAV:FORM ASCII")

            x_inc = float(instr.query(":WAV:XINC?"))
            x_ori = float(instr.query(":WAV:XORI?"))
            y_inc = float(instr.query(":WAV:YINC?"))
            y_ori = float(instr.query(":WAV:YORI?"))
            y_ref = float(instr.query(":WAV:YREF?"))

            raw_data = instr.query(":WAV:DATA?").strip()

            # Parse: may start with '#' length header (IEEE 488.2 block)
            if raw_data.startswith("#"):
                n_digits = int(raw_data[1])
                raw_data = raw_data[2 + n_digits:]

            values = [float(v) for v in raw_data.split(",") if v.strip()]
            voltage_v = [(v - y_ref) * y_inc + y_ori for v in values]
            time_ns   = [(x_ori + i * x_inc) * 1e9 for i in range(len(voltage_v))]

        except Exception:
            # Fallback: SAVe:WAVEform CSV (Tektronix style). The scratch file
            # is created inside the shared ``vibe4fpga_*`` scratch root and is
            # cleaned up automatically on interpreter shutdown — no /tmp, no
            # manual unlink, works identically on Windows and macOS.
            try:
                tmp_path = scratch_file(".csv")
                instr.write(f'SAV:WAV {ch_name},CSV,"{tmp_path}"')

                # *OPC? blocks until operation complete; guard with a tighter timeout
                instr.timeout = min(timeout_ms, 30_000)
                try:
                    instr.query("*OPC?")
                except Exception:
                    pass   # Tektronix may not ack; check file presence instead

                from instrument_mcp.readers.generic import read_csv_auto
                result = read_csv_auto(str(tmp_path), channel=channel - 1)
                return {**result, "idn": idn, "vendor": "scpi_fallback"}
            except Exception as fallback_exc:
                return {"error": f"SCPI WAV capture failed and CSV fallback also failed: {fallback_exc}"}

        duration_ns = time_ns[-1] - time_ns[0] if len(time_ns) > 1 else 0.0
        sample_rate = (len(time_ns) - 1) / (duration_ns * 1e-9) if duration_ns > 0 else 0.0

        return {
            "time_ns":        time_ns,
            "voltage_v":      voltage_v,
            "sample_rate_hz": round(sample_rate),
            "duration_ns":    round(duration_ns, 3),
            "channel":        channel,
            "idn":            idn,
            "vendor":         "scpi",
            "points":         len(time_ns),
        }

    finally:
        try:
            instr.close()
        except Exception:
            pass
