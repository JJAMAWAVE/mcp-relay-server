import asyncio
import json
import logging
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, WebSocket, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette.sse import EventSourceResponse
from starlette.websockets import WebSocketDisconnect

# 1. 로깅 설정
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("RelayServer")

app = FastAPI()

# 2. CORS 설정 (ChatGPT가 브라우저에서 접속하므로 필수)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 보안상 특정 도메인으로 제한하면 좋으나, 개발 단계에선 * 허용
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 3. 상태 관리 (동시성 제어 강화)
local_agent_ws: Optional[WebSocket] = None
ws_lock = asyncio.Lock()  # 연결/해제 시 충돌 방지용 락

# 큐 크기 제한 (메모리 폭발 방지)
message_queue = asyncio.Queue(maxsize=100)

@app.get("/")
def health_check():
    """서버 상태 및 연결 여부 확인"""
    return {
        "status": "Running",
        "agent_connected": local_agent_ws is not None,
        "queue_size": message_queue.qsize(),
        "timestamp": datetime.utcnow().isoformat()
    }

# =================================================================
# 1. 로컬 PC 접속용 (WebSocket) - 안전장치 대폭 강화
# =================================================================
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    global local_agent_ws
    
    try:
        await websocket.accept()
        
        # 동시성 제어: 기존 연결이 있다면 안전하게 끊고 교체
        async with ws_lock:
            if local_agent_ws is not None:
                try:
                    await local_agent_ws.close()
                    logger.warning("[Replace] Closing previous connection.")
                except:
                    pass
            local_agent_ws = websocket
            logger.info("[Connect] Local Agent Connected!")

        while True:
            # 로컬 PC 데이터 수신
            data = await websocket.receive_text()
            logger.info(f"[From Local] {data[:100]}...")

            # 큐에 넣기 (꽉 찼으면 오래된 것 버림 - 렉 방지)
            try:
                message_queue.put_nowait(data)
            except asyncio.QueueFull:
                try:
                    message_queue.get_nowait() # 하나 버리고
                    message_queue.put_nowait(data) # 새거 넣기
                    logger.warning("[Queue] Dropped old message (Buffer Full)")
                except:
                    pass

    except WebSocketDisconnect:
        logger.warning("[Disconnect] Local Agent Disconnected")
    except Exception as e:
        logger.error(f"[WebSocket Error] {e}", exc_info=True)
    finally:
        # 연결 종료 시 깔끔하게 정리
        async with ws_lock:
            if local_agent_ws == websocket:
                local_agent_ws = None

# =================================================================
# 2. ChatGPT 청취용 (SSE) - Keepalive 추가 (끊김 방지)
# =================================================================
@app.get("/events")
async def sse_endpoint(request: Request):
    async def event_generator():
        try:
            while True:
                if await request.is_disconnected():
                    break
                
                try:
                    # 30초 동안 데이터 없으면 타임아웃 발생시킴
                    data = await asyncio.wait_for(message_queue.get(), timeout=30.0)
                    yield {"data": data}
                except asyncio.TimeoutError:
                    # 30초마다 빈 신호를 보내 연결 유지 (Heartbeat)
                    yield {"comment": "keepalive"}
                    
        except Exception as e:
            logger.error(f"[SSE Error] {e}")

    return EventSourceResponse(event_generator())

# =================================================================
# 3. ChatGPT 명령 전달용 (POST) - Lock 적용
# =================================================================
@app.post("/command")
async def send_command(request: Request):
    """ChatGPT -> Render -> Local PC 명령 전송"""
    
    # Lock을 걸고 소켓 상태 확인 (전송 중 끊김 방지)
    async with ws_lock:
        if local_agent_ws is None:
            return JSONResponse(
                {"error": "Local Agent is NOT connected."},
                status_code=503
            )
        
        try:
            body = await request.json()
            command_str = json.dumps(body)
            
            await local_agent_ws.send_text(command_str)
            logger.info(f"[To Local] Command Forwarded: {command_str}")
            
            return {"status": "Sent", "command": body}
            
        except Exception as e:
            logger.error(f"[Command Error] {e}")
            return JSONResponse({"error": str(e)}, status_code=500)
