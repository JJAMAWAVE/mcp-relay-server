import asyncio
import json
import logging
import os
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, WebSocket, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette.sse import EventSourceResponse
from starlette.websockets import WebSocketDisconnect

# ------------------------------------
# Logging
# ------------------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("RelayServer")

# ------------------------------------
# FastAPI App
# ------------------------------------
app = FastAPI()

# ------------------------------------
# CORS
# ------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ------------------------------------
# 상태 관리
# ------------------------------------
local_agent_ws: Optional[WebSocket] = None
ws_lock = asyncio.Lock()
message_queue = asyncio.Queue(maxsize=100)

# ------------------------------------
# 환경 변수
# ------------------------------------
RENDER_URL = os.getenv("RENDER_EXTERNAL_URL", "https://mcp-relay-server.onrender.com")

# ------------------------------------
# MCP Manifest (OpenAI 스펙 준수)
# ------------------------------------
@app.get("/.well-known/mcp.json")
async def mcp_manifest():
    """
    ChatGPT MCP 디스커버리 엔드포인트
    """
    return {
        "sse_endpoint": f"{RENDER_URL}/sse"
    }

# ------------------------------------
# Health Check
# ------------------------------------
@app.get("/")
def health_check():
    return {
        "status": "Running",
        "agent_connected": local_agent_ws is not None,
        "queue_size": message_queue.qsize(),
        "timestamp": datetime.utcnow().isoformat(),
        "mcp_endpoint": f"{RENDER_URL}/sse"
    }

# =================================================================
# 1. Local Agent (WebSocket)
# =================================================================
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    global local_agent_ws

    try:
        await websocket.accept()
        async with ws_lock:
            if local_agent_ws is not None:
                try:
                    await local_agent_ws.close()
                except:
                    pass
            local_agent_ws = websocket

        logger.info("[Connect] Local Agent Connected")

        while True:
            data = await websocket.receive_text()
            logger.info(f"[From Local] {data[:100]}...")

            try:
                message_queue.put_nowait(data)
            except asyncio.QueueFull:
                try:
                    message_queue.get_nowait()
                    message_queue.put_nowait(data)
                    logger.warning("[Queue] Dropped old message")
                except:
                    pass

    except WebSocketDisconnect:
        logger.warning("[Disconnect] Local Agent Disconnected")
    except Exception as e:
        logger.error(f"[WebSocket Error] {e}", exc_info=True)
    finally:
        async with ws_lock:
            if local_agent_ws == websocket:
                local_agent_ws = None


# =================================================================
# 2. SSE Endpoint (MCP 프로토콜 준수)
# =================================================================
@app.get("/sse")
async def sse_endpoint(request: Request):
    """
    MCP SSE 트랜스포트 엔드포인트
    스펙: https://spec.modelcontextprotocol.io/specification/basic/transports/#http-with-sse
    """
    async def event_generator():
        try:
            # ✅ 필수: 초기 endpoint 이벤트 전송
            logger.info("[SSE] Client connected, sending endpoint")
            yield {
                "event": "endpoint",
                "data": json.dumps({
                    "uri": f"{RENDER_URL}/command"
                })
            }
            
            # 메시지 스트림
            while True:
                if await request.is_disconnected():
                    logger.info("[SSE] Client disconnected")
                    break
                
                try:
                    data = await asyncio.wait_for(
                        message_queue.get(),
                        timeout=30.0
                    )
                    logger.info(f"[SSE] Sending data: {data[:100]}...")
                    yield {"data": data}
                    
                except asyncio.TimeoutError:
                    # Keepalive
                    yield {"comment": "keepalive"}
                    
        except Exception as e:
            logger.error(f"[SSE Error] {e}", exc_info=True)

    return EventSourceResponse(event_generator())


# =================================================================
# 3. Command Endpoint (MCP 메시지 수신)
# =================================================================
@app.post("/command")
async def send_command(request: Request):
    """
    ChatGPT → Local Agent 명령 전달
    """
    async with ws_lock:
        if local_agent_ws is None:
            return JSONResponse(
                {"error": "Local Agent is NOT connected."},
                status_code=503
            )

        try:
            body = await request.json()
            cmd_str = json.dumps(body)
            await local_agent_ws.send_text(cmd_str)

            logger.info(f"[To Local] Command: {cmd_str[:100]}...")

            return {"status": "Sent", "command": body}

        except Exception as e:
            logger.error(f"[Command Error] {e}")
            return JSONResponse({"error": str(e)}, status_code=500)


# =================================================================
# 4. Uvicorn 실행
# =================================================================
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 10000))
    
    logger.info(f"Starting MCP Relay Server on port {port}")
    logger.info(f"SSE Endpoint: {RENDER_URL}/sse")
    logger.info(f"Command Endpoint: {RENDER_URL}/command")
    
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port
    )
