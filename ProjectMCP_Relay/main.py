import asyncio
import json
import logging
import os
import uuid
from datetime import datetime
from typing import Optional, Dict

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import Response
from starlette.websockets import WebSocket, WebSocketDisconnect
from contextlib import asynccontextmanager


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger("MCPRelayServer")

SERVER_NAME = "mcp-relay-server"
SERVER_VERSION = "1.0.0"
PROTOCOL_VERSION = "2025-03-26"
MCP_SESSION_ID_HEADER = "mcp-session-id"


# ======================================================
# GLOBAL STORAGE
# ======================================================
local_agent_ws: Optional[WebSocket] = None
ws_lock = asyncio.Lock()
active_sessions: Dict[str, dict] = {}
pending_requests: Dict[str, asyncio.Future] = {}

# 🔥 Local Agent에서 전달받은 실제 Tool 목록 저장
tools_cache: Dict[str, dict] = {}


# ======================================================
# LIFESPAN
# ======================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("===== MCP Relay Server Started =====")
    yield
    logger.info("===== MCP Relay Server Shutdown =====")


app = FastAPI(lifespan=lifespan)


# ======================================================
# CORS
# ======================================================
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def add_cors(resp: Response):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Accept, Mcp-Session-Id"
    resp.headers["Access-Control-Expose-Headers"] = "Mcp-Session-Id, Content-Type"
    return resp


# ======================================================
# HEALTH CHECK
# ======================================================
@app.get("/")
def health():
    return add_cors(JSONResponse({
        "status": "Running",
        "server": SERVER_NAME,
        "version": SERVER_VERSION,
        "protocol": PROTOCOL_VERSION,
        "agent_connected": local_agent_ws is not None,
        "tool_count": len(tools_cache),
        "time": datetime.utcnow().isoformat()
    }))


# ======================================================
# WELL-KNOWN MCP METADATA
# ======================================================
@app.get("/.well-known/mcp.json")
def mcp_metadata():
    return add_cors(JSONResponse({
        "protocolVersion": PROTOCOL_VERSION,
        "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        "capabilities": {"tools": tools_cache},
        "transport": "streamableHttp",
        "streamableHttp": {"url": "/mcp"}
    }))


# ======================================================
# RPC HANDLER
# ======================================================
async def handle_rpc(body: dict, session_id: str):
    global tools_cache

    rpc_id = body.get("id")
    method = body.get("method")
    params = body.get("params", {})

    logger.info(f"[RPC] {method} (id={rpc_id})")

    # -------- initialize --------
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                "capabilities": {"tools": tools_cache}
            }
        }

    # -------- tools/list --------
    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "result": {"tools": [
                {"name": tn, **meta} for tn, meta in tools_cache.items()
            ]}
        }

    # -------- tools/call --------
    if method == "tools/call":
        tool_name = params.get("name")
        arguments = params.get("arguments", {})

        if tool_name not in tools_cache:
            return {
                "jsonrpc": "2.0",
                "id": rpc_id,
                "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"}
            }

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
            result = await asyncio.wait_for(future, timeout=45)
            return result
        except asyncio.TimeoutError:
            return {
                "jsonrpc": "2.0",
                "id": rpc_id,
                "error": {"code": -32000, "message": "Local Agent timeout"}
            }


# ======================================================
# MCP HTTP ENDPOINT
# ======================================================
@app.post("/mcp")
async def post_mcp(request: Request):
    body = await request.json()
    session_id = request.headers.get(MCP_SESSION_ID_HEADER) or str(uuid.uuid4())
    active_sessions[session_id] = {}

    result = await handle_rpc(body, session_id)

    resp = JSONResponse(result)
    resp.headers[MCP_SESSION_ID_HEADER] = session_id
    return add_cors(resp)


@app.options("/mcp")
async def options_mcp():
    return add_cors(Response())


# ======================================================
# LOCAL AGENT WebSocket
# ======================================================
@app.websocket("/ws")
async def websocket_bridge(ws: WebSocket):
    global local_agent_ws, tools_cache

    await ws.accept()
    async with ws_lock:
        if local_agent_ws:
            await local_agent_ws.close()
        local_agent_ws = ws

    logger.info("Local Agent Connected")

    # 🔥 Local Agent에게 툴 목록 요청
    await ws.send_text(json.dumps({
        "id": "__sync_tools__",
        "type": "sync_request"
    }))

    try:
        while True:
            msg = json.loads(await ws.receive_text())
            msg_id = msg.get("id")

            # 🔥 툴 목록 동기화 응답 처리
            if msg_id == "__sync_tools__":
                tools_cache = msg.get("tools", {})
                logger.info(f"[SYNC] Tools Updated: {len(tools_cache)} Tools Loaded")
                continue

            # 일반 툴 응답 처리
            if msg_id in pending_requests:
                pending_requests[msg_id].set_result(msg)
                del pending_requests[msg_id]

    except WebSocketDisconnect:
        logger.warning("Local Agent Disconnected")
        local_agent_ws = None
