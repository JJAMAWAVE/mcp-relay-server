import asyncio
import json
import logging
import uuid
from datetime import datetime
from typing import Optional, Dict

from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.websockets import WebSocket, WebSocketDisconnect

from tools_registry import TOOL_REGISTRY

# ============== LOGGING ==============
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger("RelayMCP")

# ============== CONFIG ==============
SERVER_NAME = "mcp-relay-server"
SERVER_VERSION = "1.0.0"
PROTOCOL_VERSION = "2025-03-26"
MCP_SESSION_ID_HEADER = "mcp-session-id"

# ============== STATE ==============
active_sessions: Dict[str, dict] = {}
pending_requests = {}  # HTTP RPC 요청 대기
local_agent_ws: Optional[WebSocket] = None
ws_lock = asyncio.Lock()

# ============== FASTAPI ==============
app = FastAPI(title=SERVER_NAME, version=SERVER_VERSION)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Add CORS helper
def add_cors(response: Response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "*"
    response.headers["Access-Control-Expose-Headers"] = MCP_SESSION_ID_HEADER
    return response

# ============== Metadata (.well-known) ==============
@app.get("/.well-known/mcp.json")
async def mcp_metadata():
    logger.info("📥 Metadata requested")
    metadata = {
        "protocolVersion": PROTOCOL_VERSION,
        "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        "capabilities": {
            "tools": TOOL_REGISTRY     # ← 툴 목록 동적 로드!
        },
        "transport": "streamableHttp",
        "streamableHttp": {"url": "/mcp"}
    }
    return add_cors(JSONResponse(metadata))

# ============== Health Check ==============
@app.get("/")
async def root():
    return add_cors(JSONResponse({
        "status": "running",
        "active_sessions": len(active_sessions),
        "agent_connected": local_agent_ws is not None,
        "timestamp": datetime.utcnow().isoformat()
    }))

@app.options("/mcp")
async def mcp_options():
    return add_cors(Response())

# ============== MCP POST ==============
@app.post("/mcp")
async def mcp_post(request: Request):
    global local_agent_ws

    body_bytes = await request.body()
    body_text = body_bytes.decode(errors="ignore")
    body = json.loads(body_text)

    rpc_id = body.get("id")
    method = body.get("method")

    session_id = request.headers.get(MCP_SESSION_ID_HEADER)
    if not session_id:
        session_id = str(uuid.uuid4())
        active_sessions[session_id] = {"created_at": datetime.utcnow().isoformat()}
        logger.info(f"✨ New session created: {session_id[:8]}...")

    # Initialize directly handled
    if method == "initialize":
        return add_cors(JSONResponse({
            "jsonrpc": "2.0",
            "id": rpc_id,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                "capabilities": {"tools": TOOL_REGISTRY}
            }
        }))

    # List tools
    if method == "tools/list":
        return add_cors(JSONResponse({
            "jsonrpc": "2.0",
            "id": rpc_id,
            "result": {"tools": list(TOOL_REGISTRY.values())}
        }))

    # 로컬 PC 미연결이면 오류
    if local_agent_ws is None:
        return add_cors(JSONResponse({
            "jsonrpc": "2.0",
            "id": rpc_id,
            "error": {"code": -32000, "message": "Local Agent not connected"}
        }))

    # 로컬로 중계
    logger.info(f"📤 Forward → Local Agent: {method}")

    loop = asyncio.get_running_loop()
    future = loop.create_future()
    pending_requests[rpc_id] = future

    await local_agent_ws.send_text(body_text)

    try:
        result = await asyncio.wait_for(future, timeout=30.0)
        response = JSONResponse(result)
        response.headers[MCP_SESSION_ID_HEADER] = session_id
        return add_cors(response)

    except asyncio.TimeoutError:
        del pending_requests[rpc_id]
        return add_cors(JSONResponse({
            "jsonrpc": "2.0",
            "id": rpc_id,
            "error": {"code": -32000, "message": "Local Agent timeout"}
        }))

# ============== WebSocket: Local Agent ==============
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    global local_agent_ws
    await websocket.accept()

    async with ws_lock:
        local_agent_ws = websocket

    logger.info("🔌 Local Agent connected")

    try:
        while True:
            message = await websocket.receive_text()
            response = json.loads(message)
            rpc_id = response.get("id")

            if rpc_id in pending_requests:
                pending_requests[rpc_id].set_result(response)
                del pending_requests[rpc_id]
    except WebSocketDisconnect:
        logger.warning("⚠ Local Agent disconnected")
        local_agent_ws = None
