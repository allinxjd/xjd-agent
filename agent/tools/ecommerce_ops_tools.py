"""电商运营工具 — 店铺管理操作注册到 ToolRegistry.

每个工具接收 platform 参数，内部路由到对应平台适配器。
与 ecommerce_tools.py (做图) 和 ecommerce/tools.py (客户端 stub) 互补，
本模块面向卖家/商家的店铺运营操作。
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from agent.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

_platform_instances: dict[str, Any] = {}
_browser_lock: asyncio.Lock | None = None


async def _with_browser(coro):
    """序列化浏览器操作，防止并发导航冲突."""
    global _browser_lock
    if _browser_lock is None:
        _browser_lock = asyncio.Lock()
    async with _browser_lock:
        return await coro


def _get_platform(platform: str) -> Any:
    if platform in _platform_instances:
        return _platform_instances[platform]
    from agent.ecommerce.platforms import get_platform_class
    cls = get_platform_class(platform)
    if not cls:
        return None
    from agent.ecommerce.session import get_session_manager
    instance = cls(session_manager=get_session_manager())
    _platform_instances[platform] = instance
    return instance


def _result_json(result: Any) -> str:
    if hasattr(result, "to_dict"):
        return json.dumps(result.to_dict(), ensure_ascii=False, indent=2)
    return str(result)


def _parse_json_param(value: Any) -> tuple[Any, str]:
    """安全解析 JSON 字符串参数，返回 (data, error)."""
    if not isinstance(value, str):
        return value, ""
    try:
        return json.loads(value), ""
    except (json.JSONDecodeError, TypeError) as e:
        return None, f"JSON 解析失败: {e}"


def _no_platform(name: str) -> str:
    return json.dumps({"success": False, "error": f"未知平台: {name}"})


async def _login(platform: str, credentials: str = "") -> str:
    p = _get_platform(platform)
    if not p:
        return _no_platform(platform)
    creds, err = _parse_json_param(credentials) if credentials else ({}, "")
    if err:
        return json.dumps({"success": False, "error": err})
    return _result_json(await _with_browser(p.login(creds)))


async def _list_products(platform: str, status: str = "", page: int = 1) -> str:
    p = _get_platform(platform)
    if not p:
        return _no_platform(platform)
    filters: dict[str, Any] = {}
    if status:
        filters["status"] = status
    if page > 1:
        filters["page"] = page
    return _result_json(await _with_browser(p.list_products(filters)))


async def _get_product(platform: str, product_id: str) -> str:
    p = _get_platform(platform)
    if not p:
        return _no_platform(platform)
    return _result_json(await _with_browser(p.get_product(product_id)))


async def _create_product(platform: str, product_data: str) -> str:
    p = _get_platform(platform)
    if not p:
        return _no_platform(platform)
    data, err = _parse_json_param(product_data)
    if err:
        return json.dumps({"success": False, "error": err})
    return _result_json(await _with_browser(p.create_product(data)))


async def _update_product(platform: str, product_id: str, updates: str) -> str:
    p = _get_platform(platform)
    if not p:
        return _no_platform(platform)
    data, err = _parse_json_param(updates)
    if err:
        return json.dumps({"success": False, "error": err})
    return _result_json(await _with_browser(p.update_product(product_id, data)))


async def _toggle_product(platform: str, product_id: str, active: bool = True) -> str:
    p = _get_platform(platform)
    if not p:
        return _no_platform(platform)
    return _result_json(await _with_browser(p.toggle_product(product_id, active)))


async def _list_orders(platform: str, status: str = "", page: int = 1) -> str:
    p = _get_platform(platform)
    if not p:
        return _no_platform(platform)
    filters: dict[str, Any] = {}
    if status:
        filters["status"] = status
    if page > 1:
        filters["page"] = page
    return _result_json(await _with_browser(p.list_orders(filters)))


async def _ship_order(
    platform: str, order_id: str, tracking_number: str = "", carrier: str = "",
) -> str:
    p = _get_platform(platform)
    if not p:
        return _no_platform(platform)
    return _result_json(await _with_browser(p.ship_order(order_id, {
        "tracking_number": tracking_number, "carrier": carrier,
    })))


async def _shop_stats(
    platform: str, start_date: str = "", end_date: str = "",
) -> str:
    p = _get_platform(platform)
    if not p:
        return _no_platform(platform)
    dr: dict[str, str] = {}
    if start_date:
        dr["start"] = start_date
    if end_date:
        dr["end"] = end_date
    return _result_json(await _with_browser(p.get_shop_stats(dr)))


async def _list_messages(platform: str, page: int = 1) -> str:
    active = _get_active_cs(platform)
    if active:
        shops_info = []
        for k, client in active.items():
            shops_info.append({
                "mall_id": k.split(":", 1)[1],
                "status": "standby" if client.standby else "connected",
                "stats": client._stats,
            })
        return json.dumps({
            "status": "ws_active",
            "message": "智能客服正在运行，消息由 WebSocket 自动接收和回复。"
                       "打开浏览器客服页面会导致 WebSocket 被踢下线。"
                       "如需手动查看消息，请先调用 ecommerce_stop_cs 停止智能客服。",
            "shops": shops_info,
        }, ensure_ascii=False)
    p = _get_platform(platform)
    if not p:
        return _no_platform(platform)
    return _result_json(await _with_browser(p.list_messages({"page": page} if page > 1 else {})))


async def _reply_message(platform: str, msg_id: str, content: str) -> str:
    active = _get_active_cs(platform)
    if active:
        return json.dumps({
            "status": "ws_active",
            "message": "WebSocket 智能客服正在运行，消息由系统自动回复。如需手动回复，请先 ecommerce_stop_cs。",
        }, ensure_ascii=False)
    p = _get_platform(platform)
    if not p:
        return _no_platform(platform)
    return _result_json(await _with_browser(p.reply_message(msg_id, content)))


async def _create_promotion(platform: str, promo_data: str) -> str:
    p = _get_platform(platform)
    if not p:
        return _no_platform(platform)
    data, err = _parse_json_param(promo_data)
    if err:
        return json.dumps({"success": False, "error": err})
    return _result_json(await _with_browser(p.create_promotion(data)))


async def _list_promotions(platform: str) -> str:
    p = _get_platform(platform)
    if not p:
        return _no_platform(platform)
    return _result_json(await _with_browser(p.list_promotions()))


async def _get_product_stats(platform: str, product_id: str) -> str:
    p = _get_platform(platform)
    if not p:
        return _no_platform(platform)
    return _result_json(await _with_browser(p.get_product_stats(product_id)))


async def _list_platforms_handler() -> str:
    from agent.ecommerce.platforms import list_platforms
    return json.dumps({"platforms": list_platforms()}, ensure_ascii=False)


# ── 新增: 客服/广告/批量/报告 handlers ──

_cs_clients: dict[str, Any] = {}


def _cs_key(platform: str, mall_id: str) -> str:
    return f"{platform}:{mall_id}"


def _get_active_cs(platform: str) -> dict[str, Any]:
    """获取某平台所有活跃的 CS client (connected 或 standby)."""
    prefix = f"{platform}:"
    return {k: v for k, v in _cs_clients.items()
            if k.startswith(prefix) and (v.connected or v.standby)}

_cs_start_lock: asyncio.Lock | None = None


async def _build_cs_llm_fn(system_prompt: str = ""):
    """构建客服 LLM 回复函数 — 用 cheap 模型生成自然回复."""
    from agent.core.config import Config
    from agent.core.model_router import ModelRouter, build_credential_manager_from_config
    from agent.providers.openai_provider import OpenAIProvider
    from agent.providers.base import ProviderType, Message

    config = Config.load()
    config.apply_env_overrides()
    cred_mgr = build_credential_manager_from_config(config)
    router = ModelRouter(credential_manager=cred_mgr)

    cheap = config.model.cheap
    primary = config.model.primary
    source = cheap if (cheap and cheap.provider and cheap.api_key) else primary
    if not source or not source.provider or not source.api_key:
        logger.warning("CS LLM: 无可用模型配置，LLM 回复将不可用")
        return None

    provider = OpenAIProvider(
        provider_type=ProviderType(source.provider),
        api_key=source.api_key,
        base_url=source.base_url or None,
    )
    router.register_provider(provider)
    router.set_primary(source.provider, source.model)

    default_prompt = (
        "你是一名拼多多店铺客服。回复简短自然，像真人聊天。"
        "不要使用绝对化用语，不要引导站外交易，不确定的问题引导联系人工客服。"
    )
    sys_prompt = system_prompt or default_prompt

    async def llm_fn(*, history, kb_context, category, goods_info=None, order_info=None):
        messages = [Message(role="system", content=sys_prompt)]
        extra_ctx = []
        if kb_context:
            extra_ctx.append(f"[知识库参考]\n{kb_context}")
        if goods_info:
            extra_ctx.append(f"[咨询商品] {goods_info.get('goods_name', '')} ¥{goods_info.get('goods_price', '?')}")
        if order_info:
            extra_ctx.append(f"[相关订单] {order_info.get('order_id', '')} {order_info.get('goods_name', '')}")
        if extra_ctx:
            messages.append(Message(role="system", content="\n".join(extra_ctx)))

        for turn in history:
            role = "user" if turn["role"] == "buyer" else "assistant"
            messages.append(Message(role=role, content=turn["content"]))

        if not history:
            return None
        resp = await router.complete_with_failover(messages=messages, user_message=history[-1]["content"])
        reply = (resp.content or "").strip()
        if len(reply) > 200:
            reply = reply[:200]
        return reply if reply else None

    logger.info("CS LLM: 已初始化 (%s:%s)", source.provider, source.model)
    return llm_fn


async def _start_cs(platform: str = "pdd", shop_id: str = "") -> str:
    """启动智能客服. shop_id 为空则启动所有已登录店铺."""
    from agent.ecommerce.platforms.pdd_cs import PddCSClient
    from agent.ecommerce.operations.customer import AutoReplyEngine, KnowledgeBase, ConversationContext
    from agent.ecommerce.session import get_session_manager
    from pathlib import Path

    def _get_shop_name(acct: str) -> str:
        """从 shop_info.json 读取店铺名称."""
        try:
            info_path = Path.home() / ".xjd-agent" / "ecommerce" / platform / acct / "shop_info.json"
            if info_path.exists():
                import json as _json
                data = _json.loads(info_path.read_text())
                return data.get("mall_name", "")
        except Exception:
            pass
        return ""

    p = _get_platform(platform)
    if not p:
        return _no_platform(platform)

    sm = get_session_manager()
    kb_path = None
    for candidate in [
        Path(__file__).resolve().parent.parent.parent / "config" / "cs_knowledge.json",
        Path.home() / ".xjd-agent" / "skills" / "ecommerce-pdd" / "cs_knowledge.json",
        Path(__file__).resolve().parent.parent.parent / "skills" / "ecommerce-pdd" / "cs_knowledge.json",
    ]:
        if candidate.exists():
            kb_path = candidate
            break

    if shop_id:
        accounts = [shop_id]
    else:
        accounts = sm.list_accounts(platform)
        if not accounts:
            return json.dumps({
                "status": "no_shops",
                "error": "没有已登录的店铺，请先调用 ecommerce_login 登录拼多多商家后台",
            }, ensure_ascii=False)

    results = []
    global _cs_start_lock
    if _cs_start_lock is None:
        _cs_start_lock = asyncio.Lock()
    async with _cs_start_lock:
        for account in accounts:
            key = _cs_key(platform, account)
            if key in _cs_clients and (_cs_clients[key].connected or _cs_clients[key].standby):
                entry = {"mall_id": account, "status": "already_running"}
                name = _get_shop_name(account)
                if name:
                    entry["mall_name"] = name
                results.append(entry)
                continue

            cookies_list = sm.load_cookies(platform, account)
            if cookies_list:
                cookies = {c["name"]: c["value"] for c in cookies_list if "pinduoduo" in c.get("domain", "")}
            else:
                cookies = {}

            if not cookies:
                entry = {"mall_id": account, "status": "no_cookies", "error": "无可用 cookies，请先登录"}
                name = _get_shop_name(account)
                if name:
                    entry["mall_name"] = name
                results.append(entry)
                continue

            kb = KnowledgeBase(kb_path if kb_path else None)
            llm_fn = await _build_cs_llm_fn(kb.system_prompt)
            ctx = ConversationContext(max_turns=10, expire_minutes=30)
            engine = AutoReplyEngine(kb, llm_fn=llm_fn, context=ctx)
            client = PddCSClient(cookies, shop_id=account)
            client.set_reply_engine(engine)
            ok = await client.connect()
            if ok:
                async def _on_cs_offline(shop_id: str, reason: str, detail: str) -> None:
                    try:
                        from gateway.core.server import get_gateway_server
                        gw = get_gateway_server()
                        if not gw or not hasattr(gw, '_notifier') or not gw._notifier:
                            return
                        msg = f"⚠️ 拼多多客服离线\n店铺: {shop_id}\n原因: {detail}"
                        sent = set()
                        for s in list(gw._session_manager._sessions.values()):
                            if not s.is_active:
                                continue
                            for plat, cid in list(s.platform_bindings.items()):
                                key = f"{plat}:{cid}"
                                if key in sent or plat not in gw._notifier._send_callbacks:
                                    continue
                                sent.add(key)
                                await gw._notifier.send_direct(plat, cid, msg)
                        if not sent:
                            for ch in list(gw._notifier._send_callbacks):
                                await gw._notifier.send_direct(ch, "", msg)
                    except Exception as e:
                        logger.warning("CS offline notify failed: %s", e)

                client.on_offline = _on_cs_offline
                actual_id = client.mall_id or account
                actual_key = _cs_key(platform, actual_id)
                _cs_clients[actual_key] = client
                if actual_id != account:
                    _cs_clients[_cs_key(platform, account)] = client
                entry = {"mall_id": actual_id, "status": "connected"}
                name = _get_shop_name(account) or _get_shop_name(actual_id)
                if name:
                    entry["mall_name"] = name
                results.append(entry)
            else:
                entry = {"mall_id": account, "status": "failed", "error": "WebSocket 连接失败"}
                name = _get_shop_name(account)
                if name:
                    entry["mall_name"] = name
                results.append(entry)

    return json.dumps({"platform": platform, "shops": results}, ensure_ascii=False)


async def _stop_cs(platform: str = "pdd", shop_id: str = "") -> str:
    """停止智能客服. shop_id 为空则停止该平台所有店铺."""
    if shop_id:
        key = _cs_key(platform, shop_id)
        client = _cs_clients.pop(key, None)
        if client:
            await client.disconnect()
            return json.dumps({"status": "disconnected", "platform": platform, "mall_id": shop_id}, ensure_ascii=False)
        return json.dumps({"status": "not_running", "platform": platform, "mall_id": shop_id}, ensure_ascii=False)

    prefix = f"{platform}:"
    keys = [k for k in _cs_clients if k.startswith(prefix)]
    if not keys:
        return json.dumps({"status": "not_running", "platform": platform}, ensure_ascii=False)
    results = []
    for k in keys:
        client = _cs_clients.pop(k)
        try:
            await client.disconnect()
        except Exception as e:
            logger.warning("stop_cs disconnect error for %s: %s", k, e)
        results.append({"mall_id": k.split(":", 1)[1], "status": "disconnected"})
    return json.dumps({"platform": platform, "shops": results}, ensure_ascii=False)


async def _cs_status(platform: str = "pdd", shop_id: str = "") -> str:
    """查看客服状态. shop_id 为空则返回该平台所有店铺状态."""
    if shop_id:
        key = _cs_key(platform, shop_id)
        client = _cs_clients.get(key)
        if not client:
            return json.dumps({"status": "not_running", "platform": platform, "mall_id": shop_id}, ensure_ascii=False)
        return json.dumps({
            "status": "standby" if client.standby else ("connected" if client.connected else "disconnected"),
            "platform": platform, "mall_id": shop_id,
            "queue_size": client.queue.qsize(), "stats": client._stats,
        }, ensure_ascii=False)

    prefix = f"{platform}:"
    shops = []
    for k, client in _cs_clients.items():
        if k.startswith(prefix):
            mid = k.split(":", 1)[1]
            shops.append({
                "mall_id": mid,
                "status": "standby" if client.standby else ("connected" if client.connected else "disconnected"),
                "queue_size": client.queue.qsize(), "stats": client._stats,
            })
    if not shops:
        return json.dumps({"status": "not_running", "platform": platform}, ensure_ascii=False)
    return json.dumps({"platform": platform, "shops": shops}, ensure_ascii=False)


async def _list_shops(platform: str = "pdd") -> str:
    """列出某平台所有已登录的店铺."""
    from agent.ecommerce.session import get_session_manager
    sm = get_session_manager()
    accounts = sm.list_accounts(platform)
    shops = []
    for acc in accounts:
        key = _cs_key(platform, acc)
        client = _cs_clients.get(key)
        cs_status = "not_running"
        if client:
            if client.standby:
                cs_status = "standby"
            elif client.connected:
                cs_status = "connected"
            else:
                cs_status = "disconnected"
        entry = {"mall_id": acc, "has_cookies": True, "cs_status": cs_status}
        try:
            from pathlib import Path as _P
            info_path = _P.home() / ".xjd-agent" / "ecommerce" / platform / acc / "shop_info.json"
            if info_path.exists():
                _data = json.loads(info_path.read_text())
                if _data.get("mall_name"):
                    entry["mall_name"] = _data["mall_name"]
        except Exception:
            pass
        shops.append(entry)
    return json.dumps({"platform": platform, "shops": shops}, ensure_ascii=False)


async def _create_ad(platform: str = "pdd", config: str = "{}") -> str:
    p = _get_platform(platform)
    if not p:
        return _no_platform(platform)
    data, err = _parse_json_param(config)
    if err:
        return json.dumps({"success": False, "error": err}, ensure_ascii=False)
    from agent.ecommerce.operations.promotion import AdCampaignManager
    mgr = AdCampaignManager(p)
    return _result_json(await _with_browser(mgr.create_campaign(data)))


async def _manage_ad(platform: str = "pdd", campaign_id: str = "", action: str = "pause") -> str:
    p = _get_platform(platform)
    if not p:
        return _no_platform(platform)
    from agent.ecommerce.operations.promotion import AdCampaignManager
    mgr = AdCampaignManager(p)
    if action == "pause":
        return _result_json(await _with_browser(mgr.pause_campaign(campaign_id)))
    elif action == "resume":
        return _result_json(await _with_browser(mgr.resume_campaign(campaign_id)))
    return json.dumps({"error": f"未知操作: {action}"}, ensure_ascii=False)


async def _ad_stats(platform: str = "pdd", campaign_id: str = "") -> str:
    p = _get_platform(platform)
    if not p:
        return _no_platform(platform)
    from agent.ecommerce.operations.promotion import AdCampaignManager
    mgr = AdCampaignManager(p)
    return _result_json(await _with_browser(mgr.get_stats(campaign_id)))


async def _batch_ship(platform: str = "pdd", orders: str = "[]") -> str:
    p = _get_platform(platform)
    if not p:
        return _no_platform(platform)
    data, err = _parse_json_param(orders)
    if err:
        return json.dumps({"success": False, "error": err}, ensure_ascii=False)
    from agent.ecommerce.operations.order import OrderManager
    mgr = OrderManager(p)
    return _result_json(await _with_browser(mgr.batch_ship(data)))


async def _daily_report(platform: str = "pdd") -> str:
    p = _get_platform(platform)
    if not p:
        return _no_platform(platform)
    from agent.ecommerce.operations.analytics import AnalyticsAggregator
    agg = AnalyticsAggregator(p)
    return _result_json(await _with_browser(agg.generate_report()))


# ── 注册 ──

_PLATFORM_PARAM = {
    "type": "string",
    "description": "电商平台 (pdd/taobao/jd/douyin)",
}


def register_ecommerce_ops_tools(registry: ToolRegistry) -> None:
    """注册电商运营工具."""

    registry.register(
        name="ecommerce_login",
        description="登录电商平台商家后台",
        parameters={
            "type": "object",
            "properties": {
                "platform": _PLATFORM_PARAM,
                "credentials": {"type": "string", "description": "JSON 凭证 (可选)"},
            },
            "required": ["platform"],
        },
        handler=_login,
        category="ecommerce_ops",
        requires_approval=True,
    )

    registry.register(
        name="ecommerce_list_products",
        description="查看店铺商品列表",
        parameters={
            "type": "object",
            "properties": {
                "platform": _PLATFORM_PARAM,
                "status": {"type": "string", "description": "筛选状态"},
                "page": {"type": "integer", "default": 1},
            },
            "required": ["platform"],
        },
        handler=_list_products,
        category="ecommerce_ops",
    )

    registry.register(
        name="ecommerce_get_product",
        description="查看商品详情",
        parameters={
            "type": "object",
            "properties": {
                "platform": _PLATFORM_PARAM,
                "product_id": {"type": "string", "description": "商品 ID"},
            },
            "required": ["platform", "product_id"],
        },
        handler=_get_product,
        category="ecommerce_ops",
    )

    registry.register(
        name="ecommerce_create_product",
        description="发布新商品到店铺 (支持 images 字段上传图片，传本地路径列表)",
        parameters={
            "type": "object",
            "properties": {
                "platform": _PLATFORM_PARAM,
                "product_data": {"type": "string", "description": "商品信息 JSON"},
            },
            "required": ["platform", "product_data"],
        },
        handler=_create_product,
        category="ecommerce_ops",
        requires_approval=True,
    )

    registry.register(
        name="ecommerce_update_product",
        description="编辑已有商品信息 (支持 images 字段上传图片，传本地路径列表)",
        parameters={
            "type": "object",
            "properties": {
                "platform": _PLATFORM_PARAM,
                "product_id": {"type": "string"},
                "updates": {"type": "string", "description": "更新内容 JSON"},
            },
            "required": ["platform", "product_id", "updates"],
        },
        handler=_update_product,
        category="ecommerce_ops",
        requires_approval=True,
    )

    registry.register(
        name="ecommerce_toggle_product",
        description="商品上架/下架",
        parameters={
            "type": "object",
            "properties": {
                "platform": _PLATFORM_PARAM,
                "product_id": {"type": "string"},
                "active": {"type": "boolean", "description": "true=上架, false=下架"},
            },
            "required": ["platform", "product_id", "active"],
        },
        handler=_toggle_product,
        category="ecommerce_ops",
        requires_approval=True,
    )

    registry.register(
        name="ecommerce_list_orders",
        description="查看店铺订单列表",
        parameters={
            "type": "object",
            "properties": {
                "platform": _PLATFORM_PARAM,
                "status": {"type": "string", "description": "订单状态筛选"},
                "page": {"type": "integer", "default": 1},
            },
            "required": ["platform"],
        },
        handler=_list_orders,
        category="ecommerce_ops",
    )

    registry.register(
        name="ecommerce_ship_order",
        description="订单发货 (填写物流单号)",
        parameters={
            "type": "object",
            "properties": {
                "platform": _PLATFORM_PARAM,
                "order_id": {"type": "string"},
                "tracking_number": {"type": "string", "description": "物流单号"},
                "carrier": {"type": "string", "description": "快递公司"},
            },
            "required": ["platform", "order_id"],
        },
        handler=_ship_order,
        category="ecommerce_ops",
        requires_approval=True,
    )

    registry.register(
        name="ecommerce_shop_stats",
        description="查看店铺经营数据 (流量/转化/营收)",
        parameters={
            "type": "object",
            "properties": {
                "platform": _PLATFORM_PARAM,
                "start_date": {"type": "string", "description": "开始日期 YYYY-MM-DD"},
                "end_date": {"type": "string", "description": "结束日期 YYYY-MM-DD"},
            },
            "required": ["platform"],
        },
        handler=_shop_stats,
        category="ecommerce_ops",
    )

    registry.register(
        name="ecommerce_list_messages",
        description="查看店铺客服消息",
        parameters={
            "type": "object",
            "properties": {
                "platform": _PLATFORM_PARAM,
                "page": {"type": "integer", "default": 1},
            },
            "required": ["platform"],
        },
        handler=_list_messages,
        category="ecommerce_ops",
    )

    registry.register(
        name="ecommerce_reply_message",
        description="回复客服消息",
        parameters={
            "type": "object",
            "properties": {
                "platform": _PLATFORM_PARAM,
                "msg_id": {"type": "string"},
                "content": {"type": "string", "description": "回复内容"},
            },
            "required": ["platform", "msg_id", "content"],
        },
        handler=_reply_message,
        category="ecommerce_ops",
    )

    registry.register(
        name="ecommerce_create_promotion",
        description="创建营销活动/优惠券",
        parameters={
            "type": "object",
            "properties": {
                "platform": _PLATFORM_PARAM,
                "promo_data": {"type": "string", "description": "活动配置 JSON"},
            },
            "required": ["platform", "promo_data"],
        },
        handler=_create_promotion,
        category="ecommerce_ops",
        requires_approval=True,
    )

    registry.register(
        name="ecommerce_list_promotions",
        description="查看店铺营销活动列表",
        parameters={
            "type": "object",
            "properties": {
                "platform": _PLATFORM_PARAM,
            },
            "required": ["platform"],
        },
        handler=_list_promotions,
        category="ecommerce_ops",
    )

    registry.register(
        name="ecommerce_get_product_stats",
        description="查看单品数据统计 (流量/转化/销量)",
        parameters={
            "type": "object",
            "properties": {
                "platform": _PLATFORM_PARAM,
                "product_id": {"type": "string", "description": "商品 ID"},
            },
            "required": ["platform", "product_id"],
        },
        handler=_get_product_stats,
        category="ecommerce_ops",
    )

    registry.register(
        name="ecommerce_list_platforms",
        description="列出所有已支持的电商平台",
        parameters={"type": "object", "properties": {}},
        handler=_list_platforms_handler,
        category="ecommerce_ops",
    )

    # ── 新增工具 ──

    _SHOP_ID_PARAM = {"type": "string", "description": "店铺 mall_id（可选，不传则操作所有已登录店铺）"}

    registry.register(
        name="ecommerce_start_cs",
        description="启动实时智能客服 (WebSocket). 不传 shop_id 则启动所有已登录店铺",
        parameters={
            "type": "object",
            "properties": {"platform": _PLATFORM_PARAM, "shop_id": _SHOP_ID_PARAM},
            "required": ["platform"],
        },
        handler=_start_cs,
        category="ecommerce_ops",
        requires_approval=True,
        optional_deps=["websockets"],
    )

    registry.register(
        name="ecommerce_stop_cs",
        description="停止实时智能客服. 不传 shop_id 则停止所有店铺",
        parameters={
            "type": "object",
            "properties": {"platform": _PLATFORM_PARAM, "shop_id": _SHOP_ID_PARAM},
            "required": ["platform"],
        },
        handler=_stop_cs,
        category="ecommerce_ops",
        optional_deps=["websockets"],
    )

    registry.register(
        name="ecommerce_cs_status",
        description="查看智能客服运行状态. 不传 shop_id 则返回所有店铺状态",
        parameters={
            "type": "object",
            "properties": {"platform": _PLATFORM_PARAM, "shop_id": _SHOP_ID_PARAM},
            "required": ["platform"],
        },
        handler=_cs_status,
        category="ecommerce_ops",
        optional_deps=["websockets"],
    )

    registry.register(
        name="ecommerce_list_shops",
        description="列出某平台所有已登录的店铺 (mall_id + cookies 状态)",
        parameters={
            "type": "object",
            "properties": {"platform": _PLATFORM_PARAM},
            "required": ["platform"],
        },
        handler=_list_shops,
        category="ecommerce_ops",
    )

    registry.register(
        name="ecommerce_create_ad",
        description="创建付费推广广告计划",
        parameters={
            "type": "object",
            "properties": {
                "platform": _PLATFORM_PARAM,
                "config": {"type": "string", "description": "广告配置 JSON (campaign_name, budget, bid)"},
            },
            "required": ["platform", "config"],
        },
        handler=_create_ad,
        category="ecommerce_ops",
        requires_approval=True,
    )

    registry.register(
        name="ecommerce_manage_ad",
        description="管理广告计划 (暂停/恢复)",
        parameters={
            "type": "object",
            "properties": {
                "platform": _PLATFORM_PARAM,
                "campaign_id": {"type": "string", "description": "广告计划 ID"},
                "action": {"type": "string", "enum": ["pause", "resume"], "description": "操作"},
            },
            "required": ["platform", "campaign_id", "action"],
        },
        handler=_manage_ad,
        category="ecommerce_ops",
        requires_approval=True,
    )

    registry.register(
        name="ecommerce_ad_stats",
        description="查看广告投放数据",
        parameters={
            "type": "object",
            "properties": {
                "platform": _PLATFORM_PARAM,
                "campaign_id": {"type": "string", "description": "广告计划 ID"},
            },
            "required": ["platform", "campaign_id"],
        },
        handler=_ad_stats,
        category="ecommerce_ops",
    )

    registry.register(
        name="ecommerce_batch_ship",
        description="批量发货 (逐单处理，防限流)",
        parameters={
            "type": "object",
            "properties": {
                "platform": _PLATFORM_PARAM,
                "orders": {"type": "string", "description": "发货列表 JSON [{order_id, tracking_number, carrier}]"},
            },
            "required": ["platform", "orders"],
        },
        handler=_batch_ship,
        category="ecommerce_ops",
        requires_approval=True,
    )

    registry.register(
        name="ecommerce_daily_report",
        description="生成店铺日报 (流量/转化/营收趋势)",
        parameters={
            "type": "object",
            "properties": {"platform": _PLATFORM_PARAM},
            "required": ["platform"],
        },
        handler=_daily_report,
        category="ecommerce_ops",
    )

    logger.info("电商运营工具已注册: 24 个工具")
