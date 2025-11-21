# tool_manager.py
from tools_registry import TOOLS


def list_tools():
    """Return list format of tools for tools/list RPC."""
    return [
        {
            "name": name,
            "description": data.get("description", ""),
            "inputSchema": data.get("inputSchema", {})
        }
        for name, data in TOOLS.items()
    ]


def get_tool_metadata_dict():
    """Return dict format of tools for MCP metadata."""
    return TOOLS


def tool_exists(name: str) -> bool:
    return name in TOOLS
