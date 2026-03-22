.PHONY: install build watch lint clean \
        dev-router dev-fpga-project-mcp dev-eda-bridge-mcp \
        dev-waveform-mcp dev-instrument-mcp dev-datasheet-mcp \
        dev-quartus-mcp dev-yosys-mcp dev-collab-server

# ── Installation ──────────────────────────────────────────────────────────────
install: install-node install-python

install-node:
	npm install

install-python:
	cd packages/llm-router && uv sync
	cd packages/mcp-servers/fpga-project-mcp && uv sync
	cd packages/mcp-servers/eda-bridge-mcp && uv sync
	cd packages/mcp-servers/waveform-mcp && uv sync
	cd packages/mcp-servers/instrument-mcp && uv sync
	cd packages/mcp-servers/datasheet-mcp && uv sync
	cd packages/mcp-servers/quartus-mcp && uv sync
	cd packages/mcp-servers/yosys-mcp && uv sync
	cd packages/collab-server && uv sync
	cd packages/skills && uv sync

# ── Build ─────────────────────────────────────────────────────────────────────
build:
	npm run build

watch:
	npm run watch

lint:
	npm run lint

# ── Dev servers ───────────────────────────────────────────────────────────────
dev-router:
	cd packages/llm-router && uv run uvicorn llm_router.main:app --reload --port 8765

dev-fpga-project-mcp:
	cd packages/mcp-servers/fpga-project-mcp && uv run fpga-project-mcp

dev-eda-bridge-mcp:
	cd packages/mcp-servers/eda-bridge-mcp && uv run eda-bridge-mcp

dev-waveform-mcp:
	cd packages/mcp-servers/waveform-mcp && uv run waveform-mcp

dev-instrument-mcp:
	cd packages/mcp-servers/instrument-mcp && uv run instrument-mcp

dev-datasheet-mcp:
	cd packages/mcp-servers/datasheet-mcp && uv run datasheet-mcp

dev-quartus-mcp:
	cd packages/mcp-servers/quartus-mcp && uv run quartus-mcp

dev-yosys-mcp:
	cd packages/mcp-servers/yosys-mcp && uv run yosys-mcp

dev-collab-server:
	cd packages/collab-server && uv run uvicorn collab_server.main:app --reload --port 8766

# ── Stack shortcuts ────────────────────────────────────────────────────────────
# Start all services for a full local dev stack (requires tmux or parallel shell)
dev-all:
	$(MAKE) dev-router & \
	$(MAKE) dev-fpga-project-mcp & \
	$(MAKE) dev-eda-bridge-mcp & \
	$(MAKE) dev-waveform-mcp & \
	$(MAKE) dev-instrument-mcp & \
	$(MAKE) dev-datasheet-mcp & \
	$(MAKE) dev-quartus-mcp & \
	$(MAKE) dev-yosys-mcp & \
	$(MAKE) dev-collab-server & \
	wait

# ── Clean ─────────────────────────────────────────────────────────────────────
clean:
	rm -rf packages/vscode-extension/out
	find . -name "__pycache__" -type d -not -path "*/.git/*" -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true
