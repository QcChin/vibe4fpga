"""datasheet-mcp — RAG Knowledge Base MCP Server."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from .indexer import index_document, search

mcp = FastMCP("datasheet-mcp")


@mcp.tool()
async def search_datasheet(query: str, top_k: int = 5) -> list[dict]:
    """Semantic search across indexed device datasheets.

    Returns:
        [{source, page, score, text, doc_type}]
    """
    return search(query, doc_type="datasheet", top_k=top_k)


@mcp.tool()
async def search_protocol(
    query: str,
    protocol: str | None = None,
    top_k: int = 5,
) -> list[dict]:
    """Search protocol specifications (AXI4, PCIe, DDR4, Ethernet, etc.)

    Args:
        protocol: Optional protocol filter in the query (appended to query string).
    """
    full_query = f"{protocol} {query}" if protocol else query
    return search(full_query, doc_type="protocol", top_k=top_k)


@mcp.tool()
async def search_design_pattern(query: str, top_k: int = 3) -> list[dict]:
    """Search HDL design patterns (CDC synchronizer, handshake, gray code, FIFO, etc.)"""
    return search(query, doc_type="design_pattern", top_k=top_k)


@mcp.tool()
async def get_ip_interface(ip_name: str, version: str | None = None) -> list[dict]:
    """Retrieve IP core port definitions and parameter list.

    Args:
        ip_name:  IP core name, e.g. "axi_fifo", "clk_wiz", "mig_7series".
        version:  Optional version string (appended to query).
    """
    query = f"{ip_name} {version or ''} interface ports parameters".strip()
    return search(query, doc_type="ip_interface", top_k=5)


@mcp.tool()
async def index_document_tool(file_path: str, doc_type: str = "datasheet") -> dict:
    """Index or re-index a document. Only rebuilds vectors for changed files.

    Args:
        file_path: Absolute path to PDF or text file.
        doc_type:  "datasheet" | "protocol" | "design_pattern" | "ip_interface"
    """
    return index_document(file_path, doc_type=doc_type)


@mcp.tool()
async def search_all(query: str, top_k: int = 8) -> list[dict]:
    """Search across all indexed documents regardless of type."""
    return search(query, doc_type=None, top_k=top_k)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
