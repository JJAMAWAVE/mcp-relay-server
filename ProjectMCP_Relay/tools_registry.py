# tools_registry.py

TOOL_REGISTRY = {
    "unity_command": {
        "name": "unity_command",
        "description": "Send command to Unity Editor via Local Agent",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "e.g., create_script, fix_script"},
                "parameters": {"type": "object", "description": "Parameters for the action"}
            },
            "required": ["action"]
        }
    },

    "python_exec": {
        "name": "python_exec",
        "description": "Execute Python code on local machine",
        "inputSchema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Python code to execute"}
            },
            "required": ["code"]
        }
    },

    "ask_local_ai": {
        "name": "ask_local_ai",
        "description": "Query local LLM (Ollama)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "Prompt for the AI"}
            },
            "required": ["prompt"]
        }
    }
}

# ★★★ 이 부분이 없어서 에러가 난 겁니다. 꼭 포함되어야 합니다! ★★★
def get_tools_list():
    """MCP 프로토콜 포맷(리스트)으로 변환하여 반환"""
    return list(TOOL_REGISTRY.values())
