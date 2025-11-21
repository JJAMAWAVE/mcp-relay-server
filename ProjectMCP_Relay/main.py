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
# FastAPI App 생성
# ------------------------------------
app = FastAPI()

# ------------------------------------
# CORS (필수)
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
# MCP Manifest (중요!)
# ------------------------------------
@app.get("/.well-known/mcp.json")
async def mcp_manifest():
    """
    ChatGPT가 MCP 서버 정보를 읽어가는 엔드포인트
    ⚠️ url은 실제 Render 배포 URL로 변경 필수!
    """
    render_url = os.getenv("RENDER_EXTERNAL_URL", "https://your-app.onrender.com")
    
    return {
        "mcpServers": {
            "relay": {
                "command": "sse",
                "url": render_url,
                "sse": {
                    "endpoint": "/events"
                },
                "transport": {
                    "type": "sse"
                },
                "capabilities": {
                    "logging": {}
                }
            }
        }
    }

# ------------------------------------
# 헬스 체크
# ------------------------------------
@app.get("/")
def health_check():
    return {
        "status": "Running",
        "agent_connected": local_agent_ws is not None,
        "queue_size": message_queue.qsize(),
        "timestamp": datetime.utcnow().isoformat()
    }

# =================================================================
# 1. Local Agent(WebSocket 연결)
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
                    logger.warning("[Queue] Dropped old message (Buffer Full)")
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
# 2. ChatGPT용 SSE Stream
# =================================================================
@app.get("/events")
async def sse_endpoint(request: Request):
    async def event_generator():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    data = await asyncio.wait_for(message_queue.get(), timeout=30.0)
                    yield {"data": data}
                except asyncio.TimeoutError:
                    yield {"comment": "keepalive"}
        except Exception as e:
            logger.error(f"[SSE Error] {e}")

    return EventSourceResponse(event_generator())


# =================================================================
# 3. ChatGPT 명령 → LocalAgent 전달 (POST)
# =================================================================
@app.post("/command")
async def send_command(request: Request):
    global local_agent_ws

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

            logger.info(f"[To Local] Command Sent: {cmd_str}")

            return {"status": "Sent", "command": body}

        except Exception as e:
            logger.error(f"[Command Error] {e}")
            return JSONResponse({"error": str(e)}, status_code=500)


# =================================================================
# 4. Uvicorn 실행 (Render용)
# =================================================================
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 10000))  # Render 환경 변수 우선
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port
    )
