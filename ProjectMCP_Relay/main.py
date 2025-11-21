import asyncio
import json
import logging
import os
import uuid
from datetime import datetime
from typing import Optional, Dict

from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.websockets import WebSocket, WebSocketDisconnect
from contextlib import asynccontextmanager

# ===========================================================
# LOGGING
# ===========================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger("MCPRelayServer")

# ===========================================================
# CONFIG
# ===========================================================
SERVER_NAME = "mcp-relay-server"
SERVER_VERSION = "1.0.0"
PROTOCOL_VERSION = "2025-03-26"
MCP_SESSION_ID_HEADER = "mcp-session-id"

# ===========================================================
# STORAGE
# ===========================================================
active_sessions: Dict[str, dict] = {}
pending_requests: Dict[str, asyncio.Future] = {}
local_agent_ws: Optional[WebSocket] = None
ws_lock = asyncio.Lock()

# ===========================================================
# LIFESPAN
# ===========================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("===== MCP Relay Server Started =====")
    logger.info(f"Protocol: {PROTOCOL_VERSION}")
    logger.info("Endpoint: /mcp")
    logger.info("WebSocket: /ws")
    yield
    logger.info("===== MCP Relay Server Shutdown =====")

# ===========================================================
# FASTAPI APP
# ===========================================================
app = FastAPI(
    title=SERVER_NAME,
    version=SERVER_VERSION,
    lifespan=lifespan
)

# ===========================================================
# CORS
# ===========================================================
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def add_cors(response: Response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = (
        "Content-Type, Accept, Mcp-Session-Id"
    )
    response.headers["Access-Control-Expose-Headers"] = (
        "Mcp-Session-Id, Content-Type"
    )
    return response

# ===========================================================
# HEALTH CHECK
# ===========================================================
@app.get("/")
def health():
    return add_cors(JSONResponse({
        "status": "Running",
        "server": SERVER_NAME,
        "version": SERVER_VERSION,
        "protocol": PROTOCOL_VERSION,
        "agent_connected": local_agent_ws is not None,
        "active_sessions": len(active_sessions),
        "mcp_endpoint": "/mcp",
        "time": datetime.utcnow().isoformat()
    }))

# ===========================================================
# MCP METADATA (.well-known)
# ===========================================================
@app.get("/.well-known/mcp.json")
def mcp_metadata():
    metadata = {
        "protocolVersion": PROTOCOL_VERSION,
        "serverInfo": {
            "name": SERVER_NAME,
            "version": SERVER_VERSION
        },
        "capabilities": {
            "tools": {
                "unity_command": {
                    "name": "unity_command",
                    "description": "Send command to Unity via local agent",
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
                    "description": "Execute Python script on local machine",
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
                    "description": "Query local LLM",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "prompt": {"type": "string"}
                        },
                        "required": ["prompt"]
                    }
                }
            }
        },
        "transport": "streamableHttp",
        "streamableHttp": {
            "url": "/mcp"
        }
    }
    return add_cors(JSONResponse(metadata))

# ===========================================================
# JSON-RPC HANDLER
# ===========================================================
async def handle_rpc(body: dict, session_id: str):
    rpc_id = body.get("id")
    method = body.get("method")
    params = body.get("params", {})

    logger.info(f"[RPC] {method} (id {rpc_id})")

    # ---- initialize -------------------------------------------------------
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                "capabilities": {"tools": {}}
            }
        }

    # ---- tools/list -------------------------------------------------------
    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "result": {
                "tools": [
                    {
                        "name": "unity_command",
                        "description": "Send command to Unity",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "action": {"type": "string"},
                                "parameters": {"type": "object"}
                            },
                            "required": ["action"]
                        }
                    },
                    {
                        "name": "python_exec",
                        "description": "Execute Python code",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "code": {"type": "string"}
                            },
                            "required": ["code"]
                        }
                    },
                    {
                        "name": "ask_local_ai",
                        "description": "Local LLM Query",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "prompt": {"type": "string"}
                            },
                            "required": ["prompt"]
                        }
                    }
                ]
            }
        }

    # ---- tools/call → 로컬 PC로 전달 ---------------------------------------
    if method == "tools/call":
        tool_name = params.get("name")
        arguments = params.get("arguments", {})

        async with ws_lock:
            if local_agent_ws is None:
                return {
                    "jsonrpc": "2.0",
                    "id": rpc_id,
                    "error": {"code": -32000, "message": "Local Agent not connected"}
                }

            loop = asyncio.get_running_loop()
            future = loop.create_future()
            pending_requests[rpc_id] = future

            await local_agent_ws.send_text(json.dumps({
                "id": rpc_id,
                "tool": tool_name,
                "args": arguments
            }))

        try:
            result = await asyncio.wait_for(future, timeout=30.0)
            return result
        except asyncio.TimeoutError:
            del pending_requests[rpc_id]
            return {
                "jsonrpc": "2.0",
                "id": rpc_id,
                "error": {"code": -32000, "message": "Local Agent timeout"}
            }

    return {
        "jsonrpc": "2.0",
        "id": rpc_id,
        "error": {"code": -32601, "message": f"Unknown method: {method}"}
    }

# ===========================================================
# MCP ENDPOINT (StreamableHTTP)
# ===========================================================
@app.options("/mcp")
async def options_mcp():
    return add_cors(Response())

@app.post("/mcp")
async def post_mcp(request: Request):
    try:
        body = await request.json()
        rpc_id = body.get("id")

        session_id = request.headers.get(MCP_SESSION_ID_HEADER)
        if not session_id:
            session_id = str(uuid.uuid4())
            active_sessions[session_id] = {}

        result = await handle_rpc(body, session_id)

        response = JSONResponse(result)
        response.headers[MCP_SESSION_ID_HEADER] = session_id
        return add_cors(response)

    except Exception as e:
        logger.error(f"Error: {e}")
        return add_cors(JSONResponse(
            {"error": str(e)}, status_code=500
        ))

@app.delete("/mcp")
async def delete_mcp(request: Request):
    session_id = request.headers.get(MCP_SESSION_ID_HEADER)
    if session_id and session_id in active_sessions:
        del active_sessions[session_id]
    return add_cors(Response(status_code=200))

# ===========================================================
# LOCAL AGENT (WebSocket)
# ===========================================================
@app.websocket("/ws")
async def ws_local(websocket: WebSocket):
    global local_agent_ws

    await websocket.accept()

    async with ws_lock:
        if local_agent_ws:
            await local_agent_ws.close()
        local_agent_ws = websocket

    logger.info("Local Agent Connected")

    try:
        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)
            rpc_id = msg.get("id")

            if rpc_id in pending_requests:
                pending_requests[rpc_id].set_result(msg)
                del pending_requests[rpc_id]
    except WebSocketDisconnect:
        logger.warning("Local Agent Disconnected")
    except Exception as e:
        logger.error(f"WebSocket Error: {e}")
    finally:
        local_agent_ws = None

# ===========================================================
# RUN
# ===========================================================
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 10000))
    uvicorn.run(app, host="0.0.0.0", port=port)
