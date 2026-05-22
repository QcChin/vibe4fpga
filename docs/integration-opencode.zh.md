# 接入 —— OpenCode

> 🌐 **中文** · [English](integration-opencode.md)

## 前置条件

* **uv** 0.4+
* **Python** 3.11 或 3.12
* **OpenCode**（[安装](https://opencode.ai)）
* 可选：跑 `waveform-mcp-rs` 需要 **Rust stable**；跑 eda-bridge / quartus / yosys / instrument 需要对应 **Vivado / Quartus / Yosys / pyvisa**

## 装上各 MCP

```bash
make install
```

或者逐个 MCP：在每个包目录跑 `uv tool install .`。

## 配置 OpenCode

OpenCode 从 `%APPDATA%\opencode\config.json`（Windows）或 `~/.config/opencode/config.json`（macOS/Linux）读 MCP 配置。也可以通过工程根目录的 `.opencode/opencode.json` 做工程级配置。

生成器输出在 [`configs/opencode-mcp-config.yaml`](../configs/opencode-mcp-config.yaml) —— 把 `mcp:` 块贴到你的 JSON 配置里（YAML 转 JSON），如果你的 OpenCode 版本支持 YAML 就直接保持 YAML：

```json
{
  "mcp": {
    "fpga-project-mcp": { "type": "stdio", "command": ["fpga-project-mcp"] },
    "eda-bridge-mcp":   { "type": "stdio", "command": ["eda-bridge-mcp"] },
    "waveform-mcp-rs":  { "type": "stdio", "command": ["waveform-mcp-rs"] },
    "instrument-mcp":   { "type": "stdio", "command": ["instrument-mcp"] },
    "datasheet-mcp":    { "type": "stdio", "command": ["datasheet-mcp"],
                          "env": { "OPENAI_API_KEY": "${OPENAI_API_KEY}" } },
    "quartus-mcp":      { "type": "stdio", "command": ["quartus-mcp"],
                          "env": { "QUARTUS_ROOTDIR": "C:\\intelFPGA_lite\\23.1\\quartus" } },
    "yosys-mcp":        { "type": "stdio", "command": ["yosys-mcp"] },
    "verify-mcp":       { "type": "stdio", "command": ["verify-mcp"] }
  }
}
```

OpenCode 会把 `${FOO}` 从进程环境变量展开，所以在拉 `opencode` 之前先 export `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` 就是最干净的喂凭证方式。

## 命令（Skills）

仓库带一份 `.opencode/commands/`，每个 LLM 驱动工具对应一个 `command.md`。把这个目录复制到工程根（或者直接 clone 本仓库），OpenCode 就把每个技能暴露成一个斜杠命令：

```
/spec2rtl         → fpga-project-mcp.spec_to_rtl
/review           → fpga-project-mcp.review_rtl
/timing           → fpga-project-mcp.suggest_timing_fix
/debug-wave       → waveform-mcp-rs.debug_waveform
/analyze-diff     → instrument-mcp.analyze_instrument_diff
/gen-tb           → verify-mcp.generate_testbench
/score-verify     → verify-mcp.score_verification
```

每个命令文件标注了后端 MCP + 工具、需要的环境变量、简短描述 —— 用户按 tab 时 OpenCode 全用上。

## 验证

```bash
opencode
```

然后：

```
/mcp list
```

应该列出 8 个 server 全部 `connected`。试一下：

```
/spec2rtl Write a 4-bit saturating counter with synchronous reset.
```

预期 OpenCode 转发给 `fpga-project-mcp.spec_to_rtl`，把 RTL + 自检报告返回。

## 排错

| 现象 | 原因 | 修法 |
| --- | --- | --- |
| 斜杠命令没列出来 | `.opencode/commands/` 不在 OpenCode 当前的工程里 | 在 workspace 里 clone 本仓库，或者把 `.opencode/commands/` 软链到你的工程根 |
| `mcp.fpga-project-mcp: exited immediately` | MCP 起来就崩 | 直接跑 binary（`fpga-project-mcp`）看 stderr；最常见是缺 Python 依赖或 Python 版本不对 |
| Windows 上工具输出乱码 | GBK 控制台、非 UTF-8 的 stdout | 已经被 `vibe4fpga-platform` 接管；还有问题就在拉 OpenCode 之前 `set PYTHONUTF8=1` |
| `AuthError: ANTHROPIC_API_KEY is not set` | OpenCode 启动前环境变量没 export | `$env:ANTHROPIC_API_KEY = "sk-..."` 然后重启 opencode |
