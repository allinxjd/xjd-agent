"""微信客服工具 — 启动/停止/状态查看，注册到 ToolRegistry."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Optional

from agent.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

_adapter_running: bool = False
_started_at: float = 0.0


def _secrets_to_config() -> tuple[dict[str, Any], str]:
    """从 SecretsStore 读取配置，返回 (config, error)."""
    from agent.core.secrets import get_secrets_store
    store = get_secrets_store()
    secrets = store.get_all("wechat-kf")

    corp_id = secrets.get("WECHAT_KF_CORP_ID", "")
    kf_secret = secrets.get("WECHAT_KF_SECRET", "")
    open_kfid = secrets.get("WECHAT_KF_OPEN_KFID", "")
    token = secrets.get("WECHAT_KF_TOKEN", "")
    encoding_aes_key = secrets.get("WECHAT_KF_ENCODING_AES_KEY", "")

    missing = []
    if not corp_id:
        missing.append("WECHAT_KF_CORP_ID")
    if not kf_secret:
        missing.append("WECHAT_KF_SECRET")
    if not open_kfid:
        missing.append("WECHAT_KF_OPEN_KFID")
    if not token:
        missing.append("WECHAT_KF_TOKEN")
    if not encoding_aes_key:
        missing.append("WECHAT_KF_ENCODING_AES_KEY")

    if missing:
        return {}, f"缺少必要配置: {', '.join(missing)}。请在技能设置中填写。"

    config = {
        "corp_id": corp_id,
        "kf_secret": kf_secret,
        "open_kfid": open_kfid,
        "token": token,
        "encoding_aes_key": encoding_aes_key,
        "webhook_port": int(secrets.get("WECHAT_KF_WEBHOOK_PORT", "9003")),
        "notify_contact": secrets.get("WECHAT_KF_NOTIFY_CONTACT", ""),
    }
    return config, ""


async def _wechat_kf_start(**kwargs) -> str:
    global _adapter_running, _started_at

    if _adapter_running:
        from gateway.core.server import get_gateway_server
        gw = get_gateway_server()
        if gw and "wechat_kf" in gw._adapters:
            adapter = gw._adapters["wechat_kf"]
            if adapter.is_running:
                return json.dumps({
                    "success": False,
                    "error": "微信客服已在运行中",
                    "hint": "如需重启，请先调用 wechat_kf_stop",
                }, ensure_ascii=False)

    config, err = _secrets_to_config()
    if err:
        return json.dumps({"success": False, "error": err}, ensure_ascii=False)

    from gateway.core.server import get_gateway_server
    gw = get_gateway_server()
    if not gw:
        return json.dumps({
            "success": False,
            "error": "Gateway 未启动，无法注册微信客服适配器",
        }, ensure_ascii=False)

    result = await gw.add_adapter_runtime("wechat_kf", config)
    if result == "ok":
        _adapter_running = True
        _started_at = time.time()
        port = config["webhook_port"]
        return json.dumps({
            "success": True,
            "message": f"微信客服已启动，webhook 监听端口 {port}",
            "callback_path": "/wechat-kf/callback",
            "port": port,
        }, ensure_ascii=False)
    else:
        return json.dumps({
            "success": False,
            "error": f"启动失败: {result}",
        }, ensure_ascii=False)


async def _wechat_kf_stop(**kwargs) -> str:
    global _adapter_running, _started_at

    from gateway.core.server import get_gateway_server
    gw = get_gateway_server()
    if not gw or "wechat_kf" not in gw._adapters:
        _adapter_running = False
        return json.dumps({
            "success": False,
            "error": "微信客服未在运行",
        }, ensure_ascii=False)

    await gw.remove_adapter_runtime("wechat_kf")
    _adapter_running = False
    _started_at = 0.0
    return json.dumps({
        "success": True,
        "message": "微信客服已停止",
    }, ensure_ascii=False)


async def _wechat_kf_status(**kwargs) -> str:
    from gateway.core.server import get_gateway_server
    gw = get_gateway_server()

    if not gw or "wechat_kf" not in gw._adapters:
        return json.dumps({
            "running": False,
            "message": "微信客服未启动",
        }, ensure_ascii=False)

    adapter = gw._adapters["wechat_kf"]
    uptime = time.time() - _started_at if _started_at > 0 else 0

    hours = int(uptime // 3600)
    minutes = int((uptime % 3600) // 60)
    uptime_str = f"{hours}h{minutes}m" if hours > 0 else f"{minutes}m"

    stats = gw.stats.platform_stats.get("wechat_kf", {})

    return json.dumps({
        "running": adapter.is_running,
        "uptime": uptime_str,
        "messages_received": stats.get("received", 0),
        "messages_sent": stats.get("sent", 0),
        "webhook_port": adapter._config.get("webhook_port", 9003),
    }, ensure_ascii=False)


def _get_kb_path() -> str:
    """获取知识库文件路径."""
    from pathlib import Path
    return str(Path(__file__).parent.parent / "builtin_skills" / "wechat-kf" / "cs_knowledge.json")


async def _wechat_kf_update_kb(**kwargs) -> str:
    """更新微信客服知识库."""
    from pathlib import Path
    action = kwargs.get("action", "list")
    kb_path = Path(_get_kb_path())

    if not kb_path.exists():
        return json.dumps({"success": False, "error": "知识库文件不存在"}, ensure_ascii=False)

    try:
        kb = json.loads(kb_path.read_text(encoding="utf-8"))
    except Exception as e:
        return json.dumps({"success": False, "error": f"读取知识库失败: {e}"}, ensure_ascii=False)

    if action == "list":
        faq = kb.get("faq", [])
        return json.dumps({
            "success": True,
            "faq_count": len(faq),
            "faq": faq,
            "templates": kb.get("templates", {}),
        }, ensure_ascii=False)

    elif action == "add_faq":
        question = kwargs.get("question", "").strip()
        answer = kwargs.get("answer", "").strip()
        category = kwargs.get("category", "general").strip()
        if not question or not answer:
            return json.dumps({"success": False, "error": "question 和 answer 不能为空"}, ensure_ascii=False)
        faq = kb.setdefault("faq", [])
        faq.append({"q": question, "a": answer, "category": category})
        kb_path.write_text(json.dumps(kb, ensure_ascii=False, indent=2), encoding="utf-8")
        return json.dumps({"success": True, "message": f"已添加 FAQ: {question}"}, ensure_ascii=False)

    elif action == "remove_faq":
        question = kwargs.get("question", "").strip()
        if not question:
            return json.dumps({"success": False, "error": "请指定要删除的 question"}, ensure_ascii=False)
        faq = kb.get("faq", [])
        new_faq = [item for item in faq if item.get("q") != question]
        if len(new_faq) == len(faq):
            return json.dumps({"success": False, "error": f"未找到 FAQ: {question}"}, ensure_ascii=False)
        kb["faq"] = new_faq
        kb_path.write_text(json.dumps(kb, ensure_ascii=False, indent=2), encoding="utf-8")
        return json.dumps({"success": True, "message": f"已删除 FAQ: {question}"}, ensure_ascii=False)

    elif action == "update_template":
        key = kwargs.get("key", "").strip()
        value = kwargs.get("value", "").strip()
        if not key or not value:
            return json.dumps({"success": False, "error": "key 和 value 不能为空"}, ensure_ascii=False)
        templates = kb.setdefault("templates", {})
        templates[key] = value
        kb_path.write_text(json.dumps(kb, ensure_ascii=False, indent=2), encoding="utf-8")
        return json.dumps({"success": True, "message": f"已更新模板 {key}"}, ensure_ascii=False)

    else:
        return json.dumps({
            "success": False,
            "error": f"未知操作: {action}",
            "supported_actions": ["list", "add_faq", "remove_faq", "update_template"],
        }, ensure_ascii=False)


def register_wechat_kf_tools(registry: ToolRegistry) -> None:
    registry.register(
        name="wechat_kf_start",
        description="启动微信客服智能接待 (webhook 监听企业微信回调)",
        parameters={
            "type": "object",
            "properties": {},
        },
        handler=_wechat_kf_start,
        category="wechat_kf",
        requires_approval=True,
    )

    registry.register(
        name="wechat_kf_stop",
        description="停止微信客服智能接待",
        parameters={
            "type": "object",
            "properties": {},
        },
        handler=_wechat_kf_stop,
        category="wechat_kf",
    )

    registry.register(
        name="wechat_kf_status",
        description="查看微信客服运行状态 (是否在线、消息统计、运行时长)",
        parameters={
            "type": "object",
            "properties": {},
        },
        handler=_wechat_kf_status,
        category="wechat_kf",
    )

    registry.register(
        name="wechat_kf_update_kb",
        description="管理微信客服知识库 (查看/添加/删除 FAQ，更新回复模板)",
        parameters={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "add_faq", "remove_faq", "update_template"],
                    "description": "操作类型: list=查看, add_faq=添加FAQ, remove_faq=删除FAQ, update_template=更新模板",
                },
                "question": {"type": "string", "description": "FAQ 问题 (add_faq/remove_faq 时必填)"},
                "answer": {"type": "string", "description": "FAQ 回答 (add_faq 时必填)"},
                "category": {"type": "string", "description": "FAQ 分类 (可选, 默认 general)"},
                "key": {"type": "string", "description": "模板 key (update_template 时必填, 如 greeting/outside_hours/transfer)"},
                "value": {"type": "string", "description": "模板内容 (update_template 时必填)"},
            },
            "required": ["action"],
        },
        handler=_wechat_kf_update_kb,
        category="wechat_kf",
    )
