"""OAuth + 认证体系 — API Key / JWT / OAuth2 多模式认证.

提供:
- API Key 认证 (简单模式)
- JWT Token 认证 (无状态)
- OAuth2 授权码流程
- RBAC 角色权限
- 速率限制中间件
- 会话管理
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)

class AuthMethod(str, Enum):
    """认证方式."""

    API_KEY = "api_key"
    JWT = "jwt"
    OAUTH2 = "oauth2"
    BASIC = "basic"

class Role(str, Enum):
    """用户角色."""

    ADMIN = "admin"
    USER = "user"
    VIEWER = "viewer"
    SERVICE = "service"  # 服务间调用

class Permission(str, Enum):
    """权限."""

    CHAT = "chat"
    CHAT_STREAM = "chat:stream"
    TOOLS_USE = "tools:use"
    TOOLS_MANAGE = "tools:manage"
    MEMORY_READ = "memory:read"
    MEMORY_WRITE = "memory:write"
    ADMIN_READ = "admin:read"
    ADMIN_WRITE = "admin:write"
    PLUGINS_MANAGE = "plugins:manage"
    MODELS_MANAGE = "models:manage"

# 角色 → 权限映射
ROLE_PERMISSIONS: dict[Role, list[Permission]] = {
    Role.ADMIN: list(Permission),  # 全部权限
    Role.USER: [
        Permission.CHAT,
        Permission.CHAT_STREAM,
        Permission.TOOLS_USE,
        Permission.MEMORY_READ,
        Permission.MEMORY_WRITE,
    ],
    Role.VIEWER: [
        Permission.CHAT,
        Permission.MEMORY_READ,
    ],
    Role.SERVICE: [
        Permission.CHAT,
        Permission.CHAT_STREAM,
        Permission.TOOLS_USE,
        Permission.MEMORY_READ,
        Permission.ADMIN_READ,
    ],
}

@dataclass
class User:
    """用户."""

    user_id: str = ""
    username: str = ""
    email: str = ""
    role: Role = Role.USER
    api_key: str = ""
    api_key_hash: str = ""
    password_hash: str = ""
    created_at: float = 0.0
    last_login: float = 0.0
    active: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def permissions(self) -> list[Permission]:
        return ROLE_PERMISSIONS.get(self.role, [])

    def has_permission(self, perm: Permission) -> bool:
        return perm in self.permissions

@dataclass
class AuthToken:
    """认证 Token."""

    token: str = ""
    user_id: str = ""
    method: AuthMethod = AuthMethod.JWT
    issued_at: float = 0.0
    expires_at: float = 0.0
    scopes: list[str] = field(default_factory=list)

    @property
    def is_expired(self) -> bool:
        return time.time() > self.expires_at if self.expires_at else False

@dataclass
class AuthResult:
    """认证结果."""

    authenticated: bool = False
    user: Optional[User] = None
    token: Optional[AuthToken] = None
    error: str = ""

class PasswordHasher:
    """密码哈希工具 (PBKDF2-SHA256, OWASP 2023 推荐 600k 迭代)."""

    ITERATIONS = 600_000

    @staticmethod
    def hash_password(password: str, salt: str = "") -> str:
        if not salt:
            salt = secrets.token_hex(16)
        dk = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt.encode("utf-8"),
            iterations=PasswordHasher.ITERATIONS,
        )
        return f"{salt}:{dk.hex()}"

    @staticmethod
    def verify_password(password: str, hashed: str) -> bool:
        try:
            salt, expected_hex = hashed.split(":", 1)
            # 兼容旧 100k 迭代的哈希 — 先试新迭代数，失败再试旧的
            for iters in (PasswordHasher.ITERATIONS, 100_000):
                dk = hashlib.pbkdf2_hmac(
                    "sha256",
                    password.encode("utf-8"),
                    salt.encode("utf-8"),
                    iterations=iters,
                )
                if hmac.compare_digest(dk.hex(), expected_hex):
                    return True
            return False
        except (ValueError, KeyError, TypeError):
            return False

class JWTManager:
    """JWT Token 管理 (简化版，不依赖 PyJWT)."""

    def __init__(self, secret_key: str = "", expires_hours: int = 2) -> None:
        self._secret = secret_key or secrets.token_hex(32)
        self._expires_hours = expires_hours

    def create_token(
        self,
        user_id: str,
        role: str = "user",
        extra: dict | None = None,
    ) -> str:
        """创建 JWT Token."""
        import base64

        now = time.time()
        payload = {
            "sub": user_id,
            "role": role,
            "iat": int(now),
            "exp": int(now + self._expires_hours * 3600),
        }
        if extra:
            payload.update(extra)

        header = base64.urlsafe_b64encode(
            json.dumps({"alg": "HS256", "typ": "JWT"}).encode()
        ).rstrip(b"=").decode()

        body = base64.urlsafe_b64encode(
            json.dumps(payload).encode()
        ).rstrip(b"=").decode()

        signature_input = f"{header}.{body}"
        sig = hmac.new(
            self._secret.encode(),
            signature_input.encode(),
            hashlib.sha256,
        ).digest()
        signature = base64.urlsafe_b64encode(sig).rstrip(b"=").decode()

        return f"{header}.{body}.{signature}"

    def verify_token(self, token: str) -> Optional[dict]:
        """验证 JWT Token."""
        import base64

        try:
            parts = token.split(".")
            if len(parts) != 3:
                return None

            header_b64, body_b64, sig_b64 = parts

            # 验证签名
            signature_input = f"{header_b64}.{body_b64}"
            expected_sig = hmac.new(
                self._secret.encode(),
                signature_input.encode(),
                hashlib.sha256,
            ).digest()
            expected = base64.urlsafe_b64encode(expected_sig).rstrip(b"=").decode()

            if not hmac.compare_digest(sig_b64, expected):
                return None

            # 解析 payload
            padding = 4 - len(body_b64) % 4
            body_b64 += "=" * padding
            payload = json.loads(base64.urlsafe_b64decode(body_b64))

            # 检查过期
            if payload.get("exp", 0) < time.time():
                return None

            return payload

        except (ValueError, KeyError, TypeError):
            return None

class APIKeyManager:
    """API Key 管理 (HMAC-SHA256 with server secret)."""

    def __init__(self, server_secret: str = "") -> None:
        self._keys: dict[str, User] = {}  # key_hash → User
        self._server_secret = server_secret or secrets.token_hex(32)

    def generate_key(self, prefix: str = "xjd") -> str:
        """生成 API Key."""
        key = f"{prefix}_{secrets.token_urlsafe(32)}"
        return key

    def hash_key(self, key: str) -> str:
        return hmac.new(
            self._server_secret.encode(),
            key.encode(),
            hashlib.sha256,
        ).hexdigest()

    def register_key(self, key: str, user: User) -> None:
        key_hash = self.hash_key(key)
        user.api_key_hash = key_hash
        self._keys[key_hash] = user

    def validate_key(self, key: str) -> Optional[User]:
        key_hash = self.hash_key(key)
        return self._keys.get(key_hash)

    def revoke_key(self, key: str) -> bool:
        key_hash = self.hash_key(key)
        if key_hash in self._keys:
            del self._keys[key_hash]
            return True
        return False

class AuthManager:
    """统一认证管理器.

    用法:
        auth = AuthManager(secret_key="your-secret")
        await auth.initialize()

        # 注册用户
        user = await auth.register_user("username", "password", Role.USER)

        # API Key 认证
        key = auth.create_api_key(user)
        result = auth.authenticate_api_key(key)

        # JWT 认证
        token = auth.create_token(user)
        result = auth.authenticate_jwt(token)
    """

    def __init__(
        self,
        secret_key: str = "",
        db_path: str | None = None,
    ) -> None:
        self._secret = secret_key or secrets.token_hex(32)
        self._jwt = JWTManager(self._secret)
        self._api_keys = APIKeyManager(server_secret=self._secret)
        self._hasher = PasswordHasher()
        self._users: dict[str, User] = {}  # user_id → User
        self._db_path = db_path
        self._db = None

    async def initialize(self) -> None:
        """初始化 (加载用户数据)."""
        if self._db_path:
            import aiosqlite
            self._db = await aiosqlite.connect(self._db_path)
            await self._db.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id TEXT PRIMARY KEY,
                    username TEXT UNIQUE,
                    email TEXT DEFAULT '',
                    role TEXT DEFAULT 'user',
                    password_hash TEXT DEFAULT '',
                    api_key_hash TEXT DEFAULT '',
                    created_at REAL DEFAULT 0,
                    last_login REAL DEFAULT 0,
                    active INTEGER DEFAULT 1,
                    metadata TEXT DEFAULT '{}'
                )
            """)
            await self._db.commit()

            # 加载用户
            cursor = await self._db.execute("SELECT * FROM users")
            async for row in cursor:
                user = User(
                    user_id=row[0],
                    username=row[1],
                    email=row[2],
                    role=Role(row[3]),
                    password_hash=row[4],
                    api_key_hash=row[5],
                    created_at=row[6],
                    last_login=row[7],
                    active=bool(row[8]),
                )
                self._users[user.user_id] = user
                if user.api_key_hash:
                    self._api_keys._keys[user.api_key_hash] = user

            logger.info("AuthManager loaded: %d users", len(self._users))

        # 确保有默认 admin — 首次启动时生成随机密码
        if not any(u.role == Role.ADMIN for u in self._users.values()):
            import secrets as _sec
            generated_pw = _sec.token_urlsafe(16)
            admin_key = await self.register_user(
                username="admin",
                password=generated_pw,
                role=Role.ADMIN,
            )
            # 写入临时文件而非日志，避免密码泄漏到日志系统
            import pathlib
            pw_file = pathlib.Path(self._db_path).parent / ".admin_initial_password"
            pw_file.write_text(f"admin:{generated_pw}\n", encoding="utf-8")
            pw_file.chmod(0o600)
            logger.warning(
                "Default admin created. Credentials saved to %s — 请立即修改密码并删除该文件!",
                pw_file,
            )

    async def register_user(
        self,
        username: str,
        password: str,
        role: Role = Role.USER,
        email: str = "",
    ) -> User:
        """注册用户."""
        user_id = f"u_{secrets.token_hex(8)}"
        user = User(
            user_id=user_id,
            username=username,
            email=email,
            role=role,
            password_hash=self._hasher.hash_password(password),
            created_at=time.time(),
            active=True,
        )

        self._users[user_id] = user

        if self._db:
            await self._db.execute(
                """INSERT INTO users (user_id, username, email, role, password_hash, created_at, active)
                   VALUES (?, ?, ?, ?, ?, ?, 1)""",
                (user_id, username, email, role.value, user.password_hash, user.created_at),
            )
            await self._db.commit()

        return user

    def create_api_key(self, user: User) -> str:
        """为用户创建 API Key."""
        key = self._api_keys.generate_key()
        self._api_keys.register_key(key, user)
        return key

    def create_token(self, user: User) -> str:
        """为用户创建 JWT Token."""
        return self._jwt.create_token(
            user_id=user.user_id,
            role=user.role.value,
            extra={"username": user.username},
        )

    def authenticate_api_key(self, key: str) -> AuthResult:
        """API Key 认证."""
        user = self._api_keys.validate_key(key)
        if user and user.active:
            return AuthResult(
                authenticated=True,
                user=user,
                token=AuthToken(
                    token=key[:8] + "...",
                    user_id=user.user_id,
                    method=AuthMethod.API_KEY,
                ),
            )
        return AuthResult(error="Invalid API key")

    def authenticate_jwt(self, token: str) -> AuthResult:
        """JWT 认证."""
        payload = self._jwt.verify_token(token)
        if not payload:
            return AuthResult(error="Invalid or expired token")

        user_id = payload.get("sub", "")
        user = self._users.get(user_id)
        if not user or not user.active:
            return AuthResult(error="User not found or disabled")

        return AuthResult(
            authenticated=True,
            user=user,
            token=AuthToken(
                token=token[:20] + "...",
                user_id=user_id,
                method=AuthMethod.JWT,
                expires_at=payload.get("exp", 0),
            ),
        )

    def authenticate_password(self, username: str, password: str) -> AuthResult:
        """用户名密码认证."""
        user = None
        for u in self._users.values():
            if u.username == username:
                user = u
                break

        if not user:
            return AuthResult(error="User not found")
        if not user.active:
            return AuthResult(error="User disabled")
        if not self._hasher.verify_password(password, user.password_hash):
            return AuthResult(error="Invalid password")

        token_str = self.create_token(user)
        return AuthResult(
            authenticated=True,
            user=user,
            token=AuthToken(
                token=token_str,
                user_id=user.user_id,
                method=AuthMethod.JWT,
            ),
        )

    def authorize(self, user: User, permission: Permission) -> bool:
        """检查用户权限."""
        return user.has_permission(permission)

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None
