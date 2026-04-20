"""OpenAI-compatible API Server — 暴露为 /v1/chat/completions 兼容端点.

支持:
- POST /v1/chat/completions (streaming + non-streaming)
- GET /v1/models (列出可用模型)
- Bearer token 认证
- 兼容 Open WebUI, LobeChat, LibreChat, NextChat 等前端

用法:
    server = OpenAIAPIServer(agent_engine=engine)
    await server.start(host="0.0.0.0", port=8080)
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class APIConfig:
    """API 配置."""
    host: str = "0.0.0.0"
    port: int = 8080
    api_key: str = ""  # 空 = 不验证
    model_name: str = "xjd-agent"
    max_tokens: int = 4096


class OpenAIAPIServer:
    """OpenAI 兼容 API 服务器."""

    def __init__(
        self,
        agent_engine: Any = None,
        config: Optional[APIConfig] = None,
    ) -> None:
        self._engine = agent_engine
        self._config = config or APIConfig()
        self._app = None
        self._request_count = 0

    def _create_app(self) -> Any:
        """创建 ASGI 应用."""
        try:
            from starlette.applications import Starlette
            from starlette.requests import Request
            from starlette.responses import JSONResponse, StreamingResponse
            from starlette.routing import Route
        except ImportError:
            raise ImportError("需要安装 starlette: pip install starlette uvicorn")

        async def auth_check(request: Request) -> Optional[JSONResponse]:
            """Bearer token 验证."""
            if not self._config.api_key:
                return None
            auth = request.headers.get("Authorization", "")
            import hmac
            if not auth.startswith("Bearer ") or not hmac.compare_digest(auth[7:], self._config.api_key):
                return JSONResponse(
                    {"error": {"message": "Invalid API key", "type": "invalid_request_error", "code": "invalid_api_key"}},
                    status_code=401,
                )
            return None

        async def chat_completions(request: Request) -> Any:
            """POST /v1/chat/completions — OpenAI 兼容."""
            err = await auth_check(request)
            if err:
                return err

            body = await request.json()
            messages = body.get("messages", [])
            stream = body.get("stream", False)
            model = body.get("model", self._config.model_name)
            temperature = body.get("temperature")
            max_tokens = body.get("max_tokens", self._config.max_tokens)

            if not messages:
                return JSONResponse(
                    {"error": {"message": "messages is required", "type": "invalid_request_error"}},
                    status_code=400,
                )

            self._request_count += 1
            request_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"

            # 提取最后一条用户消息
            user_msg = ""
            for m in reversed(messages):
                if m.get("role") == "user":
                    user_msg = m.get("content", "")
                    break

            if stream:
                return StreamingResponse(
                    self._stream_response(request_id, model, user_msg),
                    media_type="text/event-stream",
                )

            # 非流式
            content = await self._generate(user_msg)
            return JSONResponse({
                "id": request_id,
                "object": "chat.completion",
                "created": int(time.time()),
                "model": model,
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }],
                "usage": {
                    "prompt_tokens": len(user_msg) // 4,
                    "completion_tokens": len(content) // 4,
                    "total_tokens": (len(user_msg) + len(content)) // 4,
                },
            })

        async def list_models(request: Request) -> JSONResponse:
            """GET /v1/models — 列出可用模型."""
            err = await auth_check(request)
            if err:
                return err
            return JSONResponse({
                "object": "list",
                "data": [{
                    "id": self._config.model_name,
                    "object": "model",
                    "created": int(time.time()),
                    "owned_by": "xjd-agent",
                }],
            })

        async def health(request: Request) -> JSONResponse:
            return JSONResponse({"status": "ok", "requests": self._request_count})

        app = Starlette(routes=[
            Route("/v1/chat/completions", chat_completions, methods=["POST"]),
            Route("/v1/models", list_models, methods=["GET"]),
            Route("/health", health, methods=["GET"]),
        ])
        return app

    async def _generate(self, user_msg: str) -> str:
        """调用 agent engine 生成回复."""
        if not self._engine:
            return "Agent engine not configured"
        try:
            result = await self._engine.run_turn(user_msg)
            return result.content
        except Exception as e:
            logger.error("生成失败: %s", e)
            return f"Error: {e}"

    async def _stream_response(self, request_id: str, model: str, user_msg: str):
        """SSE 流式响应."""
        try:
            content = await self._generate(user_msg)

            # 模拟分块输出
            chunk_size = 20
            for i in range(0, len(content), chunk_size):
                chunk = content[i:i + chunk_size]
                data = {
                    "id": request_id,
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": model,
                    "choices": [{
                        "index": 0,
                        "delta": {"content": chunk},
                        "finish_reason": None,
                    }],
                }
                yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

            # 结束标记
            data = {
                "id": request_id,
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": model,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            }
            yield f"data: {json.dumps(data)}\n\n"
            yield "data: [DONE]\n\n"
        except Exception as e:
            logger.error("Stream response error: %s", e)
            error_data = {
                "id": request_id,
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": model,
                "choices": [{"index": 0, "delta": {"content": f"\n[Error: {e}]"}, "finish_reason": "stop"}],
            }
            yield f"data: {json.dumps(error_data)}\n\n"
            yield "data: [DONE]\n\n"

    async def start(self, host: str = "", port: int = 0) -> None:
        """启动 API 服务器."""
        import uvicorn

        h = host or self._config.host
        p = port or self._config.port
        app = self._create_app()
        config = uvicorn.Config(app, host=h, port=p, log_level="info")
        server = uvicorn.Server(config)
        await server.serve()
