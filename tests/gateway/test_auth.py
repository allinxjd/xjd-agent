"""Tests for gateway.core.auth — 认证体系."""

import pytest
from gateway.core.auth import (
    PasswordHasher,
    JWTManager,
    APIKeyManager,
    AuthManager,
    User,
    Role,
    Permission,
    AuthMethod,
    ROLE_PERMISSIONS,
)


class TestPasswordHasher:
    def test_hash_and_verify(self):
        h = PasswordHasher()
        hashed = h.hash_password("mypassword")
        assert h.verify_password("mypassword", hashed) is True
        assert h.verify_password("wrong", hashed) is False

    def test_different_salt(self):
        h = PasswordHasher()
        h1 = h.hash_password("pass", salt="salt1")
        h2 = h.hash_password("pass", salt="salt2")
        assert h1 != h2


class TestJWTManager:
    def test_create_and_verify(self):
        jwt = JWTManager(secret_key="test-secret")
        token = jwt.create_token("user123", role="admin")
        payload = jwt.verify_token(token)
        assert payload is not None
        assert payload["sub"] == "user123"
        assert payload["role"] == "admin"

    def test_invalid_token(self):
        jwt = JWTManager(secret_key="secret1")
        token = jwt.create_token("user")
        # Verify with wrong secret
        jwt2 = JWTManager(secret_key="secret2")
        assert jwt2.verify_token(token) is None

    def test_malformed_token(self):
        jwt = JWTManager()
        assert jwt.verify_token("invalid") is None
        assert jwt.verify_token("a.b") is None
        assert jwt.verify_token("") is None

    def test_expired_token(self):
        jwt = JWTManager(secret_key="s", expires_hours=-1)  # already expired
        token = jwt.create_token("user")
        assert jwt.verify_token(token) is None


class TestAPIKeyManager:
    def test_generate_key(self):
        mgr = APIKeyManager()
        key = mgr.generate_key()
        assert key.startswith("xjd_")
        assert len(key) > 10

    def test_register_and_validate(self):
        mgr = APIKeyManager()
        user = User(user_id="u1", username="test", role=Role.USER)
        key = mgr.generate_key()
        mgr.register_key(key, user)
        found = mgr.validate_key(key)
        assert found is not None
        assert found.user_id == "u1"

    def test_invalid_key(self):
        mgr = APIKeyManager()
        assert mgr.validate_key("bad_key") is None

    def test_revoke(self):
        mgr = APIKeyManager()
        user = User(user_id="u1")
        key = mgr.generate_key()
        mgr.register_key(key, user)
        assert mgr.revoke_key(key) is True
        assert mgr.validate_key(key) is None


class TestUserRBAC:
    def test_admin_has_all_permissions(self):
        u = User(role=Role.ADMIN)
        assert u.has_permission(Permission.CHAT)
        assert u.has_permission(Permission.ADMIN_WRITE)
        assert u.has_permission(Permission.PLUGINS_MANAGE)

    def test_user_permissions(self):
        u = User(role=Role.USER)
        assert u.has_permission(Permission.CHAT)
        assert u.has_permission(Permission.TOOLS_USE)
        assert not u.has_permission(Permission.ADMIN_WRITE)

    def test_viewer_limited(self):
        u = User(role=Role.VIEWER)
        assert u.has_permission(Permission.CHAT)
        assert u.has_permission(Permission.MEMORY_READ)
        assert not u.has_permission(Permission.MEMORY_WRITE)
        assert not u.has_permission(Permission.TOOLS_USE)


class TestAuthManager:
    @pytest.mark.asyncio
    async def test_register_and_password_auth(self):
        auth = AuthManager(secret_key="test")
        user = await auth.register_user("bob", "pass123", Role.USER)
        assert user.username == "bob"

        result = auth.authenticate_password("bob", "pass123")
        assert result.authenticated is True
        assert result.user.username == "bob"

    @pytest.mark.asyncio
    async def test_wrong_password(self):
        auth = AuthManager(secret_key="test")
        await auth.register_user("alice", "correct")
        result = auth.authenticate_password("alice", "wrong")
        assert result.authenticated is False

    @pytest.mark.asyncio
    async def test_api_key_flow(self):
        auth = AuthManager(secret_key="test")
        user = await auth.register_user("svc", "p", Role.SERVICE)
        key = auth.create_api_key(user)
        result = auth.authenticate_api_key(key)
        assert result.authenticated is True
        assert result.user.role == Role.SERVICE

    @pytest.mark.asyncio
    async def test_jwt_flow(self):
        auth = AuthManager(secret_key="test")
        user = await auth.register_user("u", "p")
        token = auth.create_token(user)
        result = auth.authenticate_jwt(token)
        assert result.authenticated is True

    @pytest.mark.asyncio
    async def test_authorize(self):
        auth = AuthManager()
        user = User(role=Role.ADMIN)
        assert auth.authorize(user, Permission.ADMIN_WRITE) is True
        viewer = User(role=Role.VIEWER)
        assert auth.authorize(viewer, Permission.ADMIN_WRITE) is False
