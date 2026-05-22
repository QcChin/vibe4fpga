# 端到端演示走查

> 🌐 **中文** · [English](demo-walkthrough.md)

一个能马上跑、约 10 分钟的实战演示，按顺序串联 8 个 MCP 中的 5 个。被测对象是仓库里的小 [`saturating_counter`](../examples/saturating_counter/)。

**前置条件：**
* MCP 已装好（`make install`）
* Claude Code / Codex / OpenCode 已用 `configs/` 中的片段配置好
* `brew install icarus-verilog`（macOS）或 oss-cad-suite（Windows）
* 环境变量里有 `ANTHROPIC_API_KEY`

下面每一步都是你在 agent 宿主里要输入的 prompt。Agent 自己把每个调用路由到对应的 MCP 工具——你不直接调工具。

---

## 1 · 从 spec 生成 RTL

**Prompt：**
> Use `spec_to_rtl` to build me a 4-bit up/down saturating counter.
> It should clamp at `4'hF` when counting up and `4'h0` when counting
> down rather than wrapping. Expose `at_max` / `at_min` status flags.
> Active-low synchronous reset, 100 MHz target clock.

**内部发生了什么：**
* `fpga-project-mcp.spec_to_rtl` 跑 5 阶段流水线
  * 阶段 1 —— 把英文 spec 解析成 `DesignIntent` JSON
  * 阶段 2 —— 标注歧义；饱和计数器无歧义，所以不会有阻塞性提问
  * 阶段 3 —— 扫描当前项目目录（如果打开了一个）寻找命名规范
  * 阶段 4 —— 以 temperature=0.1 生成 RTL
  * 阶段 5 —— 第二次 LLM 调用做自检；最多 2 轮修复

**你拿到什么：**
一个 `Spec2RTLResult` dict，字段包括 `rtl_code`、`self_check`、`score`，以及自主决策的审计轨迹。生成的 RTL 应该和 [`examples/saturating_counter/saturating_counter.v`](../examples/saturating_counter/saturating_counter.v) 接近。

---

## 2 · 评审生成的 RTL

**Prompt：**
> Now run `review_rtl` on what you just produced.

**内部发生了什么：**
* `fpga-project-mcp.review_rtl` 把 RTL 喂进 FPGA 10 大坑清单（锁存器推断、CDC、多驱动、复位覆盖、阻塞 / 非阻塞、initial 块、敏感列表、有符号 / 无符号混用、悬空端口）
* 返回 `{findings: [...], error_count, warning_count, summary}`

干净的计数器应该 findings 列表为空。spec2rtl 阶段 5 自检漏掉的细微问题（比如 `at_max` 复位是组合逻辑而非时序），code_review 通常能补救。

---

## 3 · 生成 Testbench

**Prompt：**
> Generate a self-checking testbench for the counter with
> `generate_testbench`. Cover reset recovery, saturation at both bounds,
> and the enable-held-low case. Target iverilog.

**内部发生了什么：**
* `verify-mcp.generate_testbench` 构造一个 SystemVerilog testbench，含时钟生成、复位序列、定向场景、SVA 断言、covergroup、看门狗
* 输出遵循 `$display("PASS")` / `$display("FAIL")` 约定，仿真日志解析器能据此评分

参考 testbench 在 [`examples/saturating_counter/tb_saturating_counter.sv`](../examples/saturating_counter/tb_saturating_counter.sv)。

---

## 4 · 跑仿真

**Prompt：**
> Write both files to disk as `saturating_counter.v` and
> `tb_saturating_counter.sv`, then call `run_simulation` with
> simulator=icarus.

**内部发生了什么：**
* Agent 宿主用自带的写文件工具把两份文件落盘
* `eda-bridge-mcp.run_simulation` 调 `iverilog -g2012` 编译、`vvp` 运行，抓 stdout/stderr
* `parse_sim_log` 解读输出：
  * 计 PASS / FAIL / `$error` / `$fatal` / 断言标记数
  * 暴露最多 10 行违例
  * 把计数压成单一 `verdict` ∈ `{pass, fail, error, unclear}`

**返回：**
```json
{
  "success":  true,
  "verdict":  "pass",
  "summary":  { "pass_count": 7, "fail_count": 0, ... },
  "stdout":   "...",
  "vcd_path": null
}
```

一个有 `$finish` 但没任何 PASS 标记的运行会被判 `verdict=unclear`，不会被静默放过—— 这是 `fix: critical post-review bugs` 那次修掉的、code review 发现的漏洞。

---

## 5 · 测试失败时 —— 调试波形

假设 testbench 报了 `verdict=fail`，问：

> Parse the VCD at `saturating_counter.vcd` with `debug_waveform` and tell
> me which signal misbehaved. Focus on the count and direction signals.

**内部发生了什么：**
* `waveform-mcp-rs.debug_waveform` 走 Rust parser 载入 VCD
* 在 tokio 上并发跑 5 个确定性检测器（毛刺、X/Z、CDC、AXI 握手、停滞超时）
* LLM 推理层（内置 Anthropic 客户端）把检测器结果跟 spec 关联 —— 无 anomaly 时自动跳过

---

## 6 · 给整次运行打分

**Prompt：**
> Score this verification run with `score_verification`: use the sim log
> you captured above, plus the empty lint/formal/synth reports for now.
> Include the original spec as the compliance check.

**内部发生了什么：**
* `verify-mcp.score_verification` 跑 5 阶段：
  1. lint 报告解析（确定性正则）
  2. sim 日志解析（复用 eda-bridge-mcp 的约定）
  3. formal 报告解析
  4. synth 报告解析（WNS 抽取）
  5. LLM 对照 RTL 做 spec 合规检查
* 返回 `{stages, score, verdict, overall_verdict, breakdown, notes, report_md}`

判定阈值：**PASS ≥ 85**、**REVIEW 60-84**、**FAIL < 60**。

---

## 这里没演示的部分

* **时序闭环** —— `suggest_timing_fix` 能读 Vivado 报告给建议，但不会重新综合。用户得自己应用建议再手工调一次 `run_synthesis`。
* **完整 Xilinx 流程** —— 需要 Vivado。`eda-bridge-mcp.run_synthesis` 把 TCL 都接好了，但 PnR → 比特流不在本 demo 范围。
* **iCE40 开源流程** —— `yosys-mcp` 能从 RTL 走到比特流，但需要 PATH 上有 oss-cad-suite。
* **示波器关联** —— `instrument-mcp.analyze_instrument_diff` 需要实物示波器（或 CSV 抓波）和 `pyvisa` 后端。

完整的 MCP 清单 + 每个能力归谁见 [`docs/architecture.md`](architecture.md)。

---

## 不接 agent 宿主也能离线验证

```bash
# 1. 跑参考 testbench 端到端
cd examples/saturating_counter
iverilog -g2012 -o tb.vvp saturating_counter.v tb_saturating_counter.sv
vvp tb.vvp

# 2. 用管道塞 stdio JSON-RPC 直接调 run_simulation
#    （正常使用让 Claude Code / Codex / OpenCode 来做就行。）
cd ../..
eda-bridge-mcp <<'REQ'
{"jsonrpc":"2.0","id":1,"method":"tools/call",
 "params":{"name":"run_simulation",
           "arguments":{
             "project_path":"/abs/path/examples/saturating_counter",
             "testbench":"/abs/path/examples/saturating_counter/tb_saturating_counter.sv",
             "source_files":["/abs/path/examples/saturating_counter/saturating_counter.v"],
             "simulator":"icarus"}}}
REQ
```

（前面那次握手 + `initialize` 调用通常由 agent 宿主代办；上面这种裸调主要给 debug 用。）
