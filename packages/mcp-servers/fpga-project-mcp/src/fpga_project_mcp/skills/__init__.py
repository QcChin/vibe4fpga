"""Skill implementations absorbed from the retired ``packages/skills/`` package.

Each subpackage hosts the prompts + pure-Python logic for one skill that
was previously coordinated by the FastAPI llm-router. Phase B rewires them
to call ``vibe4fpga_llm_client.adapter_from_env`` directly and exposes each
as an ``@mcp.tool()`` in :mod:`fpga_project_mcp.server`.

Current skills:
    * :mod:`.spec2rtl`     — natural-language spec → synthesizable Verilog
    * :mod:`.code_review`  — static + LLM lint for Verilog/VHDL
    * :mod:`.timing_fix`   — timing-report driven fix suggestions
"""
