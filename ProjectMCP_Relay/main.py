import asyncio
import json
import logging
import uuid
from datetime import datetime
from typing import Optional, Dict

from fastapi import FastAPI, Request, Response, HTTPException, WebSocket
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.websockets import WebSocketDisconnect


# ===========================================================
# CONFIG
# ===========================================================

SERVER_NAME = "mcp-relay-server"
SERVER_VERSION = "1.0.0"
PROTOCOL_VERSION = "2025-03-26"
SESSION_HEADER = "mcp-session-id"


# ===========================================================
# LOGGING
# ===========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger(SERVER_NAME)


# ===========================================================
# APP INIT
# ===========================================================

app = FastAPI(title=SERVER_NAME, version=SERVER_VERSION)

# CORS: 강력 모드 (Cloud / Render에서도 절대 안 깨짐)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)


# ===========================================================
# INTERNAL STORAGE
# ===========================================================

active_sessions: Dict[str, dict] = {}
local_agent_ws: Optional[WebSocket] = None
ws_lock = asyncio.Lock()
pending_requests: Dict[str, asyncio.Future] = {}


# ===========================================================
# CORS HEADERS (필수)
# ===========================================================

def add_cors(res: Response):
    res.headers["Access-Control-Allow-Origin"] = "*"
    res.headers["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS"
    res.headers["Access-Control-Allow-Headers"] = "Content-Type, Accept, mcp-session-id"
    res.headers["Access-Control-Expose-Headers"] = "mcp-session-id"
    return res


# ===========================================================
# HEALTH CHECK
# ===========================================================

@app.get("/")
async def root():
    return add_cors(JSONResponse({
        "status": "Running",
        "server": SERVER_NAME,
        "version": SERVER_VERSION,
        "protocol": PROTOCOL_VERSION,
        "agent_connected": local_agent_ws is not None,
        "session_count": len(active_sessions)
    }))


# ===========================================================
# REQUIRED MCP METADATA ENDPOINT
# ===========================================================

@app.get("/.well-known/mcp.json")
async def metadata():
    """
    ChatGPT가 최초에 읽는 Manifest (가장 중요)
    """
    manifest = {
        "protocolVersion": PROTOCOL_VERSION,
        "serverInfo": {
            "name": SERVER_NAME,
            "version": SERVER_VERSION
        },
        "capabilities": {
            # 필요하면 이곳에 툴만 추가하면 됨 (실제 실행은 로컬)
            "tools": {
                "unity_command": {
                    "name": "unity_command",
                    "description": "Send commands to Unity (executed on Local PC)"
                },
                "python_exec": {
                    "name": "python_exec",
                    "description": "Execute python script on Local PC"
                },
                "local_ai": {
                    "name": "local_ai",
                    "description": "Run local AI inference"
                }
            }
        },
        "transport": "streamableHttp",
        "streamableHttp": {
            "url": "/mcp"
        }
    }

    return add_cors(JSONResponse(manifest))


# ===========================================================
# MCP ENDPOINT (JSON-RPC 2.0)
# ===========================================================

@app.options("/mcp")
async def options_mcp():
    return add_cors(Response())


@app.post("/mcp")
async def mcp_post(request: Request):
    global local_agent_ws

    raw = await request.body()
    body = json.loads(raw.decode("utf-8"))
    method = body.get("method")
    rpc_id = body.get("id")

    # 세션 처리
    session_id = request.headers.get(SESSION_HEADER)
    if not session_id:
        session_id = str(uuid.uuid4())
        active_sessions[session_id] = {
            "created_at": datetime.utcnow().isoformat()
        }

    # INITIALIZE 는 서버가 직접 처리
    if method == "initialize":
        resp = {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "serverInfo": {
                    "name": SERVER_NAME,
                    "version": SERVER_VERSION
                },
                "capabilities": {
                    "tools": {}
                }
            }
        }

        res = JSONResponse(resp)
        res.headers[SESSION_HEADER] = session_id
        return add_cors(res)

    # 로컬 에이전트 미연결
    if local_agent_ws is None:
        err = {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "error": {"code": -32000, "message": "Local Agent not connected"}
        }
        res = JSONResponse(err)
        res.headers[SESSION_HEADER] = session_id
        return add_cors(res)

    # WebSocket으로 로컬 서버에 전달
    loop = asyncio.get_running_loop()
    fut = loop.create_future()
    pending_requests[rpc_id] = fut

    await local_agent_ws.send_text(json.dumps(body))

    try:
        result = await asyncio.wait_for(fut, timeout=60.0)
    except asyncio.TimeoutError:
        result = {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "error": {"code": -32000, "message": "Timeout waiting for Local Agent"}
        }

    res = JSONResponse(result)
    res.headers[SESSION_HEADER] = session_id
    return add_cors(res)


# ===========================================================
# DELETE SESSION
# ===========================================================

@app.delete("/mcp")
async def delete_mcp(request: Request):
    session_id = request.headers.get(SESSION_HEADER)
    if session_id in active_sessions:
        del active_sessions[session_id]
    return add_cors(Response(status_code=200))


# ===========================================================
# LOCAL PC AGENT WEBSOCKET
# ===========================================================

@app.websocket("/ws")
async def ws_local_agent(websocket: WebSocket):
    global local_agent_ws

    await websocket.accept()

    async with ws_lock:
        if local_agent_ws:
            await local_agent_ws.close()
        local_agent_ws = websocket

    logger.info("Local Agent Connected.")

    try:
        while True:
            msg = await websocket.receive_text()
            data = json.loads(msg)
            resp_id = data.get("id")

            if resp_id in pending_requests:
                pending_requests[resp_id].set_result(data)
                del pending_requests[resp_id]
    except WebSocketDisconnect:
        logger.warning("Local Agent Disconnected.")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        async with ws_lock:
            if local_agent_ws == websocket:
                local_agent_ws = None


# ===========================================================
# RUN SERVER
# ===========================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(__import__("os").getenv("PORT", 10000)),
        log_level="info"
    )
