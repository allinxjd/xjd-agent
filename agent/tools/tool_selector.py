"""意图→工具集映射 — 零 LLM 调用的关键词匹配器.

基于 toolset 动态过滤 + sandbox-tool-policy，
在发送给模型之前裁剪工具列表，减少 token 消耗和错误工具调用。

当无技能激活时，根据用户消息关键词选择相关 toolset，
返回 ~8-12 个工具而非全量 21 个。
"""

from __future__ import annotations

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

_INTENT_RULES: list[tuple[set[str], list[str], set[str]]] = [
    # (关键词集合, toolset 名列表, 额外单独工具)
    # 搜索/资讯
    (
        {"搜索", "查找", "新闻", "资讯", "search", "fetch", "网页", "url", "查询"},
        ["web"],
        set(),
    ),
    # 代码/终端
    (
        {"代码", "运行", "执行", "终端", "terminal", "code", "git", "编程",
         "debug", "脚本", "命令", "shell", "pip", "npm", "python"},
        ["code"],
        {"run_terminal", "execute_code"},
    ),
    # 画布/可视化
    (
        {"画布", "canvas", "知识图谱", "可视化", "导出", "export", "pdf", "png",
         "mermaid", "chart", "图表", "页面", "画册", "设计稿", "展示页", "产品页",
         "海报", "排版"},
        ["canvas"],
        set(),
    ),
    # 文件操作
    (
        {"文件", "读取", "写入", "file", "read", "write", "目录", "directory",
         "创建文件", "编辑文件", "删除"},
        [],
        {"read_file", "write_file", "edit_file", "list_directory", "grep_search"},
    ),
    # 图片/媒体
    (
        {"图片", "图像", "image", "语音", "tts", "截图", "screenshot",
         "电商图", "主图", "白底图", "详情图", "种草图"},
        ["media"],
        set(),
    ),
    # 数据
    (
        {"数据库", "database", "json", "pdf", "模板", "sql"},
        ["data"],
        set(),
    ),
    # 记忆
    (
        {"记忆", "memory", "记住", "忘记"},
        ["memory"],
        set(),
    ),
    # 电商运营 (卖家/商家)
    (
        {"店铺", "上架", "下架", "发货", "售后", "推广", "活动",
         "优惠券", "运营", "电商运营", "拼多多", "淘宝", "京东",
         "ecommerce", "shop", "product listing", "order fulfillment"},
        ["ecommerce_ops"],
        set(),
    ),
    # 消息/联系人
    (
        {"发消息", "发送消息", "联系人", "好友", "contact", "send_to", "转告", "通知",
         "发个消息", "发条消息", "告诉他", "告诉她", "帮我发", "消息", "发微信",
         "昵称", "备注", "nickname", "发给", "发表情"},
        [],
        {"send_to_contact", "list_contacts", "set_contact_nickname"},
    ),
]

_ALWAYS_INCLUDE: set[str] = {
    "request_user_approval",
    "read_file",
    "list_directory",
    "send_to_contact",
    "list_contacts",
    "set_contact_nickname",
}

_FACTUAL_KEYWORDS: set[str] = {
    "定时任务", "cron", "任务列表", "有哪些任务", "什么时候执行",
    "推送到哪", "配置", "设置了什么",
    "联系人列表", "有哪些联系人", "好友列表",
    "有哪些文件", "目录下有什么", "文件列表",
    "今天", "最新", "现在", "当前", "目前", "实时",
    "新闻", "资讯", "天气", "股价",
    "有几个", "有多少", "几点", "什么时候", "状态",
}

_FACTUAL_EXTRA_KEYWORDS: set[str] = {
    "帮我看看", "帮我查", "查一下", "看一下", "看看",
    "推送", "推送情况", "执行情况", "运行情况",
    "有没有", "是否有", "有什么",
    "怎么样", "什么情况", "啥情况",
    "多少钱", "价格", "库存", "销量", "订单",
    "设了啥", "设了什么", "哪些",
}

_FACTUAL_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"有没有.+"),
    re.compile(r".+怎么样"),
    re.compile(r"看看.+"),
    re.compile(r"查一下.+"),
    re.compile(r".+是什么情况"),
    re.compile(r"帮我.{0,4}(?:看|查|找|搜)"),
    re.compile(r".+有几个"),
    re.compile(r".+有多少"),
    re.compile(r".+列表"),
]

_FACTUAL_DOMAIN_KEYWORDS: set[str] = {
    "任务", "联系人", "文件", "搜索", "目录",
    "数据库", "订单", "商品", "产品", "店铺",
    "进程", "系统", "推送", "消息",
}

_QUESTION_MARKERS: set[str] = {
    "?", "？", "吗", "呢", "啥", "什么", "几", "多少", "怎", "哪",
}

_FACTUAL_TOOL_NAMES: frozenset[str] = frozenset({
    "web_search", "web_fetch", "list_directory", "read_file",
    "list_contacts", "database_query", "json_query", "grep_search",
    "system_info", "process_manager", "scheduled_task",
    "ecommerce_list_products", "ecommerce_list_orders",
    "ecommerce_shop_stats",
})

TOOL_DISPLAY_NAMES: dict[str, str] = {
    "scheduled_task": "定时任务管理",
    "list_contacts": "联系人列表",
    "send_to_contact": "消息发送",
    "web_search": "网络搜索",
    "web_fetch": "网页获取",
    "list_directory": "目录浏览",
    "read_file": "文件读取",
    "grep_search": "文件搜索",
    "database_query": "数据库查询",
    "json_query": "JSON查询",
    "system_info": "系统信息",
    "process_manager": "进程管理",
    "ecommerce_list_products": "商品列表",
    "ecommerce_list_orders": "订单列表",
    "ecommerce_shop_stats": "店铺统计",
}


def is_factual_query(message: str) -> bool:
    """检测是否为事实性查询（需要工具 grounding）— 三级检测."""
    msg = message.lower()
    if any(kw in msg for kw in _FACTUAL_KEYWORDS):
        return True
    if any(kw in msg for kw in _FACTUAL_EXTRA_KEYWORDS):
        return True
    if any(pat.search(msg) for pat in _FACTUAL_PATTERNS):
        return True
    if any(dk in msg for dk in _FACTUAL_DOMAIN_KEYWORDS):
        if any(q in msg for q in _QUESTION_MARKERS):
            return True
    return False


def is_factual_by_tools(tool_names: list[str]) -> bool:
    """Tier B: 如果模型调用了事实性工具，则该查询是事实性的."""
    return any(name in _FACTUAL_TOOL_NAMES for name in tool_names)


_FEEDBACK_PATTERNS: set[str] = {
    "看不到", "不行", "还是不", "没有反应", "没反应", "出错", "找不到",
    "不对", "不工作", "失败", "报错", "有问题", "不能用", "不显示",
    "打不开", "无法", "错误", "不生效", "没效果", "白屏", "空白",
    "答非所问", "不是我要的",
}


def select_tool_names_for_message(message: str) -> Optional[set[str]]:
    """根据用户消息关键词选择相关工具名集合.

    Returns:
        匹配到 → 返回工具名集合
        匹配 0 个或 ≥4 个意图（模糊）→ 返回 None（fallback 到全量）
        检测到反馈/投诉模式 → 返回 None（让模型自由回复）
    """
    from agent.tools.registry import TOOLSETS

    msg_lower = message.lower()

    if any(pat in msg_lower for pat in _FEEDBACK_PATTERNS):
        logger.debug("Tool selector: feedback pattern detected, returning full toolset")
        return None
    matched_toolsets: set[str] = set()
    extra_tools: set[str] = set()

    for keywords, toolset_names, extra in _INTENT_RULES:
        if any(kw in msg_lower for kw in keywords):
            matched_toolsets.update(toolset_names)
            extra_tools.update(extra)

    if not matched_toolsets and not extra_tools:
        return None
    if len(matched_toolsets) >= 4:
        return None

    names: set[str] = set(_ALWAYS_INCLUDE) | extra_tools
    for ts_name in matched_toolsets:
        ts = TOOLSETS.get(ts_name)
        if ts:
            names.update(ts)

    logger.debug(
        "Tool selector: matched toolsets=%s, total tools=%d",
        sorted(matched_toolsets), len(names),
    )
    return names
