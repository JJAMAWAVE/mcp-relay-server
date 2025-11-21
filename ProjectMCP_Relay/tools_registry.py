# tools_registry.py

TOOLS = {
    "unity_command": {
        "name": "unity_command",
        "description": "Send command to Unity Editor",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string"},
                "parameters": {"type": "object"}
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
                "code": {"type": "string"}
            },
            "required": ["code"]
        }
    },

    "ask_local_ai": {
        "name": "ask_local_ai",
        "description": "Ask question to Local LLM",
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string"}
            },
            "required": ["prompt"]
        }
    },

    # --------------------------------------------------------
    # NEW: Google Drive Tools for conversation log saving
    # --------------------------------------------------------

    "google_drive_save_text": {
        "name": "google_drive_save_text",
        "description": "Save a text file to Google Drive",
        "inputSchema": {
            "type": "object",
            "properties": {
                "filename": {"type": "string"},
                "content": {"type": "string"},
                "folder_id": {"type": "string"}
            },
            "required": ["filename", "content"]
        }
    },

    "google_drive_append_log": {
        "name": "google_drive_append_log",
        "description": "Append text content to an existing Google Drive file",
        "inputSchema": {
            "type": "object",
            "properties": {
                "file_id": {"type": "string"},
                "append_text": {"type": "string"}
            },
            "required": ["file_id", "append_text"]
        }
    }
}
