# vibe4fpga 扩展 MCP 服务分析报告

> 🌐 **中文** · [English](additional-mcp-services-analysis.md)

> 文档版本: 2026-04-07
> 适用项目: vibe4fpga (AI 辅助 FPGA 开发 IDE)
>
> **注**: 此文档中提到的 `waveform-mcp` 在 v0.3.0 已被吸收合并到 Rust crate
> `waveform-mcp-rs`，所有 7 个工具 (parse / extract / stats / summarize /
> decode_axi / map_signal_to_rtl / debug_waveform) 都在 Rust 侧。下文为历史
> 设计描述，工具能力不变，仅实现语言改为 Rust。

---

## 背景与现有能力回顾

vibe4fpga 目前已实现的 MCP 服务层覆盖了从 RTL 生成到硬件测量的核心链路：

| 现有 MCP 服务 | 核心能力 |
|---|---|
| `fpga-project-mcp` | 项目文件索引、模块依赖图、信号跨文件搜索、命名惯例分析 |
| `waveform-mcp` | VCD/FST 波形解析、三级压缩、AXI4 解码、信号-RTL 映射 |
| `datasheet-mcp` | LlamaIndex+Qdrant RAG、数据手册/协议规范/IP 接口检索 |
| `eda-bridge-mcp` | Vivado 综合/仿真控制、Lint、时序报告解析 |
| `instrument-mcp` | 示波器 CSV 读取、SCPI 实时采集、FFT、仿真-实测对齐 |
| `quartus-mcp` | QSF 管理、Quartus 编译/时序分析、USB-Blaster 烧录 |
| `yosys-mcp` | Yosys iCE40/ECP5 综合、nextpnr 布局布线、SymbiYosys 形式验证准备 |

尽管上述服务覆盖了主流开发场景，实际 FPGA 开发生命周期中仍存在若干关键环节缺乏 MCP 层支撑。以下分析针对 **10 个高价值扩展 MCP 服务** 展开，每个服务均从「工具列表、技术集成、价值主张」三个维度详述。

---

## 新增 MCP 服务详细分析

### 1. `constraint-mcp` — 约束文件智能管理服务

#### 背景痛点

时序约束 (XDC/SDC) 是 FPGA 实现质量的核心决定因素，却也是工程师最容易出错的环节。错误或遗漏的约束会导致综合通过但上板行为异常。目前 `eda-bridge-mcp` 仅能解析时序报告结果，但对约束文件的生成、验证、覆盖率分析均无支持。

#### 工具列表

```
parse_xdc(xdc_file)
    → 解析 Xilinx XDC 约束文件，返回时钟定义、输入输出延迟、多周期路径、
      伪路径列表及各约束的影响端口集合

parse_sdc(sdc_file)
    → 解析 Synopsys SDC 格式约束（适用于 Quartus TimeQuest、ECP5、ASIC 流程）

generate_clock_constraints(rtl_files, top_module, target_freq_mhz)
    → 从 RTL 信号分析中自动识别时钟端口，生成 create_clock / create_generated_clock
      初稿，包含推荐频率、抖动余量

check_constraint_coverage(xdc_file, synthesis_netlist)
    → 分析网表中所有时序路径，统计被约束覆盖比例；
      返回 uncovered_paths, over_constrained_paths, coverage_pct

suggest_cdc_constraints(rtl_files, waveform_file)
    → 结合 RTL 静态分析与波形动态数据，识别跨时钟域路径，
      为每条 CDC 路径推荐 set_false_path 或 set_max_delay -datapath_only

validate_timing_exceptions(xdc_file, timing_report)
    → 交叉验证 XDC 中的多周期路径和伪路径声明是否与实际时序报告匹配，
      标记「声明了异常但该路径不存在」或「未声明但可能需要异常」的情况

diff_constraints(xdc_old, xdc_new)
    → 对比两个版本的约束文件，高亮新增/删除/修改的约束及潜在影响
```

#### 技术集成

- **解析层**: 自研轻量 XDC/SDC 解析器（正则 + 状态机，无需 EDA 工具在场），覆盖 Vivado XDC 语法子集
- **网表分析**: 读取 Vivado 导出的 `.edf` 或 Yosys JSON 网表，构建时序弧图
- **覆盖率计算**: 使用 NetworkX 对时序弧图做路径遍历，与约束条目做集合覆盖运算
- **CDC 检测**: 复用 `waveform-mcp` 的 CDC 检测结果 + RTL 静态端口追踪

#### 价值主张

> 将约束错误从「烧板后发现」提前到「RTL 提交前发现」。自动约束生成可节省资深工程师 2-4 小时的重复性工作；约束覆盖率分析可捕获遗漏约束导致的虚假时序通过问题，显著降低第一次板级测试失败率。

---

### 2. `power-mcp` — 功耗分析与优化服务

#### 背景痛点

功耗是 FPGA 设计的硬性约束，尤其在嵌入式/低功耗场景下。Vivado Power Analyzer 和 Quartus PowerPlay 能出报告，但将报告与 RTL 设计决策关联、给出可操作的优化建议，目前完全依赖工程师手工分析。

#### 工具列表

```
parse_power_report(report_file, tool)
    → 解析 Vivado (.xpr) 或 Quartus (.pow) 功耗报告，
      返回 {static_mw, dynamic_mw, clock_mw, io_mw, logic_mw, bram_mw, dsp_mw}

estimate_switching_activity(vcd_file, netlist_file)
    → 从仿真 VCD 统计各信号翻转率，导入网表得到各单元动态功耗估计
      (替代 Vivado 的 .saif 流程，适用于无 XSim 环境)

generate_power_constraints(activity_data, target_power_mw)
    → 生成 Vivado XDC set_switching_activity 语句，
      为关键模块指定精确翻转率，提升功耗仿真精度

identify_power_hotspots(power_report, utilization_report)
    → 定位功耗热点模块/时钟域，输出 Top-N 高功耗层次路径
      并关联到 RTL 源文件位置

suggest_power_optimizations(rtl_files, power_report)
    → 基于规则库给出 RTL 级优化建议：
      时钟门控 (clock gating)、操作数隔离 (operand isolation)、
      寄存器级功耗门控、DSP 代替 LUT 乘法器 等

compare_power_scenarios(report_a, report_b, label_a, label_b)
    → 对比两个实现版本的功耗分布，量化优化效果
```

#### 技术集成

- **报告解析**: 针对 Vivado XML 功耗报告和 Quartus `.pow` 格式各自实现解析器
- **翻转率统计**: 基于 `waveform-mcp` 的波形数据，在 Python 层计算每个信号的边沿密度
- **优化规则库**: 编码 FPGA 功耗优化的 30+ 条模式规则，结合 LLM 进行上下文推理
- **热点关联**: 通过模块名匹配将功耗报告层次路径映射到 `fpga-project-mcp` 的模块树

#### 价值主张

> 功耗优化从「综合后人工查报告」变为「RTL 阶段 AI 主动建议」。对于电池供电或散热受限场景，可将迭代周期从数天缩短到数小时，并将功耗合规率提升 30% 以上。

---

### 3. `version-control-mcp` — 版本控制与设计演进追踪服务

#### 背景痛点

FPGA 设计以文件为中心，但现有工具链对 HDL 代码的语义化版本管理几乎为零——Git 只能告诉你文件变了，但无法回答「这次提交导致了哪些端口变化」「时序在哪个 commit 开始恶化」。

#### 工具列表

```
get_rtl_diff(project_path, commit_a, commit_b)
    → 基于 Git diff 提取 HDL 语义变化：
      新增/删除/修改的端口、参数、实例、信号
      (非行级 diff，而是模块级语义 diff)

track_timing_history(project_path, report_dir, n_commits)
    → 分析最近 N 次提交对应的时序报告，绘制 WNS/TNS/Fmax 趋势曲线
      并标注导致恶化的 commit SHA

blame_timing_violation(project_path, failing_path)
    → 给定一条失败的时序路径，追溯到最早引入该路径的 commit，
      并提取该 commit 的 RTL 变化摘要

create_design_snapshot(project_path, tag, metadata)
    → 为当前设计状态创建语义快照：记录时序分数、资源利用率、
      验证状态，并 git tag 关联，构成设计检查点 (checkpoint)

compare_snapshots(snapshot_a, snapshot_b)
    → 对比两个设计检查点的综合指标：时序/资源/功耗/验证覆盖率，
      生成 Markdown 对比报告

suggest_branch_strategy(project_path, goal)
    → 根据当前设计阶段和目标（如「开始时序优化」「准备 tape-out」）
      建议 Git 分支策略和里程碑检查点
```

#### 技术集成

- **Git 集成**: 使用 `gitpython` 库访问提交历史、diff、tag 操作
- **HDL 语义 Diff**: 在 `fpga-project-mcp` 的模块扫描基础上，对两次 scan 结果做结构化比较
- **历史报告关联**: 约定报告文件命名规范，按 commit hash 索引时序/功耗报告归档
- **检查点存储**: JSON + SQLite 轻量存储，记录每个快照的量化指标

#### 价值主张

> 将 FPGA 设计演进从「凭印象回忆」变为「数据驱动追溯」。时序恶化定位从数小时缩短到分钟级，设计回滚有据可依，也为团队代码审查提供客观的质量变化依据。

---

### 4. `ip-catalog-mcp` — IP 核目录管理与集成服务

#### 背景痛点

现代 FPGA 设计大量依赖厂商 IP（Xilinx Vivado IP Catalog、Intel IP Library）和第三方开源 IP，但 IP 的发现、版本管理、接口对接、参数配置往往需要在图形界面中手动完成，难以自动化。`datasheet-mcp` 虽有 IP 接口文档检索，但不能驱动实际 IP 配置与例化。

#### 工具列表

```
list_vivado_ips(vivado_install_path, filter_category)
    → 列举已安装的 Vivado IP 目录，支持按类别过滤（FIFO/Memory/Math/Clock/...）
      返回 {ip_name, vendor, version, categories, description}

get_ip_parameters(ip_vlnv, vivado_install_path)
    → 获取指定 IP 核的全部可配置参数列表及默认值/有效值范围
      (VLNV = Vendor:Library:Name:Version 标识符)

generate_ip_instantiation(ip_vlnv, parameters, module_name)
    → 根据参数配置生成 IP 例化 Verilog/SystemVerilog 代码片段，
      包含正确的端口映射和参数赋值

create_vivado_ip_tcl(ip_vlnv, parameters, output_dir)
    → 生成 Vivado create_ip + set_property TCL 脚本，
      用于无 GUI 的批处理 IP 配置流程

search_opencores_ip(description, protocol)
    → 在本地索引的 OpenCores / FuseSoC 库中语义搜索开源 IP，
      返回匹配的 IP 名称、许可证、接口类型

check_ip_compatibility(ip_a, ip_b, connection_map)
    → 验证两个 IP 的接口连接兼容性：数据位宽、时钟域、AXI 版本匹配检查

generate_ip_wrapper(ip_name, target_interface, project_conventions)
    → 为第三方 IP 生成与项目接口风格一致的封装模块 (wrapper)，
      处理信号重命名、位宽适配、时序域隔离
```

#### 技术集成

- **Vivado IP 元数据**: 解析 Vivado 安装目录下的 `.xml` IP 描述文件（`<vivado_root>/data/ip/`）
- **FuseSoC 集成**: 调用 `fusesoc` CLI 访问开源 IP 库索引
- **接口兼容性检查**: 基于规则引擎（AXI4/AXI4-Lite/AXI4-Stream 标准接口宽度/信号集合比对）
- **代码生成**: Jinja2 模板驱动的例化代码和 TCL 脚本生成

#### 价值主张

> IP 集成从「查文档 → 手配界面 → 手写例化」的数小时流程压缩到 LLM 一次对话完成。对于使用大量标准 IP 的系统级 FPGA 设计（如含 MIG/PCIe/AXI Interconnect 的 SoC），可节省 30-50% 的 IP 集成时间。

---

### 5. `formal-verify-mcp` — 形式化验证深度集成服务

#### 背景痛点

`yosys-mcp` 已有 `prepare_formal_verification` 工具（输出 SMT2 文件），`verification/pipeline.py` 的 Layer 3 也有 SymbiYosys 的存根实现，但注释明确写道「stub (full SymbiYosys integration in Phase 4)」。形式验证在 FPGA 场景中对协议状态机、安全属性、复位行为的验证极有价值，需要一个专门的 MCP 服务来填补这个空缺。

#### 工具列表

```
run_symbiyosys(sby_config_file, timeout_sec)
    → 执行 SymbiYosys 形式验证任务，解析输出，
      返回 {status: PASS/FAIL/UNKNOWN, property_results, counterexample_vcd}

generate_sby_config(rtl_files, top_module, mode, depth, engines)
    → 根据 RTL 文件和验证参数生成 .sby 配置文件
      mode: prove | cover | bmc
      engines: smtbmc | abc | aiger

generate_sva_properties(rtl_code, spec, property_types)
    → 使用 LLM 从自然语言规格生成 SVA 断言：
      property_types: [safety, liveness, protocol, reset, overflow]
      输出带 `ifdef FORMAL 保护的 .sv 属性文件

check_reset_behavior(rtl_files, top_module, reset_signal)
    → 形式验证复位正确性：确认复位后所有寄存器达到定义状态，
      无悬空 X 传播，复位域无 CDC 违规

verify_fifo_properties(rtl_files, fifo_module)
    → 专用 FIFO 形式验证：满/空标志正确性、读写指针关系、
      无溢出/下溢、计数值单调性

extract_counterexample(sby_output_dir)
    → 将 SymbiYosys 反例 VCD 提取并转换为人类可读的状态序列描述，
      供 LLM 进行根因分析

run_equivalence_check(golden_rtl, revised_rtl, top_module)
    → 使用 Yosys `equiv` 命令对两版 RTL 做组合/时序等价性验证，
      检测重构/优化是否意外改变行为
```

#### 技术集成

- **SymbiYosys**: 直接调用 `sby` CLI，解析 `output/` 目录下的日志和 VCD 文件
- **SVA 生成**: LLM（Claude/RTLCoder）+ 少样本提示，输出符合 IEEE 1800-2017 的 SVA 断言
- **等价验证**: 调用 `yosys -p "equiv_check"` 子命令，解析等价性结果
- **反例分析**: 复用 `waveform-mcp` 的 VCD 解析能力对反例波形进行结构化描述

#### 价值主张

> 形式验证能在仿真盲区（状态空间爆炸区域）找到隐藏 Bug，尤其适用于 FIFO、状态机、仲裁器等协议逻辑。与仿真相比，形式方法对安全关键属性提供数学级别的保证。本服务将形式验证的使用门槛从「需要专家编写 SVA 并配置工具链」降低到「自然语言描述属性，AI 完成其余工作」。

---

### 6. `board-bringup-mcp` — 板级调试与上板支持服务

#### 背景痛点

FPGA 设计流程最艰难的阶段往往是「板级 bring-up」：比特流已经可以下载，但板子上什么都不工作。这个阶段需要频繁操作 JTAG、读写寄存器、分析 ILA 数据、对照原理图排查，而这些动作目前在 vibe4fpga 中完全缺失。

#### 工具列表

```
scan_jtag_chain(interface, speed_khz)
    → 通过 OpenOCD / Vivado hw_server 扫描 JTAG 链，
      返回发现的器件列表 {idcode, ir_length, device_name}

read_register(interface, address, width_bits)
    → 通过 JTAG 或 UART 读取目标寄存器值
      支持: AXI4-Lite 地址空间、自定义 DR 扫描链

write_register(interface, address, value, mask)
    → 写目标寄存器，支持读改写掩码操作

capture_ila_data(vivado_project, ila_core_name, trigger_config, max_depth)
    → 配置并触发 Xilinx ILA (Integrated Logic Analyzer) 采集，
      等待触发后提取 VCD 格式波形数据交给 waveform-mcp 分析

parse_ila_ltx(ltx_file)
    → 解析 Vivado ILA 探针文件 (.ltx)，返回各探针信号名、位宽、时钟域

configure_signaltap(quartus_project, stp_file, trigger)
    → 配置 Intel SignalTap II 逻辑分析仪，下载捕获配置到 FPGA，
      等待触发并提取采样数据

analyze_uart_log(log_file_or_port, baud_rate, protocol)
    → 解析板级 UART 调试输出，识别已知错误模式
      支持: 裸文本、简单帧协议、SLIP 包

correlate_ila_with_simulation(ila_vcd, sim_vcd, signal_map)
    → 将 ILA 采集数据与仿真波形对齐比较，
      识别 「仿真通过但硬件失败」的信号差异，
      调用 instrument-mcp 的分类引擎进行根因归类
```

#### 技术集成

- **JTAG 访问**: OpenOCD（开源）或 Vivado `hw_server` TCL API（Xilinx 专有）
- **ILA 控制**: Vivado Tcl API (`open_hw_manager`/`run_hw_ila`) 批处理脚本
- **SignalTap**: Quartus `quartus_stp` 命令行工具
- **UART 解析**: `pyserial` + 协议状态机
- **波形关联**: 复用 `instrument-mcp` 的 `align_with_simulation` 和 `classify_differences_tool`

#### 价值主张

> 上板调试是整个 FPGA 开发周期中最依赖「经验直觉」的阶段，也是最难自动化的。本服务通过 AI 辅助的 JTAG 操作、ILA 数据智能分析、仿真-硬件比对，将板级调试从「黑盒摸索」变为「有数据支撑的系统性排查」，平均缩短 bring-up 周期 40-60%。

---

### 7. `perf-profiling-mcp` — 设计性能分析与资源优化服务

#### 背景痛点

`eda-bridge-mcp` 已能解析 Vivado 的时序报告（WNS/TNS），但对资源利用率的深度分析、关键路径的 RTL 溯源、LUT 层数优化建议、BRAM/DSP 推断策略等均无支持。工程师面对综合报告时仍需手工解读大量表格。

#### 工具列表

```
parse_utilization_detail(report_file, tool)
    → 深度解析利用率报告：不仅是总量，还包括按层次的分解、
      carry chain 使用率、SRL 使用情况、时钟缓冲资源

analyze_critical_path(timing_report, rtl_root)
    → 从时序报告中提取关键路径，溯源到 RTL 代码行，
      标注路径经过的逻辑层级数和每段延迟贡献

identify_resource_inefficiencies(utilization_report, rtl_files)
    → 识别资源使用低效模式：
      - 大量 LUT6 作为小型 MUX（应改用 casex/priority encoding）
      - BRAM 被推断为 LUTRAM（应加属性/改写法）
      - DSP 未被综合工具识别（乘加树结构问题）
      - FF 利用率远低于 LUT（存在组合逻辑宽瓶颈）

suggest_pipeline_insertion(timing_report, rtl_files, target_freq_mhz)
    → 对关键路径给出流水线插入建议：
      指出在哪条赋值语句前后插入寄存器级，
      估算插入后的时序改善量

compare_resource_usage(report_a, report_b)
    → 对比两个版本的资源利用率变化，量化每类资源的增减

estimate_floorplan_congestion(utilization_report, part)
    → 根据资源占用率估算布线拥塞风险，
      针对占用率 > 80% 的区域提出模块拆分/重组建议
```

#### 技术集成

- **报告解析**: 针对 Vivado HTML/XML 利用率报告和 Quartus Fitter 报告各自实现解析器
- **关键路径溯源**: 解析时序报告中的 `datapath` 行，通过网表单元名反向映射到 `fpga-project-mcp` 的模块信号
- **效率规则库**: 编码 Xilinx/Intel FPGA 资源推断的 20+ 条最佳实践规则
- **流水线建议**: 基于 LLM 代码理解 + 时序延迟数据，生成可直接插入的 RTL patch

#### 价值主张

> 将工程师从「盯着 300 行综合报告猜问题」解放出来。关键路径 RTL 溯源使时序收敛工作从「整体搜索」变为「精准外科手术」，配合 AgentLoop 可实现全自动的时序收敛迭代。

---

### 8. `cocotb-mcp` — Python 测试框架深度集成服务

#### 背景痛点

`eda-bridge-mcp` 支持 Icarus Verilog/Verilator/xsim 仿真，`testbench_gen` Skill 生成 SystemVerilog 测试台，但对基于 cocotb 的 Python 测试框架没有支持。cocotb 是现代 FPGA 验证的主流选择（支持 Python 协程、随机化、覆盖率收集、与 pytest 集成），其生成和执行能力的缺失是一个显著空缺。

#### 工具列表

```
generate_cocotb_test(rtl_module, spec, protocol, coverage_goals)
    → 使用 LLM 生成 cocotb Python 测试文件：
      包含 @cocotb.test() 装饰器测试函数、时钟驱动、
      随机化激励、断言检查、覆盖率 bin 定义

run_cocotb_test(test_file, dut_files, simulator, timeout_sec)
    → 执行 cocotb 测试套件，捕获 pytest 输出，
      返回 {passed, failed, errors, coverage_xml, vcd_path}

parse_coverage_report(coverage_xml)
    → 解析 cocotb-coverage 或 Verilator coverage XML 报告，
      返回功能覆盖率、行覆盖率、分支覆盖率摘要

identify_coverage_holes(coverage_report, spec)
    → 分析覆盖率报告，找出未被测试覆盖的功能场景，
      建议补充的测试用例

generate_uvm_like_sequence(protocol, transaction_types, randomization_weights)
    → 生成 cocotb 风格的类 UVM 激励序列类，
      支持协议感知的随机事务生成（AXI4/UART/SPI/I2C）

run_regression(test_dir, parallel_jobs, seed_list)
    → 并行运行回归测试套件，聚合多 seed 结果，
      输出通过率/失败 seed 列表/覆盖率汇总
```

#### 技术集成

- **cocotb 集成**: 通过 `Makefile` 驱动 cocotb 运行，或直接调用 `pytest --co` 发现并执行测试
- **覆盖率**: cocotb-coverage 库 + Verilator `--coverage` 标志
- **并行化**: `asyncio.gather` + 进程池，每个 seed 独立子进程
- **代码生成**: LLM（Claude）+ cocotb 专用提示模板库，覆盖主流协议的测试模式

#### 价值主张

> cocotb 已成为 FPGA 验证工程师的首选工具，尤其在需要复杂激励建模（如随机包生成、错误注入）的场景。本服务使 vibe4fpga 从「生成 SV 测试台」升级为「支持 Python 测试生态」，并通过覆盖率驱动的自动补全测试，实现真正的验证闭环。

---

### 9. `git-review-mcp` — HDL 代码审查与质量门禁服务

#### 背景痛点

`eda-bridge-mcp` 有 `run_lint` 工具，`code_review` Skill 有 LLM 驱动的设计评审，但两者都是针对文件级的全量分析。在 Git 工作流中，工程师需要的是「这次提交改了什么、这些改动是否引入了问题」的增量分析，以及可集成到 CI/CD 流水线的自动化质量门禁。

#### 工具列表

```
review_staged_changes(project_path, base_commit)
    → 分析当前工作区相对于基准提交的 HDL 变化，
      仅对变更行进行 Lint + LLM 审查，
      输出增量问题列表（不重复报告已有问题）

check_design_rules(rtl_files, rule_profile)
    → 执行可配置的设计规则检查：
      rule_profile: "conservative" | "aggressive" | custom JSON
      规则涵盖：CDC 风险、复位策略、FSM 编码、锁存器推断、
      时序路径风险、命名规范违反

generate_review_comment(file, line_start, line_end, issue_type)
    → 为具体代码位置生成人类可读的审查意见，
      包含问题说明、风险级别、修改建议和参考示例

create_ci_quality_gate(project_path, thresholds)
    → 生成 CI 质量门禁配置（GitHub Actions / GitLab CI YAML），
      thresholds: {max_errors: 0, max_warnings: 10, min_timing_margin_ns: 0.5}

calculate_quality_metrics(project_path)
    → 计算项目整体代码质量指标：
      {lint_score, cdc_risk_score, naming_consistency,
       comment_coverage, module_complexity_avg}

enforce_naming_conventions(project_path, convention_file)
    → 根据约定的命名规范配置文件，检查项目中所有 HDL 文件的命名违规，
      生成可自动修复的 rename 操作列表
```

#### 技术集成

- **增量分析**: 结合 `gitpython` 的 diff 功能和 `fpga-project-mcp` 的文件扫描，只对变更的模块运行分析
- **规则引擎**: 可扩展规则配置（YAML 格式），内置规则库覆盖 Xilinx/Intel 最佳实践
- **CI 集成**: 生成标准 GitHub Actions workflow YAML，可在 PR 上自动发表 review comment
- **质量指标**: 基于 `code_review` Skill 的评分体系 + 静态指标（圈复杂度近似值）

#### 价值主张

> 将 AI 代码审查融入 Git 工作流，每次 commit/PR 自动得到专业级 FPGA 设计审查意见。团队研发效率显著提升：初级工程师得到即时指导，资深工程师从重复审查工作中解放出来，质量门禁杜绝低质量代码进入主干。

---

### 10. `protocol-checker-mcp` — 片上协议合规性检查服务

#### 背景痛点

`waveform-mcp` 已有 AXI4 解码器，`instrument-mcp` 有差异分类，但对协议合规性的系统化、全面检查能力不足——例如 AXI4 的 200+ 条协议规则，UART/SPI/I2C 的时序参数合规，PCIe 的链路层行为验证。协议错误往往是 IP 集成最常见的失败原因，需要专门服务支撑。

#### 工具列表

```
check_axi4_compliance(waveform_file, axi_prefix, clock_name)
    → 全面检查 AXI4 协议合规性，覆盖 ARM AMBA 规范的关键规则：
      - VALID 不得等待 READY 才置位
      - 握手完成后信号值不得变化
      - ARLEN/AWLEN 与实际传输拍数匹配
      - WSTRB 与 WDATA 位宽对应
      - 5 通道独立性（AW/W/B/AR/R 无非法交织）
      返回: {violations: [{rule_id, description, timestamp_ns, evidence}]}

check_uart_timing(waveform_file, signal_name, baud_rate, tolerance_pct)
    → 验证 UART 信号时序合规：
      起始位宽度、数据位间隔、停止位长度，容差范围内标记为合规

check_spi_protocol(waveform_file, sclk, mosi, miso, cs, mode)
    → 检查 SPI 协议合规：
      mode 0/1/2/3 相位/极性符合性、CS 建立保持时间、
      MOSI/MISO 稳定区间

check_i2c_protocol(waveform_file, scl, sda, speed_mode)
    → 检查 I2C 协议合规：
      START/STOP 条件、ACK/NACK 正确性、时钟拉伸、
      总线仲裁行为（speed_mode: standard/fast/fast-plus）

detect_protocol_from_waveform(waveform_file, signals)
    → 自动识别一组信号所实现的协议类型，
      返回协议候选列表及置信度

generate_protocol_assertions(protocol, interface_prefix)
    → 自动生成指定协议的完整 SVA 断言文件，
      可直接用于仿真断言检查或形式验证
```

#### 技术集成

- **AXI4 检查**: 扩展 `waveform-mcp` 中已有的 AXI4 解码器，增加完整的 ARM AMBA 规则集（参考 IHI0022H）
- **串行协议分析**: 基于状态机的协议解码，与 `waveform-mcp` 的信号提取 API 配合
- **协议识别**: 基于信号名模式匹配 + 波形统计特征的协议分类器
- **SVA 生成**: 参考已发布的开源协议 SVA 库（AXI4-SVA, AMBA-SVIP）生成结构化断言

#### 价值主张

> 协议违规是系统集成最难追踪的问题类别，因为它们往往不在 RTL 仿真中显现，而在真实硬件压力测试下才暴露。本服务将协议合规检查从「依赖示波器和人眼判读」升级为「自动化的规则引擎 + AI 解释」，在仿真阶段即可发现协议违规，大幅降低板级集成风险。

---

## 服务优先级矩阵

基于「开发频率」、「现有痛点强度」、「实现难度」三个维度的综合评估：

| 服务名称 | 开发价值 | 实现复杂度 | 推荐优先级 |
|---|---|---|---|
| `constraint-mcp` | 极高 | 中 | P0 — 立即实施 |
| `formal-verify-mcp` | 高 | 中（基础设施已有） | P0 — 立即实施 |
| `board-bringup-mcp` | 极高 | 高 | P1 — 下一迭代 |
| `perf-profiling-mcp` | 高 | 中 | P1 — 下一迭代 |
| `ip-catalog-mcp` | 高 | 中 | P1 — 下一迭代 |
| `protocol-checker-mcp` | 高 | 中（AXI 已有基础） | P1 — 下一迭代 |
| `cocotb-mcp` | 中高 | 中 | P2 — 后续迭代 |
| `power-mcp` | 中高 | 中 | P2 — 后续迭代 |
| `version-control-mcp` | 中 | 低 | P2 — 后续迭代 |
| `git-review-mcp` | 中 | 低（依赖现有能力） | P2 — 后续迭代 |

---

## 服务间依赖关系

```
fpga-project-mcp ──┬──> constraint-mcp (模块信号用于时钟推断)
                   ├──> perf-profiling-mcp (关键路径 RTL 溯源)
                   ├──> git-review-mcp (增量分析基础)
                   └──> ip-catalog-mcp (接口兼容性对比)

waveform-mcp ──────┬──> formal-verify-mcp (反例 VCD 分析)
                   ├──> protocol-checker-mcp (波形信号提取)
                   └──> board-bringup-mcp (ILA 数据分析)

eda-bridge-mcp ────┬──> constraint-mcp (时序报告验证约束)
                   ├──> perf-profiling-mcp (综合/布局布线报告)
                   └──> power-mcp (功耗报告输入)

instrument-mcp ────> board-bringup-mcp (仿真-实测比对复用)

yosys-mcp ─────────> formal-verify-mcp (SMT2 准备)

datasheet-mcp ─────> ip-catalog-mcp (IP 文档 RAG 支撑)
```

---

## 扩展后的完整 MCP 架构图

```
VSCode Extension
       │  SSE / HTTP
       ▼
  LLM Router (:8765)
       │
  ┌────┴───────────────────────────────────────────────────────────┐
  │                        Skill Engine                            │
  │  Spec2RTL · CodeReview · WaveformDebug · TimingFix             │
  │  TestbenchGen · Verification · InstrumentAnalyze · AgentLoop   │
  └────┬───────────────────────────────────────────────────────────┘
       │  MCP Protocol
  ┌────┴──────────────────────────────────────────────────────────────┐
  │                        MCP Servers                                │
  │                                                                   │
  │  ── 现有服务 ──────────────────────────────────────────────────── │
  │  fpga-project · eda-bridge · waveform · instrument · datasheet   │
  │  quartus · yosys                                                  │
  │                                                                   │
  │  ── 新增服务 (本文分析) ────────────────────────────────────────── │
  │  constraint · power · version-control · ip-catalog               │
  │  formal-verify · board-bringup · perf-profiling                  │
  │  cocotb · git-review · protocol-checker                          │
  └────┬──────────────────────────────────────────────────────────────┘
       │
  EDA Tools / Hardware / VCS
  Vivado · Quartus · Yosys · nextpnr · SymbiYosys · cocotb
  Oscilloscope · Logic Analyzer · JTAG · ILA · SignalTap
  Git · GitHub Actions · OpenCores / FuseSoC
```

---

## 实施建议

### Phase 5 (近期): 约束与形式验证补全

1. 实现 `constraint-mcp`，重点先支持 XDC 解析和时钟约束自动生成
2. 将 `formal-verify-mcp` 从 `yosys-mcp` 中剥离为独立服务，实现完整的 SymbiYosys 流程
3. 在 `verification/pipeline.py` Layer 3 中对接真实的 `formal-verify-mcp`

### Phase 6 (中期): 性能与上板支持

1. 实现 `perf-profiling-mcp`，优先支持 Vivado 关键路径 RTL 溯源
2. 实现 `board-bringup-mcp`，优先支持 ILA 采集与 `waveform-mcp` 的联动
3. 实现 `ip-catalog-mcp`，优先支持 Vivado IP 目录解析和例化代码生成

### Phase 7 (远期): 流程自动化与质量体系

1. 实现 `cocotb-mcp`，支持 Python 测试生态
2. 实现 `protocol-checker-mcp`，扩展 AXI4 检查为完整规则集并新增串行协议
3. 实现 `power-mcp` 和 `version-control-mcp`
4. 实现 `git-review-mcp`，打通 CI/CD 集成

---

*本文档由 Claude Code 基于 vibe4fpga 仓库源码分析生成，2026-04-07*
