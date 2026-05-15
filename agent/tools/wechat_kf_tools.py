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


def _secrets_to_config(instance_id: str = "") -> tuple[dict[str, Any], str]:
    """从 SecretsStore 读取配置，返回 (config, error).

    instance_id 为空时读取 "wechat-kf"，否则读取 "wechat-kf:{instance_id}".
    """
    from agent.core.secrets import get_secrets_store
    store = get_secrets_store()
    skill_key = f"wechat-kf:{instance_id}" if instance_id else "wechat-kf"
    secrets = store.get_all(skill_key)

    corp_id = secrets.get("WECHAT_KF_CORP_ID", "").strip()
    kf_secret = secrets.get("WECHAT_KF_SECRET", "").strip()
    open_kfid = secrets.get("WECHAT_KF_OPEN_KFID", "").strip()
    token = secrets.get("WECHAT_KF_TOKEN", "").strip()
    encoding_aes_key = secrets.get("WECHAT_KF_ENCODING_AES_KEY", "").strip()

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
        "instance_id": instance_id,
        "instance_name": secrets.get("WECHAT_KF_INSTANCE_NAME", ""),
    }
    return config, ""


async def _wechat_kf_start(**kwargs) -> str:
    global _adapter_running, _started_at
    instance_id = kwargs.get("instance_id", "").strip()

    from gateway.core.server import get_gateway_server
    gw = get_gateway_server()

    if instance_id:
        key = f"wechat_kf:{instance_id}"
        if gw and key in gw._adapters and gw._adapters[key].is_running:
            return json.dumps({"success": False, "error": f"微信客服[{instance_id}]已在运行中"}, ensure_ascii=False)
        config, err = _secrets_to_config(instance_id)
        if err:
            return json.dumps({"success": False, "error": err}, ensure_ascii=False)
        config["instance_id"] = instance_id
        if not gw:
            return json.dumps({"success": False, "error": "Gateway 未启动"}, ensure_ascii=False)
        result = await gw.add_adapter_runtime(key, config)
        if result == "ok":
            name = config.get("instance_name") or instance_id
            return json.dumps({"success": True, "message": f"微信客服[{name}]已启动"}, ensure_ascii=False)
        return json.dumps({"success": False, "error": f"启动失败: {result}"}, ensure_ascii=False)

    # 无 instance_id — 启动所有实例或单实例兼容模式
    from agent.core.secrets import get_secrets_store
    store = get_secrets_store()
    instances = store.list_instances("wechat-kf")

    if not gw:
        return json.dumps({"success": False, "error": "Gateway 未启动"}, ensure_ascii=False)

    if instances:
        results = []
        for inst_id in instances:
            key = f"wechat_kf:{inst_id}"
            if key in gw._adapters and gw._adapters[key].is_running:
                results.append(f"{inst_id}: 已在运行")
                continue
            config, err = _secrets_to_config(inst_id)
            if err:
                results.append(f"{inst_id}: {err}")
                continue
            config["instance_id"] = inst_id
            r = await gw.add_adapter_runtime(key, config)
            name = config.get("instance_name") or inst_id
            results.append(f"{name}: {'已启动' if r == 'ok' else r}")
        _adapter_running = True
        _started_at = time.time()
        return json.dumps({"success": True, "instances": results}, ensure_ascii=False)

    # 兼容旧单实例
    if _adapter_running and "wechat_kf" in gw._adapters and gw._adapters["wechat_kf"].is_running:
        return json.dumps({"success": False, "error": "微信客服已在运行中"}, ensure_ascii=False)

    config, err = _secrets_to_config()
    if err:
        return json.dumps({"success": False, "error": err}, ensure_ascii=False)

    result = await gw.add_adapter_runtime("wechat_kf", config)
    if result == "ok":
        _adapter_running = True
        _started_at = time.time()
        return json.dumps({"success": True, "message": "微信客服已启动"}, ensure_ascii=False)
    return json.dumps({"success": False, "error": f"启动失败: {result}"}, ensure_ascii=False)


async def _wechat_kf_stop(**kwargs) -> str:
    global _adapter_running, _started_at
    instance_id = kwargs.get("instance_id", "").strip()

    from gateway.core.server import get_gateway_server
    gw = get_gateway_server()
    if not gw:
        _adapter_running = False
        return json.dumps({"success": False, "error": "微信客服未在运行"}, ensure_ascii=False)

    if instance_id:
        key = f"wechat_kf:{instance_id}"
        if key not in gw._adapters:
            return json.dumps({"success": False, "error": f"微信客服[{instance_id}]未在运行"}, ensure_ascii=False)
        await gw.remove_adapter_runtime(key)
        return json.dumps({"success": True, "message": f"微信客服[{instance_id}]已停止"}, ensure_ascii=False)

    # 停止所有 wechat_kf 实例
    stopped = []
    for key in list(gw._adapters.keys()):
        if key == "wechat_kf" or key.startswith("wechat_kf:"):
            await gw.remove_adapter_runtime(key)
            stopped.append(key)
    _adapter_running = False
    _started_at = 0.0
    if not stopped:
        return json.dumps({"success": False, "error": "微信客服未在运行"}, ensure_ascii=False)
    return json.dumps({"success": True, "message": f"已停止 {len(stopped)} 个客服实例"}, ensure_ascii=False)


async def _wechat_kf_status(**kwargs) -> str:
    instance_id = kwargs.get("instance_id", "").strip()
    from gateway.core.server import get_gateway_server
    gw = get_gateway_server()

    if not gw:
        return json.dumps({"running": False, "message": "微信客服未启动"}, ensure_ascii=False)

    # 收集所有 wechat_kf 实例状态
    instances_info = []
    for key, adapter in gw._adapters.items():
        if key == "wechat_kf" or key.startswith("wechat_kf:"):
            inst_id = key.split(":", 1)[1] if ":" in key else ""
            if instance_id and inst_id != instance_id:
                continue
            stats = gw.stats.platform_stats.get(key, {})
            instances_info.append({
                "instance_id": inst_id or "(default)",
                "name": adapter.name,
                "running": adapter.is_running,
                "messages_received": stats.get("received", 0),
                "messages_sent": stats.get("sent", 0),
            })

    if not instances_info:
        return json.dumps({"running": False, "message": "微信客服未启动"}, ensure_ascii=False)

    uptime = time.time() - _started_at if _started_at > 0 else 0
    hours = int(uptime // 3600)
    minutes = int((uptime % 3600) // 60)
    uptime_str = f"{hours}h{minutes}m" if hours > 0 else f"{minutes}m"

    return json.dumps({
        "running": True,
        "uptime": uptime_str,
        "instances": instances_info,
    }, ensure_ascii=False)


def _get_kb_path(instance_id: str = "") -> str:
    """获取知识库文件路径 — 多实例时按 instance_id 隔离."""
    from pathlib import Path
    if instance_id:
        from agent.core.config import get_home
        inst_kb = get_home() / "skills" / "wechat-kf" / f"kb_{instance_id}.json"
        if inst_kb.exists():
            return str(inst_kb)
    return str(Path(__file__).parent.parent / "builtin_skills" / "wechat-kf" / "cs_knowledge.json")


async def _wechat_kf_update_kb(**kwargs) -> str:
    """更新微信客服知识库."""
    from pathlib import Path
    action = kwargs.get("action", "list")
    instance_id = kwargs.get("instance_id", "").strip()
    kb_path = Path(_get_kb_path(instance_id))

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
        description="启动微信客服智能接待 (支持多实例，不指定 instance_id 则启动全部)",
        parameters={
            "type": "object",
            "properties": {
                "instance_id": {
                    "type": "string",
                    "description": "客服实例 ID（可选，不填则启动所有已配置实例）",
                },
            },
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
            "properties": {
                "instance_id": {
                    "type": "string",
                    "description": "客服实例 ID（可选，不填则停止所有实例）",
                },
            },
        },
        handler=_wechat_kf_stop,
        category="wechat_kf",
    )

    registry.register(
        name="wechat_kf_status",
        description="查看微信客服运行状态 (所有实例或指定实例)",
        parameters={
            "type": "object",
            "properties": {
                "instance_id": {
                    "type": "string",
                    "description": "客服实例 ID（可选，不填则显示所有实例状态）",
                },
            },
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
                "instance_id": {"type": "string", "description": "客服实例 ID（可选，不填则操作默认知识库）"},
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
