# tools_registry.py

TOOL_REGISTRY = {
    "unity_command": {
        "name": "unity_command",
        "description": "Send command to Unity Editor via Local Agent",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string"},
                "parameters": {"type": "object"}
            },
            "required": ["action"]
        }
    },

    "python_run": {
        "name": "python_run",
        "description": "Execute Python code on local machine",
        "inputSchema": {
            "type": "object",
            "properties": {
                "code": {"type": "string"}
            },
            "required": ["code"]
        }
    },

    "local_ai_query": {
        "name": "local_ai_query",
        "description": "Ask question to local LLM",
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string"}
            },
            "required": ["prompt"]
        }
    }
}
