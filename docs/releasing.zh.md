# 发布

> 🌐 **中文** · [English](releasing.md)

全部 9 个 Python 包（3 个共享 + 7 个 MCP —— `mcp-testkit` 是 dev-only 不发布）外加那个 Rust MCP，从一个 tag 一起发出去。流程文件 [`.github/workflows/release.yml`](../.github/workflows/release.yml)。

v0.3.0 起原 Python `waveform-mcp` 被并进 Rust `waveform-mcp-rs` —— 别再把它放回 release matrix。

## PyPI 一次性准备

PyPI 的 trusted publishing 要求每个项目在 PyPI 上存在、且把这个仓库 + workflow 注册成 trusted publisher。每个包做一次：

1. 在 https://pypi.org 注册 / 登录
2. 建项目占位：
   * **Your projects → Publishing → Add a pending publisher**
   * 填：
     - PyPI Project Name：该包 `pyproject.toml` 里的 `name =`
       （例如 `vibe4fpga-llm-client`、`fpga-project-mcp` 等）
     - Owner：你的 GitHub 组织 / 用户（例如 `naspter`）
     - Repository name：`vibe4fpga`
     - Workflow name：`release.yml`
     - Environment name：`release`（要跟 publish job 里的 `environment: release` 行一致）
3. 给另外 8 个包重复一遍。

要注册的包名（精确字符串查每个 `pyproject.toml`）：

| 路径 | PyPI 名 |
| ---- | ------- |
| `packages/shared/llm-client` | `vibe4fpga-llm-client` |
| `packages/shared/platform` | `vibe4fpga-platform` |
| `packages/mcp-servers/fpga-project-mcp` | `fpga-project-mcp` |
| `packages/mcp-servers/eda-bridge-mcp` | `eda-bridge-mcp` |
| `packages/mcp-servers/instrument-mcp` | `instrument-mcp` |
| `packages/mcp-servers/datasheet-mcp` | `datasheet-mcp` |
| `packages/mcp-servers/quartus-mcp` | `quartus-mcp` |
| `packages/mcp-servers/yosys-mcp` | `yosys-mcp` |
| `packages/mcp-servers/verify-mcp` | `vibe4fpga-verify-mcp` |

## GitHub 环境

在仓库下创建一个 `release` 环境（Settings → Environments → New environment → `release`）。你可以：

* 在 publish job 跑起来之前要求 reviewer（首发或 trusted publishing 验证期推荐）
* 加 secret，如果 OIDC 之外还需要别的——本流程文件如其所是的话**不需要**任何 secret；`GITHUB_TOKEN` 由系统自动注入

## 切一次 release

```bash
# 1. 凡是自上次 tag 后改过的 pyproject.toml 都改 version。
#    共享库各自的节奏；MCP 之间锁步走 v0.3.0。

# 2. 本地状态自检：
make gen-skills-check    # 无漂移
make test                # tier 1+2 全绿

# 3. 提交剩余变更、推、打 tag：
git tag -a v0.3.0 -m "Release 0.3.0"
git push origin develop
git push origin v0.3.0
```

push tag 触发 `.github/workflows/release.yml`。Job 顺序：

```
preflight  ──┬─► python-build（9 个包并行）             ──► python-publish (OIDC)
             │
             └─► rust-build  （mac + win 并行）         ──┐
                                                          │
                                                          ▼
                                                   github-release
```

任意一个 `python-build` 矩阵单元挂掉只会 skip 对应的 `python-publish`，不影响另外 8 个。Rust job 独立。GitHub release 那个 job 在前两条腿都跑完之后才动。

## 干跑

`workflow_dispatch` 手动触发，把 `dry_run=true` 打开：

* 跑 `preflight` + `python-build` + `rust-build`
* **跳过** `python-publish` 和 `github-release`

在 release candidate 分支上冒烟测试 wheel 构建，但不发布。

## 包名不一致说明

7 个起源期的 Python MCP 用裸名发布（`fpga-project-mcp`、`eda-bridge-mcp`、…）；pivot 时加入的 `verify-mcp` 用 `vibe4fpga-verify-mcp`。把 7 个旧的改名去对齐会把已经 `uv tool install` 的本地用户搞坏；我们保留原样。`configs/*` 里宿主片段引用的是 *entry point* 名（一律 `<mcp>-mcp`），不是 PyPI 名，所以用户不会感知这个分裂。

## 发布之后

* 在干净机器上验证安装：
  ```bash
  uv tool install fpga-project-mcp
  fpga-project-mcp --help
  ```
* 如果维护 `CHANGELOG.md`，把这个 tag 的差更新进去
* 在团队习惯的渠道里公告
