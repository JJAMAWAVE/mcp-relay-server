"""
tools_registry.py
자동 Tool 등록 시스템 (플러그인 방식)

- tools/ 폴더에 있는 *tools.py 파일을 자동 스캔
- 내부의 함수 중 @mcp_tool 데코레이터가 붙은 함수만 MCP 툴로 등록
- main.py와 agent_server.py는 이 파일을 import 해서 사용
"""

import importlib
import pkgutil
import os
import inspect

# tools 폴더 경로 (로컬 MCP Agent 기준)
TOOLS_PACKAGE = "tools"

# 등록된 MCP Tools (main.py가 이걸 사용함)
TOOLS = {}


# --------------------------------------------------
# MCP Tool 데코레이터
# --------------------------------------------------
def mcp_tool(name=None, description="", input_schema=None):
    """
    @mcp_tool(name="unity_command", description="Unity 명령", input_schema={...})
    """

    def decorator(func):
        tool_name = name or func.__name__

        TOOLS[tool_name] = {
            "callable": func,
            "description": description,
            "inputSchema": input_schema or {
                "type": "object",
                "properties": {},
                "required": []
            }
        }

        return func

    return decorator


# --------------------------------------------------
# tools/ 폴더 자동 스캔하여 MCP 툴 로드
# --------------------------------------------------
def load_all_tools():
    global TOOLS

    package_dir = os.path.join(os.getcwd(), TOOLS_PACKAGE)

    if not os.path.exists(package_dir):
        print(f"[tools_registry] Tools directory not found: {package_dir}")
        return

    # 패키지 스캔
    for module_info in pkgutil.iter_modules([package_dir]):
        module_name = f"{TOOLS_PACKAGE}.{module_info.name}"

        try:
            module = importlib.import_module(module_name)

            # 모듈 내 함수 확인
            for _, obj in inspect.getmembers(module):
                # @mcp_tool 데코레이터가 붙은 함수만 등록됨
                if hasattr(obj, "_is_mcp_tool"):
                    # 데코레이터에서 이미 TOOLS에 등록함
                    pass

        except Exception as e:
            print(f"[tools_registry] Failed to load {module_name}: {e}")


# --------------------------------------------------
# 데코레이터에서 마킹할 때 사용
# --------------------------------------------------
def mark_as_tool(func):
    func._is_mcp_tool = True
    return func


# --------------------------------------------------
# 최종 초기 실행: tools/ 자동 로딩
# --------------------------------------------------
load_all_tools()
