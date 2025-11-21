import asyncio
import json
import logging
import time
import websockets
import subprocess
import os

from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse
from starlette.websockets import WebSocketDisconnect


# 로컬 AI Manager 준비용 (초기단계)
# from local_ai_manager import run_local_ai

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("LocalAgentMCP")

# ======================================
# GLOBALS
# ======================================
RENDER_WS_URL = "wss://YOUR_PROD_RENDER_URL/ws"   # <- 반드시 PROD로 변경
RECONNECT_DELAY = 3

app = FastAPI()

# SSE Push Queue
message_queue = asyncio.Queue()

# Render WebSocket
render_ws = None


# ======================================
# 1) Render WS Auto-Reconnect Client
# ======================================
async def render_ws_connect():
    global render_ws

    while True:
        try:
            logger.info(f"[WS] Connecting to Render: {RENDER_WS_URL}")
            async with websockets.connect(
                RENDER_WS_URL,
                ping_interval=None,
                ping_timeout=None
            ) as websocket:

                render_ws = websocket
                logger.info("[WS] Connected to Render")

                listener = asyncio.create_task(ws_listener(websocket))
                pinger   = asyncio.create_task(ws_heartbeat(websocket))

                await asyncio.gather(listener, pinger)

        except Exception as e:
            logger.error(f"[WS] Disconnected: {e}")
            render_ws = None

        logger.info(f"[WS] Reconnecting in {RECONNECT_DELAY} sec...")
        await asyncio.sleep(RECONNECT_DELAY)


async def ws_listener(ws):
    """Render → Python (ChatGPT 명령)"""
    while True:
        try:
            msg = await ws.recv()
            logger.info(f"[WS → Local] {msg}")

            # ChatGPT → Unity 명령 라우팅
            try:
                payload = json.loads(msg)
                unity_result = UnityPipeClient.send(payload)
                logger.info(f"[Unity] Response: {unity_result}")

                # Unity 응답도 ChatGPT에게 SSE로 전달
                await message_queue.put(unity_result)

            except Exception as e:
                logger.error(f"Command Handling Error: {e}")

        except websockets.exceptions.ConnectionClosed:
            logger.warning("[WS] Listener closed")
            break


async def ws_heartbeat(ws):
    """Ping/Pong KeepAlive"""
    while True:
        try:
            await asyncio.sleep(20)
            pong = await ws.ping()
            await asyncio.wait_for(pong, timeout=10)
        except:
            logger.warning("[WS] Ping timeout")
            raise


# ======================================
# 2) FastAPI HTTP + SSE
# ======================================
@app.get("/")
def health():
    return {"status": "ok", "render_ws_connected": render_ws is not None}


@app.get("/events")
async def sse_endpoint(request: Request):
    async def event_generator():
        while True:
            if await request.is_disconnected():
                break
            data = await message_queue.get()
            yield {"data": data}
    return EventSourceResponse(event_generator())


@app.post("/command")
async def send_command(request: Request):
    """ChatGPT → Python 명령 → Render WS로 전달"""
    global render_ws

    if render_ws is None:
        return {"error": "Render WS not connected"}

    body = await request.json()
    cmd = json.dumps(body)

    try:
        await render_ws.send(cmd)
        return {"status": "sent", "command": body}
    except Exception as e:
        return {"error": str(e)}


# ======================================
# 3) Optional: Git Auto Commit (초기 구현)
# ======================================
def git_auto_commit(message="AutoCommit"):
    try:
        subprocess.run(["git", "add", "-A"], cwd=".", check=False)
        subprocess.run(["git", "commit", "-m", message], cwd=".", check=False)
        logger.info(f"[Git] Commit: {message}")
    except Exception as e:
        logger.error(f"[Git] Failed: {e}")


# ======================================
# APP STARTUP
# ======================================
@app.on_event("startup")
async def startup_event():
    asyncio.create_task(render_ws_connect())


# ======================================
# RUN
# ======================================
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))

