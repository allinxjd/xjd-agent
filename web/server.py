"""Web 前端服务 — FastAPI + WebSocket 聊天界面 + Admin REST API.

提供:
- WebSocket 实时聊天 (/ws)
- REST API (/api/...)
- 静态前端 (/index.html)
- Admin 后台接口 (/api/admin/...) — 带 JWT 认证
- 健康检查 (/health)
- Prometheus 指标 (/metrics)
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class AuditEntry:
    """审计日志条目."""
    timestamp: float
    user: str
    action: str
    detail: str = ""
    ip: str = ""


@dataclass
class WebSession:
    """Web 用户会话."""

    session_id: str
    user_id: str = "web_user"
    username: str = "User"
    connected_at: float = 0.0
    last_active: float = 0.0
    message_count: int = 0


class WebServer:
    """Web 前端服务器.

    用法:
        server = WebServer(agent_engine=engine, config={...})
        await server.start(host="0.0.0.0", port=8080)
    """

    def __init__(
        self,
        agent_engine=None,
        config: Optional[dict] = None,
    ) -> None:
        self._engine = agent_engine
        self._config = config or {}
        self._app = None
        self._sessions: dict[str, WebSession] = {}
        self._ws_connections: dict[str, Any] = {}  # session_id → WebSocket
        self._static_dir = None  # set in start()
        self._auth = None  # AuthManager, initialized in start()

        # 内嵌 Gateway (可选)
        self._gateway = None  # GatewayServer
        self._global_config = None  # Config 对象 (用于 save)

        # 审计日志 (最近 500 条)
        self._audit_log: deque[AuditEntry] = deque(maxlen=500)

        # TTS pipeline 缓存
        self._tts_pipeline = None
        self._tts_pipeline_key = None

        # Inspector 订阅
        self._inspector_subs: set[str] = set()
        self._inspector_write_lock = asyncio.Lock()
        from pathlib import Path as _Path
        self._inspector_log_path = str(_Path.home() / ".xjd-agent" / "inspector.jsonl")

        # Background tasks tracking
        self._bg_tasks: set[asyncio.Task] = set()

        # 会话持久化 (SQLite)
        _db_path = str(_Path.home() / ".xjd-agent" / "web_sessions.db")
        from gateway.core.session import SessionManager
        self._session_mgr = SessionManager(
            dm_policy="pairing",
            session_timeout=3600 * 24,
            db_path=_db_path,
        )
        self._ws_sessions: dict[str, Any] = {}  # ws_session_id → gateway Session
        self._ws_locks: dict[str, asyncio.Lock] = {}  # ws_session_id → 并发锁
        self._ws_active_tasks: dict[str, asyncio.Task] = {}  # ws_session_id → 当前 run_turn 任务

        # 工作目录 (文件浏览沙箱)
        self._workspace_dir = self._config.get("workspace_dir", ".")

        # Hub 客户端
        self._hub_client = None

        # 指标
        self._total_requests = 0
        self._total_messages = 0
        self._start_time = time.time()

        # Rate limiting: IP → deque of timestamps
        self._rate_buckets: dict[str, deque] = {}

        # WebSocket per-IP connection tracking
        self._ws_per_ip: dict[str, int] = {}
        self._WS_MAX_PER_IP = 10

    async def start(self, host: str = "0.0.0.0", port: int = 8080) -> None:
        """启动 Web 服务."""
        try:
            from aiohttp import web
        except ImportError:
            raise ImportError("aiohttp 未安装。请运行: pip install aiohttp")

        import pathlib
        self._static_dir = pathlib.Path(__file__).parent / "static"

        # 初始化认证 (仅在配置了 secret_key 时启用)
        secret_key = self._config.get("secret_key", "")
        if secret_key:
            try:
                from gateway.core.auth import AuthManager
                self._auth = AuthManager(
                    secret_key=secret_key,
                    db_path=self._config.get("auth_db", "data/auth.db"),
                )
                await self._auth.initialize()
                logger.info("AuthManager initialized")
            except Exception as e:
                logger.warning("AuthManager init failed: %s — admin API unprotected", e)
        else:
            logger.info("No secret_key configured — admin API unprotected (dev mode)")

        app = web.Application(middlewares=self._create_middlewares())

        # 路由
        app.router.add_get("/", self._serve_index)
        app.router.add_get("/health", self._health)
        app.router.add_get("/metrics", self._metrics)
        app.router.add_get("/ws", self._websocket_handler)

        # Auth API (public)
        app.router.add_get("/api/auth/status", self._auth_status)
        app.router.add_post("/api/auth/login", self._auth_login)
        app.router.add_post("/api/auth/register", self._auth_register)
        app.router.add_post("/api/auth/logout", self._auth_logout)

        # REST API
        app.router.add_post("/api/chat", self._api_chat)
        app.router.add_get("/api/sessions", self._api_sessions)
        app.router.add_post("/api/reset", self._api_reset)

        # Admin API (protected)
        app.router.add_get("/api/admin/stats", self._admin_stats)
        app.router.add_get("/api/admin/models", self._admin_models)
        app.router.add_post("/api/admin/model-config", self._admin_set_model)
        app.router.add_get("/api/admin/tools", self._admin_tools)
        app.router.add_get("/api/admin/memory", self._admin_memory)
        app.router.add_get("/api/admin/sessions", self._admin_sessions)
        app.router.add_post("/api/admin/config", self._admin_update_config)
        app.router.add_get("/api/admin/system-prompt", self._admin_get_system_prompt)
        app.router.add_post("/api/admin/system-prompt", self._admin_set_system_prompt)
        app.router.add_get("/api/admin/audit", self._admin_audit_log)
        app.router.add_get("/api/admin/inspector/events", self._admin_inspector_events)
        app.router.add_get("/api/admin/users", self._admin_list_users)
        app.router.add_post("/api/admin/users", self._admin_create_user)

        # Workspace API (files, memory, skills, context)
        app.router.add_get("/api/workspace/files", self._ws_files_list)
        app.router.add_get("/api/workspace/file", self._ws_file_read)
        app.router.add_get("/api/workspace/context/pins", self._ctx_list_pins)
        app.router.add_post("/api/workspace/context/pins", self._ctx_add_pin)
        app.router.add_put("/api/workspace/context/pins/{pin_id}", self._ctx_update_pin)
        app.router.add_delete("/api/workspace/context/pins/{pin_id}", self._ctx_remove_pin)
        app.router.add_post("/api/workspace/context/pins/reorder", self._ctx_reorder_pins)
        app.router.add_get("/api/workspace/context/activity", self._ctx_activity)
        app.router.add_get("/api/workspace/context/preview", self._ctx_preview)
        app.router.add_get("/api/workspace/memory/list", self._ws_memory_list)
        app.router.add_get("/api/workspace/memory/search", self._ws_memory_search)
        app.router.add_get("/api/workspace/memory/detail", self._ws_memory_detail)
        app.router.add_post("/api/workspace/memory/create", self._ws_memory_create)
        app.router.add_put("/api/workspace/memory/{memory_id}", self._ws_memory_update)
        app.router.add_delete("/api/workspace/memory/{memory_id}", self._ws_memory_delete)
        app.router.add_get("/api/workspace/memory-health", self._ws_memory_health)
        app.router.add_get("/api/workspace/metrics", self._ws_metrics)
        app.router.add_get("/api/workspace/skills", self._ws_skills_list)

        # Canvas API
        app.router.add_get("/api/workspace/canvas/list", self._canvas_list)
        app.router.add_get("/api/workspace/canvas/{artifact_id}", self._canvas_get)

        # Gateway Admin API (channels, voice, ecommerce)
        app.router.add_get("/api/admin/gateway/channels", self._gw_list_channels)
        app.router.add_post("/api/admin/gateway/channels", self._gw_save_channel)
        app.router.add_delete("/api/admin/gateway/channels/{platform}", self._gw_delete_channel)
        app.router.add_post("/api/admin/gateway/channels/{platform}/start", self._gw_start_channel)
        app.router.add_post("/api/admin/gateway/channels/{platform}/stop", self._gw_stop_channel)
        app.router.add_get("/api/admin/gateway/channels/{platform}/login-state", self._gw_channel_login_state)
        app.router.add_get("/api/admin/gateway/channels/{platform}/contacts", self._gw_channel_contacts)
        app.router.add_post("/api/admin/gateway/channels/{platform}/send", self._gw_channel_send)
        app.router.add_get("/api/admin/gateway/schemas", self._gw_schemas)
        app.router.add_get("/api/admin/gateway/voice", self._gw_get_voice)
        app.router.add_post("/api/admin/gateway/voice", self._gw_save_voice)
        app.router.add_post("/api/admin/gateway/voice/test", self._gw_test_voice)
        app.router.add_get("/api/admin/gateway/ecommerce", self._gw_get_ecommerce)
        app.router.add_post("/api/admin/gateway/ecommerce", self._gw_save_ecommerce)
        app.router.add_get("/api/admin/gateway/calabash", self._gw_get_calabash)
        app.router.add_post("/api/admin/gateway/calabash", self._gw_save_calabash)
        app.router.add_get("/api/admin/gateway/cron/tasks", self._gw_cron_list)
        app.router.add_post("/api/admin/gateway/cron/tasks/{task_id}/run", self._gw_cron_run)
        app.router.add_get("/api/admin/skill-secrets", self._skill_secrets_list)
        app.router.add_get("/api/admin/skill-secrets/{skill_id}", self._skill_secrets_get)
        app.router.add_post("/api/admin/skill-secrets/{skill_id}", self._skill_secrets_save)

        # Skill Admin API
        app.router.add_get("/api/admin/skills", self._skill_list)
        app.router.add_post("/api/admin/skills", self._skill_create)
        app.router.add_get("/api/admin/skills/{skill_id}", self._skill_detail)
        app.router.add_put("/api/admin/skills/{skill_id}", self._skill_update)
        app.router.add_delete("/api/admin/skills/{skill_id}", self._skill_delete)
        app.router.add_post("/api/admin/skills/{skill_id}/test", self._skill_test)
        app.router.add_get("/api/admin/skills/{skill_id}/versions", self._skill_versions)
        app.router.add_post("/api/admin/skills/{skill_id}/rollback", self._skill_rollback)

        # XjdHub API
        app.router.add_get("/api/admin/hub/search", self._hub_search)
        app.router.add_get("/api/admin/hub/categories", self._hub_categories)
        app.router.add_get("/api/admin/hub/featured", self._hub_featured)
        app.router.add_get("/api/admin/hub/skill/{slug}", self._hub_detail)
        app.router.add_post("/api/admin/hub/install", self._hub_install)
        app.router.add_post("/api/admin/hub/publish", self._hub_publish)
        app.router.add_get("/api/admin/hub/published", self._hub_published)

        # Hub Remote (充值代理)
        app.router.add_post("/api/admin/hub/account/register", self._hub_remote_register)
        app.router.add_post("/api/admin/hub/account/login", self._hub_remote_login)
        app.router.add_get("/api/admin/hub/account/balance", self._hub_remote_balance)
        app.router.add_get("/api/admin/hub/recharge/packages", self._hub_remote_packages)
        app.router.add_post("/api/admin/hub/recharge/create", self._hub_remote_recharge_create)
        app.router.add_get("/api/admin/hub/recharge/status/{order_no}", self._hub_remote_recharge_status)

        # 静态文件
        app.router.add_static("/static", self._static_dir, show_index=False)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, host, port)
        await site.start()

        self._app = app
        self._runner = runner
        logger.info("Web server started at http://%s:%d", host, port)

    async def stop(self):
        """优雅关闭服务器."""
        # Drain background tasks
        if self._bg_tasks:
            await asyncio.gather(*self._bg_tasks, return_exceptions=True)

        # 关闭所有 WebSocket 连接
        for sid, ws in list(self._ws_connections.items()):
            try:
                await ws.close()
            except Exception:
                pass
        self._ws_connections.clear()
        self._sessions.clear()

        # 持久化所有 web session
        try:
            await self._session_mgr.save_all()
            await self._session_mgr.close()
        except Exception as e:
            logger.warning("Session manager shutdown failed: %s", e)

        # Cleanup TTS pipeline
        if self._tts_pipeline and hasattr(self._tts_pipeline, 'close'):
            try:
                await self._tts_pipeline.close()
            except Exception:
                pass
            self._tts_pipeline = None

        # 关闭 AppRunner
        if hasattr(self, '_runner') and self._runner:
            await self._runner.cleanup()
            self._runner = None

    # ─── Middleware ───

    @staticmethod
    def _get_rate_limit(path: str) -> int:
        """根据路径返回每分钟请求限制."""
        if path.startswith("/api/auth/login") or path.startswith("/api/auth/register"):
            return 5
        if path.startswith("/api/chat"):
            return 30
        if path.startswith("/api/memory") or path.startswith("/api/workspace"):
            return 100
        if path.startswith("/api/admin"):
            return 60
        return 120

    def _create_middlewares(self):
        """创建 aiohttp middleware 列表."""
        from aiohttp import web

        server = self

        @web.middleware
        async def rate_limit(request, handler):
            server._total_requests += 1
            ip = request.remote or "unknown"
            path = request.path

            if path.startswith("/static") or path in ("/health", "/metrics", "/"):
                return await handler(request)

            limit = server._get_rate_limit(path)
            bucket_key = f"{ip}:{path.split('/')[2] if path.startswith('/api/') else 'other'}"
            now = time.time()

            if bucket_key not in server._rate_buckets:
                server._rate_buckets[bucket_key] = deque()
            bucket = server._rate_buckets[bucket_key]

            while bucket and bucket[0] < now - 60:
                bucket.popleft()

            if len(bucket) >= limit:
                return web.json_response(
                    {"error": "Too many requests, please try later"},
                    status=429,
                )
            bucket.append(now)

            # 定期清理过期 bucket，防止内存泄漏
            if len(server._rate_buckets) > 1000:
                stale = [k for k, v in server._rate_buckets.items() if not v or v[-1] < now - 120]
                for k in stale:
                    del server._rate_buckets[k]

            return await handler(request)

        @web.middleware
        async def csrf_check(request, handler):
            if request.method in ("POST", "PUT", "DELETE") and request.path != "/ws":
                csrf_val = request.headers.get("X-XJD-Request", "")
                if csrf_val != "1":
                    return web.json_response(
                        {"error": "Missing or invalid CSRF header"},
                        status=403,
                    )
            return await handler(request)

        @web.middleware
        async def security_headers(request, handler):
            # CORS preflight
            if request.method == "OPTIONS":
                resp = web.Response(status=204)
            else:
                resp = await handler(request)

            origin = request.headers.get("Origin", "")
            # 只允许同源或 localhost 开发
            allowed_origins = {f"http://localhost:{server._config.get('port', 8080)}", ""}
            if origin in allowed_origins or not origin:
                if origin:
                    resp.headers["Access-Control-Allow-Origin"] = origin
                    resp.headers["Access-Control-Allow-Credentials"] = "true"
            resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-XJD-Request, X-API-Key"
            resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
            resp.headers["X-Content-Type-Options"] = "nosniff"
            resp.headers["X-Frame-Options"] = "DENY"
            resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
            return resp

        return [rate_limit, csrf_check, security_headers]

    # ─── 认证辅助 ───

    def _audit(self, user: str, action: str, detail: str = "", ip: str = "") -> None:
        """记录审计日志."""
        self._audit_log.append(AuditEntry(
            timestamp=time.time(), user=user, action=action, detail=detail, ip=ip,
        ))

    def _get_auth_user(self, request):
        """从请求中提取认证用户. 返回 (user, error_response)."""
        from aiohttp import web

        if not self._auth:
            return None, None  # 无认证系统 → 放行

        # 尝试 Bearer token
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
            result = self._auth.authenticate_jwt(token)
            if result.authenticated:
                return result.user, None
            return None, web.json_response({"error": "Invalid token"}, status=401)

        # 尝试 API Key
        api_key = request.headers.get("X-API-Key", "")
        if api_key:
            result = self._auth.authenticate_api_key(api_key)
            if result.authenticated:
                return result.user, None
            return None, web.json_response({"error": "Invalid API key"}, status=401)

        return None, web.json_response({"error": "Authentication required"}, status=401)

    def _require_admin(self, request):
        """要求 admin 权限. 返回 (user, error_response)."""
        from aiohttp import web
        from gateway.core.auth import Permission

        user, err = self._get_auth_user(request)
        if err:
            return None, err
        if user is None:
            return None, None  # 无认证系统 → 放行
        if not user.has_permission(Permission.ADMIN_READ):
            self._audit(user.username, "DENIED", request.path, request.remote or "")
            return None, web.json_response({"error": "Admin access required"}, status=403)
        return user, None

    # ─── Auth API (public) ───

    async def _auth_status(self, request):
        """GET /api/auth/status — 返回认证状态 (public, no auth required)."""
        from aiohttp import web

        if not self._auth:
            return web.json_response({"auth_enabled": False})

        # Check if any users exist
        needs_setup = len(self._auth._users) == 0

        if needs_setup:
            return web.json_response({"auth_enabled": True, "needs_setup": True})

        # Try to get current user from token (optional, no error if missing)
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
            result = self._auth.authenticate_jwt(token)
            if result.authenticated:
                return web.json_response({
                    "auth_enabled": True,
                    "needs_setup": False,
                    "user": {
                        "user_id": result.user.user_id,
                        "username": result.user.username,
                    },
                    "role": result.user.role.value,
                })

        return web.json_response({"auth_enabled": True, "needs_setup": False})

    async def _auth_login(self, request):
        """POST /api/auth/login — 用户名密码登录, 返回 JWT."""
        from aiohttp import web

        if not self._auth:
            return web.json_response({"error": "Auth not configured"}, status=501)

        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        username = body.get("username", "").strip()
        password = body.get("password", "")
        if not username or not password:
            return web.json_response({"error": "username and password required"}, status=400)

        result = self._auth.authenticate_password(username, password)
        if not result.authenticated:
            self._audit(username, "LOGIN_FAILED", result.error, request.remote or "")
            return web.json_response({"error": result.error}, status=401)

        self._audit(username, "LOGIN", "", request.remote or "")
        return web.json_response({
            "token": result.token.token,
            "user_id": result.user.user_id,
            "username": result.user.username,
            "role": result.user.role.value,
        })

    async def _auth_register(self, request):
        """POST /api/auth/register — 注册新用户 (首次无用户时免 admin token, 否则需 admin)."""
        from aiohttp import web

        if not self._auth:
            return web.json_response({"error": "Auth not configured"}, status=501)

        # Allow first user registration without admin token (setup flow)
        is_setup = len(self._auth._users) == 0
        user = None
        if not is_setup:
            user, err = self._require_admin(request)
            if err:
                return err

        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        username = body.get("username", "").strip()
        password = body.get("password", "")
        role = body.get("role", "user")
        if not username or not password:
            return web.json_response({"error": "username and password required"}, status=400)

        try:
            from gateway.core.auth import Role
            new_user = await self._auth.register_user(username, password, Role(role))
            admin_name = user.username if user else "system"
            self._audit(admin_name, "USER_CREATED", f"{username} ({role})", request.remote or "")
            return web.json_response({
                "user_id": new_user.user_id,
                "username": new_user.username,
                "role": new_user.role.value,
            })
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    async def _auth_logout(self, request):
        """POST /api/auth/logout — 登出 (客户端清除 token 即可)."""
        from aiohttp import web

        user, _ = self._get_auth_user(request)
        username = user.username if user else "unknown"
        self._audit(username, "LOGOUT", "", request.remote or "")
        return web.json_response({"ok": True})

    # ─── 页面 ───

    async def _serve_index(self, request):
        from aiohttp import web
        index_path = self._static_dir / "index.html"
        if index_path.exists():
            return web.FileResponse(index_path)
        return web.Response(text="<h1>index.html not found</h1>", content_type="text/html", status=404)

    async def _health(self, request):
        from aiohttp import web
        uptime = time.time() - self._start_time
        return web.json_response({
            "status": "ok",
            "uptime_seconds": round(uptime),
            "active_sessions": len(self._sessions),
            "total_messages": self._total_messages,
        })

    async def _metrics(self, request):
        from aiohttp import web
        uptime = time.time() - self._start_time
        lines = [
            f"# HELP xjd_uptime_seconds Server uptime",
            f"# TYPE xjd_uptime_seconds gauge",
            f"xjd_uptime_seconds {uptime:.0f}",
            f"# HELP xjd_total_messages Total messages processed",
            f"# TYPE xjd_total_messages counter",
            f"xjd_total_messages {self._total_messages}",
            f"# HELP xjd_active_sessions Active WebSocket sessions",
            f"# TYPE xjd_active_sessions gauge",
            f"xjd_active_sessions {len(self._ws_connections)}",
            f"# HELP xjd_total_requests Total HTTP requests",
            f"# TYPE xjd_total_requests counter",
            f"xjd_total_requests {self._total_requests}",
        ]
        return web.Response(text="\n".join(lines), content_type="text/plain")

    # ─── WebSocket 聊天 ───

    async def _websocket_handler(self, request):
        from aiohttp import web, WSMsgType

        # Origin 验证 — 拒绝跨域 WebSocket 连接
        origin = request.headers.get("Origin", "")
        if origin:
            from urllib.parse import urlparse
            req_host = request.host.split(":")[0]
            origin_host = urlparse(origin).hostname or ""
            if origin_host not in (req_host, "localhost", "127.0.0.1"):
                return web.Response(text="Origin not allowed", status=403)

        # Per-IP 连接数限制
        client_ip = request.remote or "unknown"
        current = self._ws_per_ip.get(client_ip, 0)
        if current >= self._WS_MAX_PER_IP:
            return web.Response(text="Too many WebSocket connections", status=429)

        ws = web.WebSocketResponse(heartbeat=30.0, max_msg_size=1_048_576)
        await ws.prepare(request)

        self._ws_per_ip[client_ip] = self._ws_per_ip.get(client_ip, 0) + 1

        session_id = str(uuid.uuid4())[:12]
        session = WebSession(
            session_id=session_id,
            connected_at=time.time(),
            last_active=time.time(),
        )
        self._sessions[session_id] = session
        self._ws_connections[session_id] = ws

        # 恢复或创建持久化 session
        from gateway.platforms.base import PlatformType
        requested_sid = request.query.get("session_id", "")
        gw_session = None
        if requested_sid:
            gw_session = await self._session_mgr.resume_session(requested_sid)
        if not gw_session:
            gw_session = await self._session_mgr.get_or_create(
                user_id="web_user", platform=PlatformType.WEB, chat_id=session_id,
            )
        self._ws_sessions[session_id] = gw_session
        self._ws_locks[session_id] = asyncio.Lock()

        # 发送欢迎 + 持久化 session_id
        welcome = {
            "type": "connected",
            "session_id": session_id,
            "persistent_session_id": gw_session.session_id,
            "message": "Connected to XJDAgent",
        }
        # 恢复历史消息（最近 40 条，避免 welcome 包过大）
        if gw_session.messages:
            welcome["history"] = [
                {"role": m["role"], "content": m["content"]}
                for m in gw_session.messages[-40:]
            ]
        await ws.send_json(welcome)

        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    try:
                        payload = json.loads(msg.data) if msg.data.startswith('{') else {"type": "chat", "message": msg.data}
                    except (json.JSONDecodeError, ValueError):
                        payload = {"type": "chat", "message": msg.data}
                    msg_type = payload.get("type", "chat")
                    if msg_type == "ping":
                        await ws.send_json({"type": "pong"})
                    elif msg_type == "new_session":
                        gw_session = await self._session_mgr.get_or_create(
                            user_id="web_user", platform=PlatformType.WEB,
                            chat_id=str(uuid.uuid4())[:12],
                        )
                        self._ws_sessions[session_id] = gw_session
                        await ws.send_json({
                            "type": "session_created",
                            "persistent_session_id": gw_session.session_id,
                        })
                    elif msg_type in ("inspector_subscribe", "terminal_exec"):
                        await self._handle_ws_message(session_id, msg.data, ws)
                    else:
                        # 取消该 session 上一个还在跑的任务（避免 WS 重连后旧任务空转）
                        old_task = self._ws_active_tasks.get(session_id)
                        if old_task and not old_task.done():
                            old_task.cancel()
                        task = asyncio.create_task(self._handle_ws_message(session_id, msg.data, ws))
                        self._ws_active_tasks[session_id] = task
                        self._bg_tasks.add(task)
                        task.add_done_callback(self._bg_tasks.discard)
                elif msg.type in (WSMsgType.ERROR, WSMsgType.CLOSE):
                    break
        finally:
            # 取消正在跑的 run_turn 任务，避免 WS 断开后空转浪费 API 调用
            active_task = self._ws_active_tasks.pop(session_id, None)
            if active_task and not active_task.done():
                active_task.cancel()
            self._ws_connections.pop(session_id, None)
            self._ws_sessions.pop(session_id, None)
            self._ws_locks.pop(session_id, None)
            self._inspector_subs.discard(session_id)
            self._ws_per_ip[client_ip] = max(0, self._ws_per_ip.get(client_ip, 1) - 1)
            if self._ws_per_ip.get(client_ip) == 0:
                self._ws_per_ip.pop(client_ip, None)
            logger.info("WebSocket disconnected: %s", session_id)

        return ws

    async def _handle_ws_message(self, session_id: str, data: str, ws) -> None:
        """处理 WebSocket 消息."""
        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            payload = {"type": "chat", "message": data}

        msg_type = payload.get("type", "chat")
        user_message = payload.get("message", "")

        # 心跳
        if msg_type == "ping":
            try:
                await asyncio.wait_for(ws.send_json({"type": "pong"}), timeout=5.0)
            except (asyncio.TimeoutError, Exception):
                logger.warning("WS send timeout on pong, skipping")
            return

        if msg_type == "terminal_exec":
            if self._auth:
                try:
                    await asyncio.wait_for(ws.send_json({"type": "terminal_complete", "content": "Terminal requires authentication via admin API", "exit_code": 1}), timeout=5.0)
                except (asyncio.TimeoutError, Exception):
                    logger.warning("WS send timeout on terminal_complete, skipping")
                return
            await self._handle_terminal_exec(payload.get("command", ""), ws)
            return

        if msg_type == "inspector_subscribe":
            self._inspector_subs.add(session_id)
            return

        if msg_type == "a2ui_action":
            import re
            action = payload.get("action", "")[:100]
            action_payload = payload.get("payload", {})
            artifact_id = payload.get("artifact_id", "")
            if not re.match(r'^[a-zA-Z0-9_-]{0,20}$', artifact_id):
                return
            if not action:
                return
            a2ui_msg = f"[A2UI 交互] Canvas {artifact_id}: {action}"
            if action_payload:
                import json as _json
                a2ui_msg += f" — {_json.dumps(action_payload, ensure_ascii=False)[:500]}"
            payload = {"type": "chat", "message": a2ui_msg}
            msg_type = "chat"
            user_message = a2ui_msg

        if not user_message:
            return

        session = self._sessions.get(session_id)
        if session:
            session.message_count += 1
            session.last_active = time.time()

        self._total_messages += 1

        # 流式回复（加锁防止同一 session 并发写入）
        lock = self._ws_locks.get(session_id)
        if self._engine:
            try:
                if lock:
                    await lock.acquire()
                accumulated = []

                async def _safe_send(data):
                    try:
                        if not ws.closed:
                            await asyncio.wait_for(ws.send_json(data), timeout=5.0)
                        else:
                            logger.warning("WS closed, dropping message type=%s", data.get("type"))
                    except asyncio.TimeoutError:
                        logger.warning("WS send timeout, dropping message type=%s", data.get("type"))
                    except Exception as exc:
                        logger.warning("WS send failed: %s", exc)

                def on_stream(text):
                    accumulated.append(text)
                    asyncio.get_event_loop().create_task(_safe_send({
                        "type": "stream",
                        "content": text,
                    }))

                def on_thinking(text):
                    asyncio.get_event_loop().create_task(_safe_send({
                        "type": "thinking",
                        "content": text,
                    }))

                def on_tool_call(name, args):
                    asyncio.get_event_loop().create_task(_safe_send({
                        "type": "tool_call",
                        "name": name,
                        "args": args,
                    }))
                    asyncio.get_event_loop().create_task(self.broadcast_inspector_event({
                        "event_type": "tool_call",
                        "title": f"Tool: {name}",
                        "detail": str(args)[:200] if args else "",
                        "timestamp": time.time(),
                    }))

                def on_tool_result(name, result):
                    result = str(result) if result is not None else ""
                    asyncio.get_event_loop().create_task(_safe_send({
                        "type": "tool_result",
                        "name": name,
                        "result": result[:500],
                    }))
                    asyncio.get_event_loop().create_task(self.broadcast_inspector_event({
                        "event_type": "tool_result",
                        "title": f"Result: {name}",
                        "detail": result[:200] if result else "",
                        "timestamp": time.time(),
                    }))
                    # Activity 追踪 — 文件操作记录
                    _FILE_TOOLS = {"read_file": "read", "write_file": "write", "edit_file": "edit", "list_directory": "list", "file_read": "read", "file_write": "write", "file_edit": "edit"}
                    if name in _FILE_TOOLS and self._get_pin_manager():
                        action = _FILE_TOOLS[name]
                        # 从 result 提取路径 (通常第一行或 JSON)
                        path_hint = (result or "")[:200].split("\n")[0]
                        asyncio.get_event_loop().create_task(self._get_pin_manager().record_activity(
                            path=path_hint[:100], action=action, tool_name=name, summary=(result or "")[:100],
                        ))
                        asyncio.get_event_loop().create_task(_safe_send({
                            "type": "context_activity",
                            "path": path_hint[:100], "action": action, "tool_name": name,
                            "timestamp": time.time(),
                        }))
                    # Canvas 渲染推送
                    if result and '"__canvas_render__"' in result:
                        try:
                            import json as _json
                            canvas_data = _json.loads(result)
                            if canvas_data.get("__canvas_render__"):
                                asyncio.get_event_loop().create_task(_safe_send({
                                    "type": "canvas_render",
                                    "component": {
                                        "type": canvas_data.get("type", "html"),
                                        "title": canvas_data.get("title", ""),
                                        "content": canvas_data.get("content", ""),
                                        "artifact_id": canvas_data.get("artifact_id", ""),
                                    },
                                }))
                        except Exception:
                            logger.debug("Canvas render parse failed", exc_info=True)

                def on_llm_event(phase, info):
                    if phase == "request":
                        asyncio.get_event_loop().create_task(self.broadcast_inspector_event({
                            "event_type": "llm_request",
                            "title": "LLM Request",
                            "detail": f"round={info.get('round', 0)}, msgs={info.get('messages_count', 0)}, tools={'yes' if info.get('has_tools') else 'no'}",
                            "timestamp": time.time(),
                        }))
                    elif phase == "response":
                        asyncio.get_event_loop().create_task(self.broadcast_inspector_event({
                            "event_type": "llm_response",
                            "title": "LLM Response",
                            "detail": f"tokens={info.get('prompt_tokens', 0)}+{info.get('completion_tokens', 0)}, tool_calls={'yes' if info.get('has_tool_calls') else 'no'}",
                            "timestamp": time.time(),
                            "duration_ms": info.get("duration_ms", 0),
                        }))

                # message_in 事件
                asyncio.get_event_loop().create_task(self.broadcast_inspector_event({
                    "event_type": "message_in",
                    "title": "Message In",
                    "detail": user_message[:100],
                    "timestamp": time.time(),
                }))

                # 构建持久化 session 的消息历史，用 ContextEngine 智能压缩
                gw_session = self._ws_sessions.get(session_id)
                session_msgs = None
                if gw_session:
                    from agent.providers.base import Message as _Msg
                    from agent.context_engine.manager import ContextEngine
                    all_msgs = [
                        _Msg(role=m["role"], content=m["content"])
                        for m in gw_session.messages
                    ]
                    ctx_engine = ContextEngine(max_context_tokens=self._engine._max_context_tokens)
                    if ctx_engine.should_auto_compact(all_msgs):
                        try:
                            all_msgs = await ctx_engine.manage(all_msgs, self._engine._router)
                        except Exception as _ce:
                            logger.warning("Context auto-compact failed: %s", _ce)
                            all_msgs = all_msgs[-50:]
                    session_msgs = all_msgs

                result = await asyncio.wait_for(
                    self._engine.run_turn(
                        user_message,
                        on_stream=on_stream,
                        on_thinking=on_thinking,
                        on_tool_call=on_tool_call,
                        on_tool_result=on_tool_result,
                        on_llm_event=on_llm_event,
                        session_messages=session_msgs,
                        abort_check=lambda: ws.closed,
                    ),
                    timeout=300.0,  # 5 分钟超时保护
                )

                # 持久化本轮消息
                if gw_session:
                    gw_session.add_message("user", user_message)
                    gw_session.add_message("assistant", result.content or "")
                    gw_session.tool_calls_count += result.tool_calls_made
                    gw_session.total_tokens += result.total_usage.total_tokens
                    await self._session_mgr._persist_session(gw_session)

                await _safe_send({
                    "type": "complete",
                    "content": result.content,
                    "thinking": result.thinking or "",
                    "tool_calls": result.tool_calls_made,
                    "tokens": result.total_usage.total_tokens,
                    "duration_ms": round(result.duration_ms),
                })

                # message_out 事件
                asyncio.get_event_loop().create_task(self.broadcast_inspector_event({
                    "event_type": "message_out",
                    "title": "Message Out",
                    "detail": f"tools={result.tool_calls_made}, tokens={result.total_usage.total_tokens}, {round(result.duration_ms)}ms",
                    "timestamp": time.time(),
                    "duration_ms": round(result.duration_ms),
                }))

                # 自动检测回复中的 HTML 代码块，推送到 Canvas
                import re as _re
                _html_blocks = _re.findall(r'```html\s*\n([\s\S]*?)```', result.content or '')
                for i, html_block in enumerate(_html_blocks):
                    html_block = html_block.strip()
                    if len(html_block) > 100:  # 忽略太短的片段
                        from agent.core.canvas import wrap_canvas_html
                        render = html_block if '<html' in html_block.lower()[:200] else wrap_canvas_html("Preview", html_block)
                        await _safe_send({
                            "type": "canvas_render",
                            "component": {
                                "type": "html",
                                "title": f"HTML Preview {i+1}",
                                "content": render,
                            },
                        })

            except asyncio.CancelledError:
                logger.info("run_turn cancelled for session %s (WS disconnected)", session_id)
            except TimeoutError:
                logger.warning("run_turn timeout for session %s (>300s)", session_id)
                err_msg = "AI 响应超时，请稍后重试。如果问题持续出现，可能是上游模型服务不稳定。"
                try:
                    if not ws.closed:
                        await asyncio.wait_for(ws.send_json({"type": "error", "message": err_msg}), timeout=5.0)
                        await asyncio.wait_for(ws.send_json({"type": "complete", "content": err_msg, "thinking": "", "tool_calls": 0, "tokens": 0, "duration_ms": 0}), timeout=5.0)
                except Exception:
                    pass
            except Exception as e:
                logger.error("run_turn failed for session %s: %s", session_id, e, exc_info=True)
                asyncio.get_event_loop().create_task(self.broadcast_inspector_event({
                    "event_type": "error",
                    "title": "Error",
                    "detail": str(e)[:200],
                    "timestamp": time.time(),
                }))
                err_msg = f"处理出错: {e}"
                try:
                    if not ws.closed:
                        await asyncio.wait_for(ws.send_json({"type": "error", "message": err_msg}), timeout=5.0)
                except Exception:
                    pass
            finally:
                if lock and lock.locked():
                    lock.release()
        else:
            try:
                if not ws.closed:
                    await ws.send_json({
                        "type": "complete",
                        "content": "Agent 未初始化。",
                    })
            except Exception:
                pass

    # ─── REST API ───

    async def _api_chat(self, request):
        from aiohttp import web
        self._total_requests += 1

        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "invalid JSON body"}, status=400)
        message = body.get("message", "")

        if not message:
            return web.json_response({"error": "message required"}, status=400)

        if not self._engine:
            return web.json_response({"error": "engine not initialized"}, status=500)

        try:
            result = await self._engine.run_turn(message)
        except Exception as e:
            logger.error("Engine error: %s", e, exc_info=True)
            return web.json_response({"error": f"Engine error: {e}"}, status=500)
        return web.json_response({
            "content": result.content,
            "tool_calls": result.tool_calls_made,
            "tokens": result.total_usage.total_tokens,
            "duration_ms": round(result.duration_ms),
        })

    async def _api_sessions(self, request):
        from aiohttp import web
        return web.json_response({
            "sessions": [
                {
                    "session_id": s.session_id,
                    "user_id": s.user_id,
                    "messages": s.message_count,
                    "connected_at": s.connected_at,
                }
                for s in self._sessions.values()
            ]
        })

    async def _api_reset(self, request):
        from aiohttp import web
        if self._engine:
            self._engine.reset()
        return web.json_response({"status": "ok"})

    # ─── Admin API ───

    async def _admin_stats(self, request):
        from aiohttp import web
        user, err = self._require_admin(request)
        if err:
            return err
        uptime = time.time() - self._start_time
        return web.json_response({
            "uptime_seconds": round(uptime),
            "total_messages": self._total_messages,
            "total_requests": self._total_requests,
            "active_ws": len(self._ws_connections),
            "total_sessions": len(self._sessions),
        })

    async def _admin_models(self, request):
        from aiohttp import web
        user, err = self._require_admin(request)
        if err:
            return err
        if self._engine and hasattr(self._engine, '_router'):
            router = self._engine._router
            providers = list(router._providers.keys()) if hasattr(router, '_providers') else []
            return web.json_response({
                "primary": {
                    "provider": getattr(router, '_primary_provider', '') or '',
                    "model": getattr(router, '_primary_model', '') or '',
                    "base_url": self._global_config.model.primary.base_url if self._global_config else '',
                },
                "cheap": {
                    "provider": getattr(router, '_cheap_provider', '') or '',
                    "model": getattr(router, '_cheap_model', '') or '',
                },
                "cheap_routing": getattr(router, '_cheap_routing_enabled', False),
                "failover": [
                    {"provider": p, "model": m}
                    for p, m in getattr(router, '_failover_chain', [])
                ],
                "providers": providers,
            })
        return web.json_response({"error": "no router", "providers": []})

    async def _admin_set_model(self, request):
        """POST /api/admin/models — 设置模型配置 (provider/model/api_key/base_url)."""
        from aiohttp import web
        user, err = self._require_admin(request)
        if err:
            return err

        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)
        provider_name = body.get("provider", "").strip()
        model_name = body.get("model", "").strip()
        api_key = body.get("api_key", "").strip()
        base_url = body.get("base_url", "").strip()

        if not provider_name or not model_name:
            return web.json_response({"error": "provider and model required"}, status=400)

        # 更新 config
        if self._global_config:
            self._global_config.model.primary.provider = provider_name
            self._global_config.model.primary.model = model_name
            if api_key:
                self._global_config.model.primary.api_key = api_key
            if base_url:
                self._global_config.model.primary.base_url = base_url
            self._save_config()

        # 热更新 router: 注册/替换 provider + 设置 primary
        if self._engine and hasattr(self._engine, '_router'):
            router = self._engine._router
            try:
                from agent.providers.openai_provider import OpenAIProvider
                from agent.providers.base import ProviderType
                effective_key = api_key or (self._global_config.model.primary.api_key if self._global_config else "")
                if effective_key:
                    new_provider = OpenAIProvider(
                        provider_type=ProviderType(provider_name),
                        api_key=effective_key,
                        base_url=base_url or None,
                    )
                    router.register_provider(new_provider)
                    router.set_primary(provider_name, model_name)
                else:
                    return web.json_response({"error": "API key required"}, status=400)
            except Exception as e:
                return web.json_response({"error": str(e)}, status=500)

        admin_name = user.username if user else "anonymous"
        self._audit(admin_name, "MODEL_SET", f"{provider_name}:{model_name}", request.remote or "")
        return web.json_response({"status": "ok", "provider": provider_name, "model": model_name})

    async def _admin_tools(self, request):
        from aiohttp import web
        user, err = self._require_admin(request)
        if err:
            return err
        if self._engine:
            tools = [
                {"name": t.definition.name, "description": t.definition.description}
                for t in self._engine._tools.values()
            ]
            return web.json_response({"tools": tools, "count": len(tools)})
        return web.json_response({"tools": [], "count": 0})

    async def _admin_memory(self, request):
        from aiohttp import web
        user, err = self._require_admin(request)
        if err:
            return err
        if self._engine and hasattr(self._engine, '_memory_manager') and self._engine._memory_manager:
            stats = await self._engine._memory_manager.get_stats()
            return web.json_response(stats)
        return web.json_response({"total_memories": 0})

    async def _admin_sessions(self, request):
        from aiohttp import web
        user, err = self._require_admin(request)
        if err:
            return err
        return web.json_response({
            "sessions": [
                {
                    "id": s.session_id,
                    "user": s.user_id,
                    "messages": s.message_count,
                    "last_active": s.last_active,
                }
                for s in self._sessions.values()
            ]
        })

    async def _admin_update_config(self, request):
        from aiohttp import web

        user, err = self._require_admin(request)
        if err:
            return err

        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)
        updated = []

        if self._engine and hasattr(self._engine, '_router'):
            router = self._engine._router

            # 切换主模型
            prov = body.get("primary_provider")
            model = body.get("primary_model")
            if prov and model:
                router.set_primary(prov, model)
                updated.extend(["primary_provider", "primary_model"])
                logger.info("Model switched: primary → %s:%s", prov, model)

            # 切换便宜模型
            cheap_prov = body.get("cheap_provider")
            cheap_model = body.get("cheap_model")
            if cheap_prov and cheap_model:
                router.set_cheap(cheap_prov, cheap_model)
                updated.extend(["cheap_provider", "cheap_model"])

        admin_name = user.username if user else "anonymous"
        self._audit(admin_name, "CONFIG_UPDATE", str(updated), request.remote or "")
        return web.json_response({"status": "ok", "updated": updated})

    async def _admin_get_system_prompt(self, request):
        from aiohttp import web
        prompt = ""
        if self._engine:
            prompt = getattr(self._engine, '_system_prompt', '') or ''
        return web.json_response({"prompt": prompt})

    async def _admin_set_system_prompt(self, request):
        from aiohttp import web

        user, err = self._require_admin(request)
        if err:
            return err

        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)
        prompt = body.get("prompt", "")
        if self._engine:
            self._engine._system_prompt = prompt
            admin_name = user.username if user else "anonymous"
            self._audit(admin_name, "SYSTEM_PROMPT_UPDATE", f"len={len(prompt)}", request.remote or "")
            return web.json_response({"status": "ok"})
        return web.json_response({"error": "engine not initialized"}, status=500)

    async def _admin_audit_log(self, request):
        """GET /api/admin/audit — 审计日志 (支持分页)."""
        from aiohttp import web

        user, err = self._require_admin(request)
        if err:
            return err

        try:
            limit = min(int(request.query.get("limit", "50")), 200)
            offset = int(request.query.get("offset", "0"))
        except (ValueError, TypeError):
            return web.json_response({"error": "invalid limit/offset"}, status=400)
        entries = list(self._audit_log)
        entries.reverse()  # 最新在前
        page = entries[offset:offset + limit]
        return web.json_response({
            "total": len(entries),
            "offset": offset,
            "limit": limit,
            "entries": [
                {
                    "timestamp": e.timestamp,
                    "user": e.user,
                    "action": e.action,
                    "detail": e.detail,
                    "ip": e.ip,
                }
                for e in page
            ],
        })

    async def _admin_inspector_events(self, request):
        """GET /api/admin/inspector/events — Inspector 历史事件."""
        from aiohttp import web

        user, err = self._require_admin(request)
        if err:
            return err

        try:
            limit = min(int(request.query.get("limit", "100")), 500)
        except (ValueError, TypeError):
            limit = 100
        event_type = request.query.get("type", "")
        events = []
        if self._inspector_log_path:
            try:
                with open(self._inspector_log_path, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                for line in reversed(lines):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        evt = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if event_type and evt.get("event_type") != event_type:
                        continue
                    events.append(evt)
                    if len(events) >= limit:
                        break
            except FileNotFoundError:
                pass
        return web.json_response({"events": events, "count": len(events)})

    async def _admin_list_users(self, request):
        """GET /api/admin/users — 用户列表."""
        from aiohttp import web

        user, err = self._require_admin(request)
        if err:
            return err

        if not self._auth:
            return web.json_response({"users": [], "count": 0})

        users = [
            {
                "user_id": u.user_id,
                "username": u.username,
                "role": u.role.value,
                "active": u.active,
                "created_at": u.created_at,
                "last_login": u.last_login,
            }
            for u in self._auth._users.values()
        ]
        return web.json_response({"users": users, "count": len(users)})

    async def _admin_create_user(self, request):
        """POST /api/admin/users — 创建用户 (同 register)."""
        return await self._auth_register(request)

    # ─── Terminal Exec ───

    async def _handle_terminal_exec(self, command: str, ws) -> None:
        """通过 WebSocket 执行终端命令 (沙箱)."""
        import shlex

        if not command.strip():
            return

        # 命令白名单 — 只允许安全的只读/开发命令
        ALLOWED_BINS = {
            "ls", "cat", "head", "tail", "grep", "find", "wc", "sort", "diff",
            "pwd", "env", "which", "echo", "date", "whoami", "file", "stat",
            "python", "python3", "pip", "pip3", "git", "node", "npm", "npx",
        }

        try:
            parts = shlex.split(command)
        except ValueError as e:
            await ws.send_json({"type": "terminal_complete", "content": f"Invalid command: {e}", "exit_code": 1})
            return

        if not parts:
            return

        # 提取基础命令名 (去掉路径前缀)
        import os
        bin_name = os.path.basename(parts[0])
        if bin_name not in ALLOWED_BINS:
            await ws.send_json({"type": "terminal_complete", "content": f"Command not allowed: {bin_name}", "exit_code": 1})
            return

        try:
            proc = await asyncio.create_subprocess_exec(
                *parts,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=self._workspace_dir,
            )
            output = []
            try:
                while True:
                    line = await asyncio.wait_for(proc.stdout.readline(), timeout=30.0)
                    if not line:
                        break
                    text = line.decode("utf-8", errors="replace")
                    output.append(text)
                    await ws.send_json({"type": "terminal_output", "content": text})
            except asyncio.TimeoutError:
                proc.kill()
                await ws.send_json({"type": "terminal_complete", "content": "Command timed out (30s)", "exit_code": -1})
                return

            await asyncio.wait_for(proc.wait(), timeout=5.0)
            await ws.send_json({
                "type": "terminal_complete",
                "content": "".join(output),
                "exit_code": proc.returncode,
            })
        except asyncio.TimeoutError:
            await ws.send_json({"type": "terminal_complete", "content": "Command timed out", "exit_code": -1})
        except Exception as e:
            await ws.send_json({"type": "terminal_complete", "content": str(e), "exit_code": 1})

    # ─── Inspector broadcast ───

    async def broadcast_inspector_event(self, event: dict) -> None:
        """向所有 inspector 订阅者推送事件 + JSONL 持久化."""
        if self._inspector_log_path:
            try:
                async with self._inspector_write_lock:
                    with open(self._inspector_log_path, "a", encoding="utf-8") as f:
                        f.write(json.dumps(event, ensure_ascii=False) + "\n")
            except Exception:
                logger.debug("Inspector JSONL write failed", exc_info=True)
        dead = []
        for sid in self._inspector_subs:
            ws = self._ws_connections.get(sid)
            if ws and not ws.closed:
                try:
                    await asyncio.wait_for(ws.send_json({"type": "inspector_event", **event}), timeout=5.0)
                except asyncio.TimeoutError:
                    logger.warning("Inspector WS send timeout for session %s, removing", sid)
                    dead.append(sid)
                except Exception:
                    dead.append(sid)
            else:
                dead.append(sid)
        for sid in dead:
            self._inspector_subs.discard(sid)

    async def broadcast_canvas(self, canvas_data: dict, source_platform: str = "", sender: str = "") -> None:
        """广播 Canvas 渲染到所有 WebUI 客户端 (跨平台 canvas 推送)."""
        msg = {
            "type": "canvas_render",
            "component": {
                "type": canvas_data.get("type", "html"),
                "title": canvas_data.get("title", ""),
                "content": canvas_data.get("content", ""),
                "artifact_id": canvas_data.get("artifact_id", ""),
            },
            "source_platform": source_platform,
            "sender": sender,
        }
        for sid, ws in list(self._ws_connections.items()):
            if ws and not ws.closed:
                try:
                    await asyncio.wait_for(ws.send_json(msg), timeout=5.0)
                except Exception:
                    logger.debug("Canvas broadcast to %s failed", sid, exc_info=True)

    async def _canvas_list(self, request):
        """GET /api/workspace/canvas/list — 列出持久化的 canvas."""
        from aiohttp import web as _web
        try:
            from agent.tools.canvas_tools import _canvas_mgr
            store = getattr(_canvas_mgr, '_store', None)
            if not store:
                return _web.json_response({"items": []})
            items = store.list_artifacts() or []
            return _web.json_response({"items": items})
        except Exception as e:
            return _web.json_response({"items": [], "error": str(e)})

    async def _canvas_get(self, request):
        """GET /api/workspace/canvas/{artifact_id} — 获取 canvas 渲染内容."""
        from aiohttp import web as _web
        artifact_id = request.match_info.get("artifact_id", "")
        try:
            from agent.tools.canvas_tools import _canvas_mgr
            artifact = _canvas_mgr.get(artifact_id)
            if not artifact:
                return _web.json_response({"error": "not found"}, status=404)
            html = _canvas_mgr.render_html(artifact_id)
            return _web.json_response({
                "artifact_id": artifact.artifact_id,
                "type": artifact.canvas_type.value,
                "title": artifact.title,
                "content": html or artifact.content,
            })
        except Exception as e:
            return _web.json_response({"error": str(e)}, status=500)

    # ─── Gateway Admin API ───

    def _save_config(self) -> None:
        """持久化当前配置到 config.yaml."""
        if self._global_config:
            self._global_config.save()

    async def _gw_schemas(self, request):
        """GET /api/admin/gateway/schemas — 平台配置字段定义."""
        from aiohttp import web
        user, err = self._require_admin(request)
        if err:
            return err
        from gateway.platforms.schemas import PLATFORM_SCHEMAS
        return web.json_response(PLATFORM_SCHEMAS)

    async def _gw_list_channels(self, request):
        """GET /api/admin/gateway/channels — 列出所有渠道 + 状态."""
        from aiohttp import web
        user, err = self._require_admin(request)
        if err:
            return err

        channels = {}
        config_channels = self._global_config.channels if self._global_config else {}

        for platform, cfg in config_channels.items():
            adapter = self._gateway._adapters.get(platform) if self._gateway else None
            channels[platform] = {
                "config": {k: ("***" if "secret" in k or "token" in k or "key" in k else v) for k, v in cfg.items()},
                "running": adapter.is_running if adapter else False,
                "capabilities": adapter.capabilities if adapter else {},
                "bot_user": adapter.bot_user.username if adapter and adapter.bot_user else None,
                "login_state": adapter.login_state if adapter and hasattr(adapter, "login_state") else None,
            }

        return web.json_response({"channels": channels})

    async def _gw_save_channel(self, request):
        """POST /api/admin/gateway/channels — 添加/更新渠道配置."""
        from aiohttp import web
        user, err = self._require_admin(request)
        if err:
            return err

        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)
        platform = body.get("platform", "")
        config_data = body.get("config", {})

        if not platform or not config_data:
            return web.json_response({"error": "platform and config required"}, status=400)

        # 校验平台合法性 + 过滤未知字段
        from gateway.platforms.schemas import PLATFORM_SCHEMAS
        if platform not in PLATFORM_SCHEMAS:
            return web.json_response({"error": f"Unknown platform: {platform}"}, status=400)
        allowed_keys = {f["key"] for f in PLATFORM_SCHEMAS[platform]["fields"]}
        config_data = {k: v for k, v in config_data.items() if k in allowed_keys}

        if self._global_config:
            self._global_config.channels[platform] = config_data
            self._save_config()

        admin_name = user.username if user else "anonymous"
        self._audit(admin_name, "CHANNEL_SAVE", platform, request.remote or "")
        return web.json_response({"status": "ok", "platform": platform})

    async def _gw_delete_channel(self, request):
        """DELETE /api/admin/gateway/channels/{platform}."""
        from aiohttp import web
        user, err = self._require_admin(request)
        if err:
            return err

        platform = request.match_info["platform"]

        # 先停止适配器
        if self._gateway:
            await self._gateway.remove_adapter_runtime(platform)

        if self._global_config and platform in self._global_config.channels:
            del self._global_config.channels[platform]
            self._save_config()

        admin_name = user.username if user else "anonymous"
        self._audit(admin_name, "CHANNEL_DELETE", platform, request.remote or "")
        return web.json_response({"status": "ok"})

    async def _gw_start_channel(self, request):
        """POST /api/admin/gateway/channels/{platform}/start."""
        from aiohttp import web
        user, err = self._require_admin(request)
        if err:
            return err

        platform = request.match_info["platform"]

        if not self._gateway:
            return web.json_response({"error": "Gateway not initialized"}, status=500)

        config_data = (self._global_config.channels or {}).get(platform)
        if not config_data:
            return web.json_response({"error": f"No config for {platform}"}, status=404)

        result = await self._gateway.add_adapter_runtime(platform, config_data)

        admin_name = user.username if user else "anonymous"
        self._audit(admin_name, "CHANNEL_START", f"{platform}: {result}", request.remote or "")

        if result == "ok":
            return web.json_response({"status": "ok"})
        return web.json_response({"error": result}, status=500)

    async def _gw_stop_channel(self, request):
        """POST /api/admin/gateway/channels/{platform}/stop."""
        from aiohttp import web
        user, err = self._require_admin(request)
        if err:
            return err

        platform = request.match_info["platform"]
        if self._gateway:
            await self._gateway.remove_adapter_runtime(platform)

        admin_name = user.username if user else "anonymous"
        self._audit(admin_name, "CHANNEL_STOP", platform, request.remote or "")
        return web.json_response({"status": "ok"})

    async def _gw_channel_login_state(self, request):
        """GET /api/admin/gateway/channels/{platform}/login-state — QR 扫码登录状态."""
        from aiohttp import web
        user, err = self._require_admin(request)
        if err:
            return err
        platform = request.match_info["platform"]
        if not self._gateway:
            return web.json_response({"error": "Gateway not initialized"}, status=500)
        adapter = self._gateway._adapters.get(platform)
        if not adapter or not hasattr(adapter, "login_state"):
            return web.json_response({"error": "Not supported"}, status=404)
        return web.json_response(adapter.login_state)

    async def _gw_channel_contacts(self, request):
        """GET /api/admin/gateway/channels/{platform}/contacts — 已知联系人列表."""
        from aiohttp import web
        user, err = self._require_admin(request)
        if err:
            return err
        platform = request.match_info["platform"]
        if not self._gateway:
            return web.json_response({"error": "Gateway not initialized"}, status=500)
        adapter = self._gateway._adapters.get(platform)
        if not adapter or not hasattr(adapter, "list_known_contacts"):
            return web.json_response({"error": "Not supported"}, status=404)
        contacts = adapter.list_known_contacts()
        return web.json_response({"contacts": contacts})

    async def _gw_channel_send(self, request):
        """POST /api/admin/gateway/channels/{platform}/send — 主动发消息."""
        from aiohttp import web
        user, err = self._require_admin(request)
        if err:
            return err
        platform = request.match_info["platform"]
        if not self._gateway:
            return web.json_response({"error": "Gateway not initialized"}, status=500)
        adapter = self._gateway._adapters.get(platform)
        if not adapter:
            return web.json_response({"error": "Adapter not found"}, status=404)
        if not adapter.is_running:
            return web.json_response({"error": "Adapter not running"}, status=400)
        body = await request.json()
        chat_id = body.get("chat_id", "")
        text = body.get("text", "")
        if not chat_id or not text:
            return web.json_response({"error": "chat_id and text required"}, status=400)
        if hasattr(adapter, "send_to_contact"):
            msg_id = await adapter.send_to_contact(chat_id, text)
        else:
            msg_id = await adapter.send_text(chat_id, text)
        return web.json_response({"message_id": msg_id, "ok": bool(msg_id)})

    async def _gw_get_voice(self, request):
        """GET /api/admin/gateway/voice."""
        from aiohttp import web
        user, err = self._require_admin(request)
        if err:
            return err

        vc = self._global_config.voice if self._global_config else None
        if not vc:
            return web.json_response({"voice": {}})

        return web.json_response({"voice": {
            "enabled": vc.enabled,
            "stt_provider": vc.stt_provider,
            "stt_model": vc.stt_model,
            "stt_language": vc.stt_language,
            "stt_api_key": vc.stt_api_key,
            "stt_base_url": vc.stt_base_url,
            "tts_provider": vc.tts_provider,
            "tts_voice": vc.tts_voice,
            "tts_speed": vc.tts_speed,
            "tts_api_key": vc.tts_api_key,
            "tts_base_url": vc.tts_base_url,
            "tts_output_format": vc.tts_output_format,
        }})

    async def _gw_save_voice(self, request):
        """POST /api/admin/gateway/voice."""
        from aiohttp import web
        user, err = self._require_admin(request)
        if err:
            return err

        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)
        if self._global_config:
            vc = self._global_config.voice
            for key in ("enabled", "stt_provider", "stt_model", "stt_language",
                        "stt_api_key", "stt_base_url",
                        "tts_provider", "tts_voice", "tts_speed",
                        "tts_api_key", "tts_base_url", "tts_output_format"):
                if key in body:
                    setattr(vc, key, body[key])
            self._save_config()

        admin_name = user.username if user else "anonymous"
        self._audit(admin_name, "VOICE_CONFIG_UPDATE", str(list(body.keys())), request.remote or "")
        return web.json_response({"status": "ok"})

    async def _gw_test_voice(self, request):
        """POST /api/admin/gateway/voice/test — TTS 试听."""
        from aiohttp import web
        user, err = self._require_admin(request)
        if err:
            return err

        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        text = body.get("text", "你好，这是一段语音测试。")
        vc = self._global_config.voice if self._global_config else None
        if not vc:
            return web.json_response({"error": "Voice not configured"}, status=400)

        try:
            from gateway.voice.pipeline import VoicePipeline, VoiceConfig as PipelineVoiceConfig, TTSProvider

            # 将字符串转为 TTSProvider 枚举
            try:
                tts_prov = TTSProvider(vc.tts_provider)
            except ValueError:
                tts_prov = TTSProvider.EDGE_TTS

            # 缓存 pipeline，配置变更时重建
            cache_key = f"{vc.tts_provider}:{vc.tts_voice}:{vc.tts_speed}:{getattr(vc, 'tts_api_key', '')}:{getattr(vc, 'tts_base_url', '')}"
            if self._tts_pipeline is None or self._tts_pipeline_key != cache_key:
                pipe_config = PipelineVoiceConfig(
                    tts_provider=tts_prov,
                    tts_voice=vc.tts_voice,
                    tts_speed=vc.tts_speed,
                    tts_api_key=vc.tts_api_key,
                    tts_base_url=getattr(vc, "tts_base_url", ""),
                )
                self._tts_pipeline = VoicePipeline(pipe_config)
                await self._tts_pipeline.initialize()
                self._tts_pipeline_key = cache_key

            result = await self._tts_pipeline.text_to_speech(text)

            if not result.audio_data:
                return web.json_response({"error": "TTS returned empty audio"}, status=500)

            import base64
            audio_b64 = base64.b64encode(result.audio_data).decode()
            return web.json_response({
                "audio": audio_b64,
                "format": result.format or "mp3",
                "size": len(result.audio_data),
            })
        except Exception as e:
            logger.error("Voice test failed: %s", e, exc_info=True)
            return web.json_response({"error": str(e)}, status=500)

    async def _gw_get_ecommerce(self, request):
        """GET /api/admin/gateway/ecommerce."""
        from aiohttp import web
        user, err = self._require_admin(request)
        if err:
            return err

        ecommerce_mode = False
        if self._gateway:
            ecommerce_mode = getattr(self._gateway, '_ecommerce_mode', False)

        return web.json_response({"ecommerce_mode": ecommerce_mode})

    async def _gw_save_ecommerce(self, request):
        """POST /api/admin/gateway/ecommerce."""
        from aiohttp import web
        user, err = self._require_admin(request)
        if err:
            return err

        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)
        enabled = body.get("enabled", False)

        if self._gateway:
            self._gateway._ecommerce_mode = enabled
            if enabled and not self._gateway._ecommerce_coordinator:
                self._gateway._init_ecommerce_coordinator()

        admin_name = user.username if user else "anonymous"
        self._audit(admin_name, "ECOMMERCE_TOGGLE", str(enabled), request.remote or "")
        return web.json_response({"status": "ok", "ecommerce_mode": enabled})

    async def _gw_get_calabash(self, request):
        """GET /api/admin/gateway/calabash — 兼容旧端点，改用 SecretsStore."""
        from aiohttp import web
        _, err = self._require_admin(request)
        if err:
            return err

        from agent.core.secrets import get_secrets_store
        store = get_secrets_store()
        vals = store.get_all("ecommerce-image-pipeline")
        return web.json_response({
            "api_url": vals.get("CALABASH_API_URL", "https://ai.allinxjd.com"),
            "phone": vals.get("CALABASH_PHONE", ""),
            "has_password": bool(vals.get("CALABASH_PASSWORD")),
        })

    async def _gw_save_calabash(self, request):
        """POST /api/admin/gateway/calabash — 兼容旧端点，改用 SecretsStore."""
        from aiohttp import web
        _, err = self._require_admin(request)
        if err:
            return err

        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        from agent.core.secrets import get_secrets_store
        store = get_secrets_store()
        updates: dict[str, str] = {}
        if "api_url" in body:
            updates["CALABASH_API_URL"] = body["api_url"]
        if "phone" in body:
            updates["CALABASH_PHONE"] = body["phone"]
        if "password" in body and body["password"]:
            updates["CALABASH_PASSWORD"] = body["password"]
        if updates:
            store.set_bulk("ecommerce-image-pipeline", updates)
        return web.json_response({"status": "ok"})

    async def _gw_cron_list(self, request):
        """GET /api/admin/gateway/cron/tasks — 列出所有定时任务."""
        from aiohttp import web
        _, err = self._require_admin(request)
        if err:
            return err
        gw = self._gateway
        if not gw or not getattr(gw, '_scheduler', None):
            return web.json_response({"tasks": []})
        tasks = await gw._scheduler.list_tasks()
        return web.json_response({"tasks": [t.to_dict() for t in tasks]})

    async def _gw_cron_run(self, request):
        """POST /api/admin/gateway/cron/tasks/{task_id}/run — 手动触发定时任务."""
        from aiohttp import web
        _, err = self._require_admin(request)
        if err:
            return err
        task_id = request.match_info["task_id"]
        gw = self._gateway
        if not gw or not getattr(gw, '_scheduler', None):
            return web.json_response({"error": "scheduler not available"}, status=503)
        ok = await gw._scheduler.run_task_now(task_id)
        if ok:
            return web.json_response({"status": "triggered", "task_id": task_id})
        return web.json_response({"error": "task not found or already running"}, status=404)

    async def _skill_secrets_list(self, request):
        """GET /api/admin/skill-secrets — 列出所有有 secrets 声明的技能."""
        from aiohttp import web
        _, err = self._require_admin(request)
        if err:
            return err

        from agent.core.secrets import get_secrets_store
        store = get_secrets_store()
        skills = []
        if self._engine and hasattr(self._engine, "_skill_manager") and self._engine._skill_manager:
            for s in self._engine._skill_manager._skills.values():
                if not s.secrets:
                    continue
                vals = store.get_all(s.skill_id)
                configured = sum(1 for sec in s.secrets if vals.get(sec.key) or sec.default)
                skills.append({
                    "skill_id": s.skill_id,
                    "skill_name": s.name,
                    "total_secrets": len(s.secrets),
                    "configured": configured,
                })
        return web.json_response({"skills": skills})

    async def _skill_secrets_get(self, request):
        """GET /api/admin/skill-secrets/{skill_id} — 获取技能凭证详情."""
        from aiohttp import web
        _, err = self._require_admin(request)
        if err:
            return err

        skill_id = request.match_info["skill_id"]
        skill = None
        if self._engine and hasattr(self._engine, "_skill_manager") and self._engine._skill_manager:
            skill = self._engine._skill_manager._skills.get(skill_id)
        if not skill:
            return web.json_response({"error": "Skill not found"}, status=404)

        from agent.core.secrets import get_secrets_store
        store = get_secrets_store()
        vals = store.get_all(skill_id)
        secrets_out = []
        for sec in skill.secrets:
            val = vals.get(sec.key, "")
            is_sensitive = "password" in sec.key.lower() or "secret" in sec.key.lower() or "token" in sec.key.lower()
            entry: dict = {
                "key": sec.key,
                "description": sec.description,
                "has_value": bool(val or sec.default),
            }
            if sec.default:
                entry["default"] = sec.default
            if val and not is_sensitive:
                entry["value"] = val
            secrets_out.append(entry)
        return web.json_response({
            "skill_id": skill_id,
            "skill_name": skill.name,
            "secrets": secrets_out,
        })

    async def _skill_secrets_save(self, request):
        """POST /api/admin/skill-secrets/{skill_id} — 保存技能凭证."""
        from aiohttp import web
        _, err = self._require_admin(request)
        if err:
            return err

        skill_id = request.match_info["skill_id"]

        skill = None
        if self._engine and hasattr(self._engine, "_skill_manager") and self._engine._skill_manager:
            skill = self._engine._skill_manager._skills.get(skill_id)
        if not skill:
            return web.json_response({"error": "Skill not found"}, status=404)

        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        if not isinstance(body, dict):
            return web.json_response({"error": "Expected JSON object"}, status=400)

        allowed_keys = {s.key for s in skill.secrets}
        from agent.core.secrets import get_secrets_store
        store = get_secrets_store()
        updates = {k: str(v) for k, v in body.items() if v and k in allowed_keys}
        if updates:
            store.set_bulk(skill_id, updates)
        return web.json_response({"status": "ok"})

    # ─── Workspace API ───

    async def _ws_files_list(self, request):
        """GET /api/workspace/files — 列出目录."""
        from aiohttp import web
        import pathlib

        _SKIP_DIRS = {"__pycache__", "node_modules", ".git", ".venv", "venv", ".mypy_cache", ".pytest_cache", ".ruff_cache", "__pypackages__", ".egg-info"}
        _SKIP_EXTS = {".pyc", ".pyo", ".so", ".dylib", ".dll", ".exe", ".o", ".a", ".class", ".jar", ".whl", ".egg"}

        rel_path = request.query.get("path", ".")
        base = pathlib.Path(self._workspace_dir).resolve()
        target = (base / rel_path).resolve()

        # 沙箱: 不能逃出工作目录，拒绝 symlink 穿越
        if not target.is_relative_to(base):
            return web.json_response({"error": "Access denied"}, status=403)
        if target.is_symlink():
            return web.json_response({"error": "Symlinks not allowed"}, status=403)

        if not target.is_dir():
            return web.json_response({"error": "Not a directory"}, status=400)

        entries = []
        try:
            for item in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
                if item.name.startswith("."):
                    continue
                if item.is_dir() and item.name in _SKIP_DIRS:
                    continue
                if item.is_file() and item.suffix in _SKIP_EXTS:
                    continue
                entries.append({
                    "name": item.name,
                    "path": str(item.relative_to(base)),
                    "is_dir": item.is_dir(),
                    "size": item.stat().st_size if item.is_file() else 0,
                    "depth": 0,
                })
        except PermissionError:
            return web.json_response({"error": "Permission denied"}, status=403)

        return web.json_response({"entries": entries, "path": str(target.relative_to(base))})

    async def _ws_file_read(self, request):
        """GET /api/workspace/file — 读取文件内容."""
        from aiohttp import web
        import pathlib

        rel_path = request.query.get("path", "")
        if not rel_path:
            return web.json_response({"error": "path required"}, status=400)

        base = pathlib.Path(self._workspace_dir).resolve()
        target = (base / rel_path).resolve()

        if not target.is_relative_to(base):
            return web.json_response({"error": "Access denied"}, status=403)
        if target.is_symlink():
            return web.json_response({"error": "Symlinks not allowed"}, status=403)
        if not target.is_file():
            return web.json_response({"error": "Not a file"}, status=404)
        if target.stat().st_size > 1_000_000:
            return web.json_response({"error": "File too large (>1MB)"}, status=400)

        # 二进制文件检测
        _BINARY_EXTS = {".pyc", ".pyo", ".so", ".dylib", ".dll", ".exe", ".o", ".a",
                        ".class", ".jar", ".whl", ".egg", ".png", ".jpg", ".jpeg",
                        ".gif", ".ico", ".bmp", ".webp", ".mp3", ".mp4", ".zip",
                        ".gz", ".tar", ".rar", ".7z", ".pdf", ".woff", ".woff2", ".ttf", ".eot"}
        if target.suffix.lower() in _BINARY_EXTS:
            return web.json_response({"error": f"Binary file ({target.suffix}), cannot display"}, status=400)

        try:
            raw = target.read_bytes()
            # 检查是否包含 null bytes (二进制文件特征)
            if b'\x00' in raw[:8192]:
                return web.json_response({"error": "Binary file, cannot display"}, status=400)
            content = raw.decode("utf-8", errors="replace")
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

        return web.json_response({"content": content, "path": rel_path, "size": len(content)})

    # ── Context Pin API ──

    def _get_pin_manager(self):
        return getattr(self, '_pin_manager', None)

    async def _ctx_list_pins(self, request):
        """GET /api/workspace/context/pins"""
        from aiohttp import web
        pm = self._get_pin_manager()
        if not pm:
            return web.json_response({"pins": [], "error": "pin_manager not initialized"})
        pins = await pm.list_pins()
        return web.json_response({"pins": pins})

    async def _ctx_add_pin(self, request):
        """POST /api/workspace/context/pins"""
        from aiohttp import web
        pm = self._get_pin_manager()
        if not pm:
            return web.json_response({"error": "pin_manager not initialized"}, status=500)
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)
        path = data.get("path", "").strip()
        if not path:
            return web.json_response({"error": "path required"}, status=400)
        try:
            result = await pm.add_pin(
                path=path,
                pin_type=data.get("pin_type", "file"),
                label=data.get("label", ""),
                priority=int(data.get("priority", 0)),
                max_lines=int(data.get("max_lines", 200)),
            )
            return web.json_response(result)
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=400)

    async def _ctx_update_pin(self, request):
        """PUT /api/workspace/context/pins/{pin_id}"""
        from aiohttp import web
        pm = self._get_pin_manager()
        if not pm:
            return web.json_response({"error": "pin_manager not initialized"}, status=500)
        pin_id = request.match_info["pin_id"]
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)
        ok = await pm.update_pin(pin_id, **data)
        return web.json_response({"ok": ok})

    async def _ctx_remove_pin(self, request):
        """DELETE /api/workspace/context/pins/{pin_id}"""
        from aiohttp import web
        pm = self._get_pin_manager()
        if not pm:
            return web.json_response({"error": "pin_manager not initialized"}, status=500)
        pin_id = request.match_info["pin_id"]
        ok = await pm.remove_pin(pin_id)
        return web.json_response({"ok": ok})

    async def _ctx_reorder_pins(self, request):
        """POST /api/workspace/context/pins/reorder"""
        from aiohttp import web
        pm = self._get_pin_manager()
        if not pm:
            return web.json_response({"error": "pin_manager not initialized"}, status=500)
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)
        pin_ids = data.get("pin_ids", [])
        if not pin_ids:
            return web.json_response({"error": "pin_ids required"}, status=400)
        ok = await pm.reorder_pins(pin_ids)
        return web.json_response({"ok": ok})

    async def _ctx_activity(self, request):
        """GET /api/workspace/context/activity"""
        from aiohttp import web
        pm = self._get_pin_manager()
        if not pm:
            return web.json_response({"activities": []})
        limit = min(int(request.query.get("limit", "30")), 100)
        activities = await pm.get_recent_activity(limit=limit)
        return web.json_response({"activities": activities})

    async def _ctx_preview(self, request):
        """GET /api/workspace/context/preview — 预览当前注入的上下文."""
        from aiohttp import web
        pm = self._get_pin_manager()
        if not pm:
            return web.json_response({"preview": "", "char_count": 0})
        preview = await pm.get_pinned_context()
        return web.json_response({"preview": preview, "char_count": len(preview)})

    async def _ws_memory_list(self, request):
        """GET /api/workspace/memory/list — 列出记忆."""
        from aiohttp import web

        if not self._engine or not hasattr(self._engine, '_memory_manager') or not self._engine._memory_manager:
            return web.json_response({"memories": []})

        try:
            mgr = self._engine._memory_manager
            await mgr.initialize()
            provider = mgr._provider

            # 直接查 DB 列出最近记忆，不走 FTS5 (空查询 FTS5 会报错)
            memories = []
            if hasattr(provider, '_db') and provider._db:
                cursor = await provider._db.execute(
                    "SELECT * FROM memories ORDER BY updated_at DESC LIMIT 50"
                )
                async for row in cursor:
                    mem = provider._row_to_memory(row)
                    memories.append({
                        "id": mem.memory_id,
                        "content": mem.content[:300],
                        "type": mem.memory_type.value if hasattr(mem.memory_type, 'value') else str(mem.memory_type),
                        "tags": list(mem.tags) if mem.tags else [],
                        "created_at": mem.created_at,
                    })
            return web.json_response({"memories": memories})
        except Exception as e:
            logger.warning("Memory list failed: %s", e)
            return web.json_response({"memories": [], "error": str(e)})

    async def _ws_memory_search(self, request):
        """GET /api/workspace/memory/search — 搜索记忆."""
        from aiohttp import web

        query = request.query.get("q", "")
        if not query:
            return await self._ws_memory_list(request)

        if not self._engine or not hasattr(self._engine, '_memory_manager') or not self._engine._memory_manager:
            return web.json_response({"memories": []})

        try:
            results = await self._engine._memory_manager.recall(query=query, limit=20)
            memories = [
                {
                    "id": r.memory.memory_id,
                    "content": r.memory.content[:300],
                    "type": r.memory.memory_type.value if hasattr(r.memory.memory_type, 'value') else str(r.memory.memory_type),
                    "tags": list(r.memory.tags) if r.memory.tags else [],
                    "relevance": round(r.relevance_score, 2),
                    "match_type": r.match_type,
                }
                for r in results
            ]
            return web.json_response({"memories": memories})
        except Exception as e:
            return web.json_response({"memories": [], "error": str(e)})

    async def _ws_memory_delete(self, request):
        """DELETE /api/workspace/memory/{memory_id} — 删除记忆."""
        from aiohttp import web

        memory_id = request.match_info.get("memory_id", "")
        if not memory_id:
            return web.json_response({"error": "memory_id required"}, status=400)

        if not self._engine or not hasattr(self._engine, '_memory_manager') or not self._engine._memory_manager:
            return web.json_response({"error": "Memory not available"}, status=500)

        try:
            await self._engine._memory_manager._provider.delete(memory_id)
            return web.json_response({"status": "ok"})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def _ws_memory_create(self, request):
        """POST /api/workspace/memory/create — 创建记忆."""
        from aiohttp import web

        if not self._engine or not hasattr(self._engine, '_memory_manager') or not self._engine._memory_manager:
            return web.json_response({"error": "Memory not available"}, status=500)

        try:
            body = await request.json()
            content = body.get("content", "").strip()
            if not content:
                return web.json_response({"error": "content required"}, status=400)

            from agent.memory.provider import MemoryType, MemoryImportance
            try:
                memory_type = MemoryType(body.get("type", "fact"))
                importance = MemoryImportance(body.get("importance", "medium"))
            except ValueError as e:
                return web.json_response({"error": f"Invalid value: {e}"}, status=400)
            tags = body.get("tags", [])

            memory_id = await self._engine._memory_manager.remember(
                content=content,
                memory_type=memory_type,
                importance=importance,
                tags=tags,
            )
            return web.json_response({"status": "ok", "memory_id": memory_id})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def _ws_memory_detail(self, request):
        """GET /api/workspace/memory/detail?id=xxx — 获取单条记忆详情."""
        from aiohttp import web

        memory_id = request.query.get("id", "")
        if not memory_id:
            return web.json_response({"error": "id required"}, status=400)

        if not self._engine or not hasattr(self._engine, '_memory_manager') or not self._engine._memory_manager:
            return web.json_response({"error": "Memory not available"}, status=500)

        try:
            provider = self._engine._memory_manager._provider
            memory = await provider.retrieve(memory_id)
            if not memory:
                return web.json_response({"error": "not found"}, status=404)
            return web.json_response({
                "id": memory.memory_id,
                "content": memory.content,
                "type": memory.memory_type.value if hasattr(memory.memory_type, 'value') else str(memory.memory_type),
                "importance": memory.importance.value if hasattr(memory.importance, 'value') else str(memory.importance),
                "tags": list(memory.tags) if memory.tags else [],
            })
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def _ws_memory_update(self, request):
        """PUT /api/workspace/memory/{memory_id} — 更新记忆."""
        from aiohttp import web

        memory_id = request.match_info.get("memory_id", "")
        if not memory_id:
            return web.json_response({"error": "memory_id required"}, status=400)

        if not self._engine or not hasattr(self._engine, '_memory_manager') or not self._engine._memory_manager:
            return web.json_response({"error": "Memory not available"}, status=500)

        try:
            body = await request.json()
            updates = {}
            if "content" in body:
                updates["content"] = body["content"]
            if "type" in body:
                updates["memory_type"] = body["type"]
            if "importance" in body:
                updates["importance"] = body["importance"]
            if not updates:
                return web.json_response({"error": "nothing to update"}, status=400)

            provider = self._engine._memory_manager._provider
            ok = await provider.update(memory_id, updates)
            if not ok:
                return web.json_response({"error": "update failed"}, status=500)
            # 更新语义索引
            if "content" in updates and hasattr(provider, '_semantic') and provider._semantic:
                try:
                    await provider._semantic.index(memory_id, updates["content"])
                except Exception:
                    pass
            return web.json_response({"status": "ok"})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    # ── Skill Admin API ──────────────────────────────────────────

    def _get_skill_manager(self):
        """获取 SkillManager 实例."""
        if self._engine and hasattr(self._engine, '_skill_manager') and self._engine._skill_manager:
            return self._engine._skill_manager
        return None

    async def _skill_list(self, request):
        """GET /api/admin/skills — 完整技能列表."""
        from aiohttp import web
        _, err = self._require_admin(request)
        if err:
            return err

        mgr = self._get_skill_manager()
        if not mgr:
            return web.json_response({"skills": [], "count": 0})

        try:
            category = request.query.get("category", "")
            skills = await mgr.list_skills(category=category or None)
            result = []
            for s in skills:
                if s.deprecated:
                    continue
                d = s.to_metadata()
                d["body"] = s.to_full_content()
                result.append(d)
            return web.json_response({"skills": result, "count": len(result)})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def _skill_create(self, request):
        """POST /api/admin/skills — 创建技能."""
        from aiohttp import web
        _, err = self._require_admin(request)
        if err:
            return err

        mgr = self._get_skill_manager()
        if not mgr:
            return web.json_response({"error": "SkillManager not available"}, status=503)

        try:
            body = await request.json()
            # 支持 SKILL.md 格式 (skill_md 字段) 或旧 JSON 字段
            skill_md = body.get("skill_md", "")
            if skill_md:
                from agent.skills.manager import Skill as SkillModel
                parsed = SkillModel.from_skill_md(skill_md)
                skill = await mgr.create_skill(
                    name=parsed.name,
                    description=parsed.description,
                    trigger=parsed.trigger,
                    body=parsed.body,
                    category=parsed.category,
                    tags=parsed.tags,
                    examples=parsed.examples,
                )
            else:
                skill = await mgr.create_skill(
                    name=body.get("name", ""),
                    description=body.get("description", ""),
                    trigger=body.get("trigger", ""),
                    steps=body.get("steps", []),
                    body=body.get("body", ""),
                    category=body.get("category", "general"),
                    tags=body.get("tags"),
                    examples=body.get("examples"),
                )
            return web.json_response({"skill": skill.to_metadata()}, status=201)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def _skill_detail(self, request):
        """GET /api/admin/skills/{skill_id} — 技能详情."""
        from aiohttp import web
        _, err = self._require_admin(request)
        if err:
            return err

        mgr = self._get_skill_manager()
        if not mgr:
            return web.json_response({"error": "SkillManager not available"}, status=503)

        skill_id = request.match_info["skill_id"]
        skill = await mgr.get_skill(skill_id)
        if not skill:
            return web.json_response({"error": "Skill not found"}, status=404)
        data = skill.to_metadata()
        data["body"] = skill.to_full_content()
        data["skill_md"] = skill.to_skill_md()
        return web.json_response({"skill": data})

    async def _skill_update(self, request):
        """PUT /api/admin/skills/{skill_id} — 更新技能."""
        from aiohttp import web
        _, err = self._require_admin(request)
        if err:
            return err

        mgr = self._get_skill_manager()
        if not mgr:
            return web.json_response({"error": "SkillManager not available"}, status=503)

        skill_id = request.match_info["skill_id"]
        try:
            body = await request.json()
            # 支持 SKILL.md 格式更新
            skill_md = body.get("skill_md", "")
            if skill_md:
                from agent.skills.manager import Skill as SkillModel
                parsed = SkillModel.from_skill_md(skill_md)
                updates = {}
                if parsed.name:
                    updates["name"] = parsed.name
                if parsed.description:
                    updates["description"] = parsed.description
                if parsed.trigger:
                    updates["trigger"] = parsed.trigger
                if parsed.body:
                    updates["body"] = parsed.body
                if parsed.tags:
                    updates["tags"] = parsed.tags
                if parsed.examples:
                    updates["examples"] = parsed.examples
                skill = await mgr.update_skill(skill_id, updates)
            else:
                skill = await mgr.update_skill(skill_id, body)
            if not skill:
                return web.json_response({"error": "Skill not found"}, status=404)
            return web.json_response({"skill": skill.to_metadata()})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def _skill_delete(self, request):
        """DELETE /api/admin/skills/{skill_id} — 删除技能."""
        from aiohttp import web
        _, err = self._require_admin(request)
        if err:
            return err

        mgr = self._get_skill_manager()
        if not mgr:
            return web.json_response({"error": "SkillManager not available"}, status=503)

        skill_id = request.match_info["skill_id"]
        ok = await mgr.delete_skill(skill_id)
        if not ok:
            return web.json_response({"error": "Skill not found"}, status=404)
        return web.json_response({"status": "deleted"})

    async def _skill_test(self, request):
        """POST /api/admin/skills/{skill_id}/test — 测试技能匹配."""
        from aiohttp import web
        _, err = self._require_admin(request)
        if err:
            return err

        mgr = self._get_skill_manager()
        if not mgr:
            return web.json_response({"error": "SkillManager not available"}, status=503)

        skill_id = request.match_info["skill_id"]
        try:
            body = await request.json()
            test_input = body.get("test_input", "")
            if not test_input:
                return web.json_response({"error": "test_input required"}, status=400)

            matched = await mgr.match_skill(test_input)
            target_skill = await mgr.get_skill(skill_id)
            if not target_skill:
                return web.json_response({"error": "Skill not found"}, status=404)

            is_match = matched is not None and matched.skill_id == skill_id
            result = {
                "matched": is_match,
                "matched_skill": matched.to_dict() if matched else None,
                "target_skill": target_skill.to_dict(),
                "test_input": test_input,
            }
            return web.json_response(result)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def _ws_memory_health(self, request):
        """GET /api/workspace/memory-health — 记忆系统健康面板."""
        from aiohttp import web

        mgr = getattr(self._engine, '_memory_manager', None) if self._engine else None
        if not mgr:
            return web.json_response({"error": "memory_manager not available"}, status=503)

        try:
            await mgr.initialize()
            provider = mgr._provider
            db = getattr(provider, '_db', None)

            health: dict = {"stats": await mgr.get_stats()}

            # 学习统计
            ll = getattr(self._engine, '_learning_loop', None)
            if ll:
                s = ll.stats
                health["learning"] = {
                    "total_turns": s.total_turns,
                    "successful_turns": s.successful_turns,
                    "failed_turns": s.failed_turns,
                    "skills_created": s.skills_created,
                    "skills_refined": s.skills_refined,
                    "memories_extracted": s.memories_extracted,
                }

            if db:
                # 合并历史
                cursor = await db.execute(
                    "SELECT consolidation_id, source_ids, result_id, strategy, created_at "
                    "FROM memory_consolidations ORDER BY created_at DESC LIMIT 10"
                )
                rows = await cursor.fetchall()
                health["consolidations"] = [
                    {"id": r[0], "source_count": len(__import__('json').loads(r[1])), "result_id": r[2], "strategy": r[3], "created_at": r[4]}
                    for r in rows
                ]

                # 反思洞察
                cursor = await db.execute(
                    "SELECT reflection_id, reflection_type, content, action_items, status, created_at "
                    "FROM reflections WHERE status = 'active' ORDER BY created_at DESC LIMIT 10"
                )
                rows = await cursor.fetchall()
                health["reflections"] = [
                    {"id": r[0], "type": r[1], "content": r[2], "action_items": r[3], "status": r[4], "created_at": r[5]}
                    for r in rows
                ]

                # 反馈分布
                cursor = await db.execute(
                    "SELECT signal, COUNT(*) FROM memory_feedback GROUP BY signal"
                )
                rows = await cursor.fetchall()
                health["feedback"] = {r[0]: r[1] for r in rows}

                # 有用性 top/bottom
                cursor = await db.execute(
                    "SELECT memory_id, content, usefulness_score, feedback_count FROM memories "
                    "WHERE feedback_count > 0 ORDER BY usefulness_score DESC LIMIT 5"
                )
                rows = await cursor.fetchall()
                health["top_useful"] = [
                    {"id": r[0], "content": r[1][:100], "score": r[2], "feedback_count": r[3]}
                    for r in rows
                ]

                cursor = await db.execute(
                    "SELECT memory_id, content, usefulness_score, feedback_count FROM memories "
                    "WHERE feedback_count > 0 ORDER BY usefulness_score ASC LIMIT 5"
                )
                rows = await cursor.fetchall()
                health["bottom_useful"] = [
                    {"id": r[0], "content": r[1][:100], "score": r[2], "feedback_count": r[3]}
                    for r in rows
                ]

                # META 记忆
                cursor = await db.execute(
                    "SELECT memory_id, content, created_at FROM memories WHERE memory_type = 'meta' ORDER BY created_at DESC LIMIT 10"
                )
                rows = await cursor.fetchall()
                health["meta_memories"] = [
                    {"id": r[0], "content": r[1], "created_at": r[2]}
                    for r in rows
                ]

            return web.json_response(health)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def _ws_metrics(self, request):
        """GET /api/workspace/metrics — Prometheus 格式指标."""
        from aiohttp import web
        from agent.memory.metrics import get_metrics

        accept = request.headers.get("Accept", "")
        m = get_metrics()

        if "application/json" in accept:
            return web.json_response(m.to_dict())
        else:
            return web.Response(
                text=m.to_prometheus(),
                content_type="text/plain; version=0.0.4; charset=utf-8",
            )

    async def _ws_skills_list(self, request):
        """GET /api/workspace/skills — 列出技能."""
        from aiohttp import web

        if not self._engine or not hasattr(self._engine, '_skill_manager') or not self._engine._skill_manager:
            return web.json_response({"skills": [], "count": 0})

        try:
            mgr = self._engine._skill_manager
            if hasattr(mgr, '_ensure_loaded'):
                await mgr._ensure_loaded()
            skills = await mgr.list_skills()
            result = [
                {
                    "id": s.skill_id,
                    "name": s.name,
                    "description": s.description,
                    "category": s.category,
                    "trigger": s.trigger,
                    "success_rate": s.success_rate,
                    "use_count": s.use_count,
                    "version": s.version,
                    "tags": list(s.tags) if s.tags else [],
                    "steps_count": len(s.steps),
                }
                for s in skills
                if "deprecated" not in (s.tags or [])
            ]
            return web.json_response({"skills": result, "count": len(result)})
        except Exception as e:
            logger.warning("Skills list failed: %s", e)
            return web.json_response({"skills": [], "count": 0, "error": str(e)})

    # ── Hub API ──────────────────────────────────────────────────

    def _get_hub_client(self):
        if self._hub_client:
            return self._hub_client
        if self._gateway and hasattr(self._gateway, '_hub_client'):
            return self._gateway._hub_client
        return None

    async def _hub_search(self, request):
        """GET /api/admin/hub/search?q=...&category=...&sort=...&page=...&per_page=..."""
        from aiohttp import web
        _, err = self._require_admin(request)
        if err:
            return err

        hub = self._get_hub_client()
        if not hub or not hub._index:
            return web.json_response({"error": "Hub not initialized"}, status=503)

        query = request.query.get("q", "")
        category = request.query.get("category", "")
        sort = request.query.get("sort", "downloads")
        try:
            page = max(1, int(request.query.get("page", "1")))
            per_page = min(50, max(1, int(request.query.get("per_page", "20"))))
        except (ValueError, TypeError):
            page, per_page = 1, 20

        results = await hub._index.search(query=query, category=category, sort=sort, page=page, per_page=per_page)
        total = await hub._index.total_count()
        return web.json_response({"results": results, "total": total, "page": page})

    async def _hub_categories(self, request):
        """GET /api/admin/hub/categories"""
        from aiohttp import web
        _, err = self._require_admin(request)
        if err:
            return err
        hub = self._get_hub_client()
        if not hub or not hub._index:
            return web.json_response({"error": "Hub not initialized"}, status=503)
        cats = await hub._index.categories()
        return web.json_response({"categories": cats})

    async def _hub_featured(self, request):
        """GET /api/admin/hub/featured"""
        from aiohttp import web
        _, err = self._require_admin(request)
        if err:
            return err
        hub = self._get_hub_client()
        if not hub or not hub._index:
            return web.json_response({"error": "Hub not initialized"}, status=503)
        results = await hub._index.featured(limit=20)
        return web.json_response({"results": results})

    async def _hub_detail(self, request):
        """GET /api/admin/hub/skill/{slug}"""
        from aiohttp import web
        _, err = self._require_admin(request)
        if err:
            return err
        hub = self._get_hub_client()
        if not hub or not hub._index:
            return web.json_response({"error": "Hub not initialized"}, status=503)
        slug = request.match_info["slug"]
        skill = await hub._index.get(slug)
        if not skill:
            return web.json_response({"error": "Not found"}, status=404)
        return web.json_response({"skill": skill})

    async def _hub_install(self, request):
        """POST /api/admin/hub/install {name, version?}"""
        from aiohttp import web
        _, err = self._require_admin(request)
        if err:
            return err

        hub = self._get_hub_client()
        if not hub:
            return web.json_response({"error": "Hub not initialized"}, status=503)

        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        name = body.get("name", "").strip()
        if not name:
            return web.json_response({"error": "name required"}, status=400)

        version = body.get("version", "latest")
        result = await hub.install(name, version)
        if result.success:
            return web.json_response({"status": "ok", "skill_id": result.skill_id, "message": result.message})
        return web.json_response({"error": result.message}, status=400)

    async def _hub_publish(self, request):
        """POST /api/admin/hub/publish {skill_id}"""
        from aiohttp import web
        _, err = self._require_admin(request)
        if err:
            return err

        hub = self._get_hub_client()
        if not hub:
            return web.json_response({"error": "Hub not initialized"}, status=503)

        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        skill_id = body.get("skill_id", "").strip()
        if not skill_id:
            return web.json_response({"error": "skill_id required"}, status=400)

        result = await hub.publish(skill_id)
        if result.success:
            return web.json_response({"status": "ok", "pkg_path": result.pkg_path, "message": result.message})
        return web.json_response({"error": result.message}, status=400)

    async def _hub_published(self, request):
        """GET /api/admin/hub/published — 已发布技能列表."""
        from aiohttp import web
        _, err = self._require_admin(request)
        if err:
            return err

        hub = self._get_hub_client()
        if not hub or not hub._index:
            return web.json_response({"skills": []})

        results = await hub.search()
        return web.json_response({
            "skills": [
                {
                    "name": r.name,
                    "version": r.version,
                    "author": r.author,
                    "description": r.description,
                    "tags": r.tags,
                    "price": r.price,
                    "downloads": r.downloads,
                }
                for r in results
            ],
        })

    # ── Hub Remote (充值代理) ────────────────────────────────────

    async def _hub_remote_register(self, request):
        from aiohttp import web
        hub = self._get_hub_client()
        if not hub or not hub._hub_url:
            return web.json_response({"error": "Hub URL not configured"}, status=503)
        try:
            body = await request.json()
            data = await hub.remote_register(body.get("username", ""), body.get("email", ""), body.get("password", ""))
            return web.json_response(data)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def _hub_remote_login(self, request):
        from aiohttp import web
        hub = self._get_hub_client()
        if not hub or not hub._hub_url:
            return web.json_response({"error": "Hub URL not configured"}, status=503)
        try:
            body = await request.json()
            data = await hub.remote_login(body.get("username", ""), body.get("password", ""))
            return web.json_response(data)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def _hub_remote_balance(self, request):
        from aiohttp import web
        hub = self._get_hub_client()
        if not hub or not hub._hub_url or not hub.has_remote_token:
            return web.json_response({"error": "Not logged in to Hub"}, status=401)
        try:
            data = await hub.remote_balance()
            return web.json_response(data)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def _hub_remote_packages(self, request):
        from aiohttp import web
        hub = self._get_hub_client()
        if not hub or not hub._hub_url:
            return web.json_response({"error": "Hub URL not configured"}, status=503)
        try:
            data = await hub.remote_recharge_packages()
            return web.json_response(data)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def _hub_remote_recharge_create(self, request):
        from aiohttp import web
        hub = self._get_hub_client()
        if not hub or not hub._hub_url or not hub.has_remote_token:
            return web.json_response({"error": "Not logged in to Hub"}, status=401)
        try:
            body = await request.json()
            data = await hub.remote_recharge_create(body.get("amount", 0), body.get("pay_type", "native"))
            return web.json_response(data)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def _hub_remote_recharge_status(self, request):
        from aiohttp import web
        hub = self._get_hub_client()
        if not hub or not hub._hub_url or not hub.has_remote_token:
            return web.json_response({"error": "Not logged in to Hub"}, status=401)
        try:
            order_no = request.match_info["order_no"]
            data = await hub.remote_recharge_status(order_no)
            return web.json_response(data)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    # ── Skill Version API ────────────────────────────────────────

    async def _skill_versions(self, request):
        """GET /api/admin/skills/{skill_id}/versions"""
        from aiohttp import web
        _, err = self._require_admin(request)
        if err:
            return err

        mgr = self._get_skill_manager()
        if not mgr:
            return web.json_response({"error": "SkillManager not available"}, status=503)

        skill_id = request.match_info["skill_id"]
        versions = await mgr.list_versions(skill_id)
        return web.json_response({"skill_id": skill_id, "versions": versions})

    async def _skill_rollback(self, request):
        """POST /api/admin/skills/{skill_id}/rollback {version}"""
        from aiohttp import web
        _, err = self._require_admin(request)
        if err:
            return err

        mgr = self._get_skill_manager()
        if not mgr:
            return web.json_response({"error": "SkillManager not available"}, status=503)

        skill_id = request.match_info["skill_id"]
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        version = body.get("version", "")
        if not version:
            return web.json_response({"error": "version required"}, status=400)

        skill = await mgr.rollback_version(skill_id, version)
        if not skill:
            return web.json_response({"error": f"Version {version} not found"}, status=404)
        return web.json_response({"status": "ok", "skill": skill.to_metadata()})


# ═══════════════════════════════════════════════════════════════════
#  前端已迁移到 web/static/index.html
# ═══════════════════════════════════════════════════════════════════
