.PHONY: install install-uv sync gen-skills gen-skills-check test clean \
        dev-fpga-project-mcp dev-eda-bridge-mcp dev-waveform-mcp \
        dev-instrument-mcp dev-datasheet-mcp dev-quartus-mcp \
        dev-yosys-mcp dev-verify-mcp dev-waveform-mcp-rs

# Pick up uv / cargo / brew / homebrew-prefix on macOS regardless of shell init.
export PATH := $(HOME)/.local/bin:$(HOME)/.cargo/bin:/opt/homebrew/bin:/usr/local/bin:$(PATH)

PY_PACKAGES := \
    packages/shared/llm-client \
    packages/shared/platform \
    packages/shared/mcp-testkit \
    packages/mcp-servers/fpga-project-mcp \
    packages/mcp-servers/eda-bridge-mcp \
    packages/mcp-servers/waveform-mcp \
    packages/mcp-servers/instrument-mcp \
    packages/mcp-servers/datasheet-mcp \
    packages/mcp-servers/quartus-mcp \
    packages/mcp-servers/yosys-mcp \
    packages/mcp-servers/verify-mcp

# ── Bootstrap ────────────────────────────────────────────────────────────────
install: install-uv sync

install-uv:
	@if command -v uv >/dev/null 2>&1; then \
	  echo "uv $$(uv --version) already installed"; \
	elif command -v brew >/dev/null 2>&1; then \
	  echo "Installing uv via Homebrew..."; brew install uv; \
	elif command -v pip3 >/dev/null 2>&1; then \
	  echo "Installing uv via pip3..."; pip3 install uv --break-system-packages; \
	else \
	  echo "Installing uv via official installer..."; \
	  curl -LsSf https://astral.sh/uv/install.sh | sh; \
	fi

# uv sync each Python package against its editable path-deps.
sync:
	@for pkg in $(PY_PACKAGES); do \
	  echo "  → uv sync $$pkg"; \
	  (cd $$pkg && uv sync --all-extras) || exit 1; \
	done

# ── Host-adapter generator (Claude Code / Codex / OpenCode) ──────────────────
gen-skills:
	uv run --script tools/gen-skills/generate.py

gen-skills-check:
	uv run --script tools/gen-skills/generate.py --check

# ── Tests ────────────────────────────────────────────────────────────────────
test:
	@for pkg in $(PY_PACKAGES); do \
	  echo "  → pytest $$pkg"; \
	  (cd $$pkg && uv run pytest --tb=short -q) || exit 1; \
	done
	@echo "  → cargo test waveform-mcp-rs"
	@cd packages/mcp-servers/waveform-mcp-rs && cargo test --locked

# ── Dev servers ──────────────────────────────────────────────────────────────
# Spawn a single MCP over stdio; intended for ad-hoc debugging. Normal usage
# is via Claude Code / Codex / OpenCode host configs in configs/.
dev-fpga-project-mcp:
	cd packages/mcp-servers/fpga-project-mcp && uv run fpga-project-mcp

dev-eda-bridge-mcp:
	cd packages/mcp-servers/eda-bridge-mcp && uv run eda-bridge-mcp

dev-waveform-mcp:
	cd packages/mcp-servers/waveform-mcp && uv run waveform-mcp

dev-waveform-mcp-rs:
	cd packages/mcp-servers/waveform-mcp-rs && cargo run --release

dev-instrument-mcp:
	cd packages/mcp-servers/instrument-mcp && uv run instrument-mcp

dev-datasheet-mcp:
	cd packages/mcp-servers/datasheet-mcp && uv run datasheet-mcp

dev-quartus-mcp:
	cd packages/mcp-servers/quartus-mcp && uv run quartus-mcp

dev-yosys-mcp:
	cd packages/mcp-servers/yosys-mcp && uv run yosys-mcp

dev-verify-mcp:
	cd packages/mcp-servers/verify-mcp && uv run verify-mcp

# ── Clean ────────────────────────────────────────────────────────────────────
clean:
	find . -name "__pycache__" -type d -not -path "*/.git/*" -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true
	find . -name ".DS_Store" -delete 2>/dev/null || true
	rm -rf packages/mcp-servers/waveform-mcp-rs/target
