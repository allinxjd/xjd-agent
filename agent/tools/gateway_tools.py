"""Gateway 消息工具 — Agent 可通过聊天主动发消息给已知联系人."""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def register_gateway_tools(registry: Any, gateway: Any) -> None:
    """注册 Gateway 消息工具到 ToolRegistry."""

    async def send_to_contact(**kwargs: Any) -> str:
        platform = kwargs.get("platform", "wechat_clawbot")
        user_id = kwargs.get("user_id", "")
        text = kwargs.get("text", "")

        if not user_id:
            return json.dumps({"success": False, "error": "必须指定 user_id"}, ensure_ascii=False)
        if not text:
            return json.dumps({"success": False, "error": "消息内容不能为空"}, ensure_ascii=False)

        adapter = gateway.get_adapter(platform)
        if not adapter:
            return json.dumps({"success": False, "error": f"平台 '{platform}' 未注册"}, ensure_ascii=False)
        if not adapter.is_running:
            return json.dumps({"success": False, "error": f"平台 '{platform}' 未连接"}, ensure_ascii=False)

        if hasattr(adapter, "send_to_contact"):
            msg_id = await adapter.send_to_contact(user_id, text)
        else:
            msg_id = await adapter.send_text(user_id, text)

        if msg_id:
            return json.dumps({"success": True, "message_id": msg_id}, ensure_ascii=False)
        return json.dumps({"success": False, "error": "发送失败，可能对方未曾发过消息"}, ensure_ascii=False)

    async def list_contacts(**kwargs: Any) -> str:
        platform = kwargs.get("platform", "wechat_clawbot")
        adapter = gateway.get_adapter(platform)
        if not adapter:
            return json.dumps({"success": False, "error": f"平台 '{platform}' 未注册"}, ensure_ascii=False)
        if not adapter.is_running:
            return json.dumps({"success": False, "error": f"平台 '{platform}' 未连接"}, ensure_ascii=False)

        if hasattr(adapter, "list_known_contacts"):
            contacts = adapter.list_known_contacts()
            return json.dumps({"success": True, "contacts": contacts, "count": len(contacts)}, ensure_ascii=False)
        return json.dumps({"success": False, "error": "该平台不支持联系人列表"}, ensure_ascii=False)

    async def set_contact_nickname(**kwargs: Any) -> str:
        platform = kwargs.get("platform", "wechat_clawbot")
        user_id = kwargs.get("user_id", "")
        nickname = kwargs.get("nickname", "")

        if not user_id or not nickname:
            return json.dumps({"success": False, "error": "必须指定 user_id 和 nickname"}, ensure_ascii=False)

        adapter = gateway.get_adapter(platform)
        if not adapter:
            return json.dumps({"success": False, "error": f"平台 '{platform}' 未注册"}, ensure_ascii=False)

        if hasattr(adapter, "set_contact_nickname"):
            adapter.set_contact_nickname(user_id, nickname)
            return json.dumps({"success": True, "user_id": user_id, "nickname": nickname}, ensure_ascii=False)
        return json.dumps({"success": False, "error": "该平台不支持设置昵称"}, ensure_ascii=False)

    registry.register(
        name="send_to_contact",
        description="主动发送消息给已知联系人（对方需曾发过消息）。支持微信等平台。",
        parameters={
            "type": "object",
            "properties": {
                "platform": {
                    "type": "string",
                    "description": "消息平台 (默认 wechat_clawbot)",
                    "default": "wechat_clawbot",
                },
                "user_id": {"type": "string", "description": "联系人 ID"},
                "text": {"type": "string", "description": "消息内容"},
            },
            "required": ["user_id", "text"],
        },
        handler=send_to_contact,
        category="system",
    )

    registry.register(
        name="list_contacts",
        description="列出指定平台的已知联系人列表（含昵称）。",
        parameters={
            "type": "object",
            "properties": {
                "platform": {
                    "type": "string",
                    "description": "消息平台 (默认 wechat_clawbot)",
                    "default": "wechat_clawbot",
                },
            },
            "required": [],
        },
        handler=list_contacts,
        category="system",
    )

    registry.register(
        name="set_contact_nickname",
        description="为联系人设置昵称/备注名，方便后续按名字查找。",
        parameters={
            "type": "object",
            "properties": {
                "platform": {
                    "type": "string",
                    "description": "消息平台 (默认 wechat_clawbot)",
                    "default": "wechat_clawbot",
                },
                "user_id": {"type": "string", "description": "联系人 ID"},
                "nickname": {"type": "string", "description": "昵称/备注名"},
            },
            "required": ["user_id", "nickname"],
        },
        handler=set_contact_nickname,
        category="system",
    )

    # 加入 core toolset 确保 Layer 3 回退时也能用
    from agent.tools.registry import TOOLSETS
    for name in ("send_to_contact", "list_contacts", "set_contact_nickname"):
        if name not in TOOLSETS.get("core", []):
            TOOLSETS.setdefault("core", []).append(name)
