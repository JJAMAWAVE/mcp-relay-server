import asyncio
import json
import logging
from fastapi import FastAPI, WebSocket, Request, BackgroundTasks
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse
from starlette.websockets import WebSocketDisconnect

# 로깅 설정
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("RelayServer")

app = FastAPI()

# --- 상태 관리 ---
# 로컬 PC(Local Agent)와의 소켓 연결을 하나만 유지합니다.
local_agent_ws: WebSocket = None

# ChatGPT에게 보낼 메시지(로그, 에러 등)를 임시 저장하는 큐
# 여러 ChatGPT 세션이 붙을 수 있으므로 브로드캐스팅 큐가 필요하지만,
# 개인용이므로 단순화하여 전역 큐를 사용합니다.
message_queue = asyncio.Queue()

@app.get("/")
def health_check():
    """서버 상태 확인용"""
    return {
        "status": "Running",
        "agent_connected": local_agent_ws is not None
    }

# =================================================================
# 1. 로컬 PC 접속용 (WebSocket) - Local Agent가 여기로 접속함
# =================================================================
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    global local_agent_ws
    await websocket.accept()
    local_agent_ws = websocket
    logger.info("[Connect] Local Agent Connected!")
    
    try:
        while True:
            # 로컬 PC에서 보내온 데이터(Unity 로그, 실행 결과 등) 수신
            data = await websocket.receive_text()
            logger.info(f"[From Local] {data[:100]}...") # 로그 너무 길면 자름
            
            # ChatGPT가 듣고 있는 SSE 스트림 큐에 넣음
            await message_queue.put(data)
            
    except WebSocketDisconnect:
        logger.warning("[Disconnect] Local Agent Disconnected")
        local_agent_ws = None
    except Exception as e:
        logger.error(f"[Error] WebSocket error: {e}")
        local_agent_ws = None

# =================================================================
# 2. ChatGPT 청취용 (SSE) - ChatGPT MCP Tool이 여기를 구독함
# =================================================================
@app.get("/events")
async def sse_endpoint(request: Request):
    """
    ChatGPT가 이 엔드포인트를 호출하면 연결을 끊지 않고
    PC에서 올라오는 로그를 실시간으로 계속 받습니다.
    """
    async def event_generator():
        while True:
            if await request.is_disconnected():
                break
            
            # 큐에 새로운 메시지가 들어올 때까지 대기 (비동기)
            data = await message_queue.get()
            
            # SSE 포맷으로 전송
            yield {"data": data}
            
    return EventSourceResponse(event_generator())

# =================================================================
# 3. ChatGPT 명령 전달용 (POST) - ChatGPT가 명령을 내리는 곳
# =================================================================
@app.post("/command")
async def send_command(request: Request):
    """
    ChatGPT가 내린 명령(JSON)을 받아서 로컬 PC로 즉시 전송합니다.
    """
    global local_agent_ws
    
    if local_agent_ws is None:
        return JSONResponse(
            {"error": "Local Agent is NOT connected. Please run 'socket_bridge.py' on your PC."},
            status_code=503
        )
    
    try:
        body = await request.json()
        command_str = json.dumps(body)
        
        # 로컬 PC의 WebSocket으로 명령 발사
        await local_agent_ws.send_text(command_str)
        logger.info(f"[To Local] Command Forwarded: {command_str}")
        
        return {"status": "Sent", "command": body}
        
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)