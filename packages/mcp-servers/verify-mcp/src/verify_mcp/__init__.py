"""verify-mcp — testbench generation and multi-stage verification scoring.

Absorbs the ``testbench_gen`` and ``verification`` skills from the retired
``packages/skills/`` package. Exposes:

* ``generate_testbench`` — LLM-driven SystemVerilog testbench synthesis
* ``score_verification`` — multi-stage (lint / sim / formal / synth / spec)
  pass-rate scoring + natural-language report
"""

__version__ = "0.1.0"
