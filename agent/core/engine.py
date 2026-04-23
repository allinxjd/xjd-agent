"""Agent 主循环 — tool calling loop.

- 接收用户消息 → 调用模型 → 解析 tool calls → 执行工具 → 再次调用模型 → 直到完成
- 支持流式输出
- 上下文管理 (自动压缩)
- 记忆注入 (每轮预取)
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Optional

from agent.core.model_router import ModelRouter
from agent.providers.base import (
    CompletionResponse,
    Message,
    StreamChunk,
    ToolDefinition,
    Usage,
)

logger = logging.getLogger(__name__)

# 默认 system prompt
DEFAULT_SYSTEM_PROMPT = """你是小巨蛋智能体 (XJD Agent)，一个强大的个人 AI 助手。

你有以下核心能力:
1. 使用工具完成任务 (终端命令、文件操作、网页搜索、代码执行等)
2. 跨平台消息互通 (微信/飞书/钉钉/Telegram/Discord/Slack 等 30+ 平台)
3. 从经验中学习 — 将成功的任务流程保存为可复用的技能
4. 持久记忆 — 记住用户的偏好和重要信息
5. 定时任务 — 用自然语言设置周期性自动化

行为准则:
- 简单的问候、闲聊、知识问答，直接用文字回复，不要调用工具
- 只在用户明确要求执行操作（文件操作、搜索、代码执行、生成图片、发消息给某人等）时才使用工具
- 用户要求发消息、转告、通知某人时，必须立即调用 list_contacts 查看联系人，然后用 send_to_contact 发送，不要反问用户要联系方式
- 如果不确定是否需要工具，先直接回答，用户会告诉你是否需要进一步操作
- 每轮工具调用后，如果已经获得足够信息，立即给出最终回复，不要继续调用工具
- 任务完成后，考虑是否值得保存为技能
- 危险操作 (删除文件、执行脚本) 需要用户确认
- 绝对禁止关闭、重启、kill 用户的浏览器进程 (Chrome/Firefox/Safari)，不要执行 pkill/killall/osascript quit 等命令，不要建议用户关闭浏览器，不要建议用户以调试模式重启浏览器，不要检测 CDP 端口。浏览器连接由系统自动处理，CDP 连不上会自动用内置 Chromium
- 回复简洁清晰，代码用 markdown 格式
- 你的回复会自动发送到用户所在的渠道（微信/飞书/WebUI 等），不需要手动推送。直接输出最终内容，不要包含"我无法推送"、"请手动复制"、"需要推送工具"等说明
- 定时任务和 cron 触发时，你的回复也会自动发送到目标渠道，只需输出内容本身
- 生成 HTML 页面、产品页、展示页、图表、流程图时，必须使用 create_canvas 工具渲染到 Canvas 面板，不要在聊天框直接输出 HTML 代码
  - type 可选: html / markdown / mermaid / chart / react
  - 用户能在 Canvas 面板实时预览效果
- Canvas 导出: 用户要求导出 Canvas 为 PDF/PNG/HTML 文件时，必须使用 export_canvas 工具，不要用 run_terminal 手动操作
  - 先用 list_canvas 查找已有 Canvas 的 artifact_id
  - 再用 export_canvas 导出，文件会保存到 ~/.xjd-agent/exports/ 目录
- 知识可视化: 用户要求展示知识图谱、记忆网络、学习曲线时，使用 show_knowledge_canvas 工具

环境信息:
- 当前系统: macOS
- 中文字体路径 (PIL/Pillow 生成图片时必须使用以下字体，否则中文无法显示):
  - "/System/Library/Fonts/STHeiti Medium.ttc" (黑体，推荐)
  - "/System/Library/Fonts/Hiragino Sans GB.ttc" (冬青黑体)
  - "/Library/Fonts/Arial Unicode.ttf" (Arial Unicode)
  注意: 此系统没有 PingFang.ttc，不要使用。Helvetica 不支持中文，不要用于中文文本。
  注意: PIL/Pillow 无法渲染 emoji 图标，生成图片时用文字序号或符号(●▶★→)替代 emoji。

- 电商图片生成 (重要):
  - 任何需要生成产品图、电商图、种草图、主图、白底图、详情图的需求，必须使用 generate_ecommerce_image 工具
  - 绝对禁止使用 Python/PIL/Pillow 脚本生成电商图片，PIL 做出来的图质量太差不能用
  - 绝对禁止用 execute_code 或 run_terminal 来生成图片，必须调用 generate_ecommerce_image 工具
  - 如果用户没有提供参考图片，先问用户要参考图片路径，不要自己用代码画图
  - 支持平台: taobao/jd/xiaohongshu/douyin/pdd/dewu/tiktok/ali1688
  - 图片类型: main(主图)/single(白底图)/detail(详情图)
  - 参数 reference_image 必填，是本地图片文件路径

- 主动发消息 (重要):
  - 用户要求给某人发消息、转告、通知时，使用 send_to_contact 工具
  - 先用 list_contacts 查看已知联系人列表，列表包含 user_id 和 nickname
  - 如果联系人有 nickname，按 nickname 匹配用户提到的名字
  - 如果联系人没有 nickname 但你知道对方是谁，用 set_contact_nickname 设置昵称方便下次查找
  - send_to_contact 需要 user_id 和 text 参数
  - 只能给曾经发过消息的联系人发送（需要对方先发过消息建立联系）
"""

@dataclass
class ToolHandler:
    """已注册的工具处理器."""

    definition: ToolDefinition
    handler: Callable[..., Any]  # async function(args) -> str
    requires_approval: bool = False

@dataclass
class TurnResult:
    """单轮对话结果."""

    content: str = ""
    thinking: Optional[str] = None
    tool_calls_made: int = 0
    total_usage: Usage = field(default_factory=Usage)
    duration_ms: float = 0.0

class AgentEngine:
    """Agent 核心引擎.

    用法:
        engine = AgentEngine(router=model_router)
        engine.register_tool(terminal_tool)

        result = await engine.run_turn("帮我查看当前目录")
        print(result.content)
    """

    def __init__(
        self,
        router: ModelRouter,
        system_prompt: Optional[str] = None,
        max_tool_rounds: int = 15,
        max_context_tokens: int = 100_000,
        memory_manager: Optional[Any] = None,
        skill_manager: Optional[Any] = None,
        learning_loop: Optional[Any] = None,
        registry: Optional[Any] = None,
        pin_manager: Optional[Any] = None,
    ) -> None:
        self._router = router
        self._system_prompt = system_prompt or DEFAULT_SYSTEM_PROMPT
        self._max_tool_rounds = max_tool_rounds
        self._max_context_tokens = max_context_tokens
        self._pin_manager = pin_manager

        # 外部工具注册表 (ToolRegistry)
        self._registry = registry

        # 工具注册表 (内部，兼容旧代码)
        self._tools: dict[str, ToolHandler] = {}

        # 对话历史
        self._messages: list[Message] = []

        # 学习系统
        self._memory_manager = memory_manager
        self._skill_manager = skill_manager
        self._learning_loop = learning_loop

        # 统计
        self._total_usage = Usage()
        self._turn_count = 0

        # 技能沙箱: 当前激活的技能 (用于工具权限检查)
        self._active_skill: Optional[Any] = None

    @property
    def messages(self) -> list[Message]:
        return self._messages

    @messages.setter
    def messages(self, value: list[Message]) -> None:
        self._messages = value

    @property
    def tool_definitions(self) -> list[ToolDefinition]:
        """返回默认工具定义 (core + skills 全集).

        外部调用 (如 admin API) 使用此属性获取完整工具列表。
        run_turn() 内部使用 _resolve_scoped_tools() 获取作用域裁剪后的列表。
        """
        if hasattr(self, '_registry') and self._registry:
            defs = self._registry.compose_toolsets("core", "skills")
            if defs:
                return defs
        return [t.definition for t in self._tools.values()]

    def _resolve_scoped_tools(
        self,
        active_skill: Optional[Any] = None,
        scoped_names: Optional[set[str]] = None,
    ) -> list[ToolDefinition]:
        """解析作用域工具定义 (三层优先级, 线程安全).

        Layer 1: 技能作用域 — 技能声明了 tools 白名单时，只返回这些工具
        Layer 2: 意图作用域 — 无技能时，按用户消息意图返回相关工具
        Layer 3: 全量回退 — 意图模糊时返回 core + skills 全集
        """
        if not (hasattr(self, '_registry') and self._registry):
            return [t.definition for t in self._tools.values()]

        # Layer 1: 技能作用域
        if active_skill:
            allowed = getattr(active_skill, 'tools', None)
            if allowed:
                defs = self._registry.get_definitions_by_names(allowed)
                if defs:
                    logger.info("Tool scope: skill '%s' → %d tools: %s",
                                active_skill.name, len(defs),
                                [d.name for d in defs])
                    return defs
                logger.warning("Tool scope: skill '%s' declared tools %s but none found in registry, fallback",
                               active_skill.name, allowed)

        # Layer 2: 意图作用域
        if scoped_names:
            defs = self._registry.get_definitions_by_names(list(scoped_names))
            if defs:
                logger.info("Tool scope: intent → %d tools: %s",
                            len(defs), [d.name for d in defs])
                return defs

        # Layer 3: 全量回退
        defs = self._registry.compose_toolsets("core", "skills")
        if defs:
            return defs
        return [t.definition for t in self._tools.values()]

    def register_tool(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
        handler: Callable[..., Any],
        requires_approval: bool = False,
    ) -> None:
        """注册工具."""
        self._tools[name] = ToolHandler(
            definition=ToolDefinition(
                name=name,
                description=description,
                parameters=parameters,
            ),
            handler=handler,
            requires_approval=requires_approval,
        )
        logger.info("Registered tool: %s", name)

    def set_system_prompt(self, prompt: str) -> None:
        """更新 system prompt."""
        self._system_prompt = prompt

    def add_context(self, context: str) -> None:
        """向 system prompt 追加上下文 (如记忆、技能提示)."""
        self._system_prompt += f"\n\n{context}"

    def reset(self) -> None:
        """重置对话."""
        self._messages = []
        self._turn_count = 0

    async def _execute_tool(self, name: str, arguments: str) -> str:
        """执行工具调用 (带超时保护 + 技能沙箱)."""
        # ── 技能沙箱: 检查工具是否在技能声明的白名单中 ──
        if self._active_skill:
            allowed = getattr(self._active_skill, 'tools', None)
            if allowed and name not in allowed:
                logger.warning(
                    "Skill sandbox blocked: skill '%s' tried to use undeclared tool '%s'",
                    self._active_skill.name, name,
                )
                declared = ', '.join(allowed)
                return (
                    f"Error: 技能「{self._active_skill.name}」未声明工具 '{name}'，调用被拦截。"
                    f"已声明工具: {declared}"
                )
        # 优先使用 ToolRegistry (有超时 + 重试 + 统计)
        if self._registry:
            tool = self._registry.get(name)
            if tool:
                try:
                    args = json.loads(arguments) if arguments else {}
                except json.JSONDecodeError:
                    return f"Error: Invalid JSON arguments for tool '{name}'"
                # 自动注入 skill_id: 技能激活时创建定时任务，绑定当前技能
                if name == "scheduled_task" and args.get("action") == "add" and self._active_skill:
                    args.setdefault("skill_id", self._active_skill.skill_id)
                return await self._registry.execute(name, args)

        # 回退: 内部工具表
        tool = self._tools.get(name)
        if not tool:
            return f"Error: Unknown tool '{name}'"

        try:
            args = json.loads(arguments) if arguments else {}
        except json.JSONDecodeError:
            return f"Error: Invalid JSON arguments for tool '{name}'"

        try:
            result = tool.handler(**args)
            if hasattr(result, "__await__"):
                result = await asyncio.wait_for(result, timeout=60.0)
            return str(result) if result is not None else "OK"
        except asyncio.TimeoutError:
            logger.warning("Tool %s timed out after 60s", name)
            return f"Error: Tool '{name}' timed out"
        except Exception as e:
            logger.error("Tool %s failed: %s", name, e, exc_info=True)
            return f"Error executing {name}: {e}"

    async def run_turn(
        self,
        user_message: str,
        on_stream: Optional[Callable[[str], None]] = None,
        on_thinking: Optional[Callable[[str], None]] = None,
        on_tool_call: Optional[Callable[[str, str], None]] = None,
        on_tool_result: Optional[Callable[[str, str], None]] = None,
        on_llm_event: Optional[Callable[[str, dict], None]] = None,
        temperature: float = 0.7,
        thinking: Optional[str] = None,
        session_messages: Optional[list[Message]] = None,
        abort_check: Optional[Callable[[], bool]] = None,
        skill_id: Optional[str] = None,
        deadline: Optional[float] = None,
    ) -> TurnResult:
        """执行一轮对话 (包含完整的 tool calling loop).

        Args:
            user_message: 用户输入
            on_stream: 流式文本回调
            on_thinking: 思考过程回调
            on_tool_call: 工具调用回调 (name, args)
            on_tool_result: 工具结果回调 (name, result)
            on_llm_event: Inspector 事件回调 (event_type, detail_dict)
            temperature: 温度
            thinking: 思考级别 ("off"|"low"|"medium"|"high")
            session_messages: 外部 session 消息历史 (Gateway 模式)，传入时不操作 self._messages
            abort_check: 中止检查回调
            skill_id: 直接指定技能 ID (跳过技能匹配，用于 cron 任务等场景)

        Returns:
            TurnResult: 本轮结果
        """
        start_time = time.time()
        self._turn_count += 1
        total_tool_calls = 0
        total_usage = Usage()

        use_session = session_messages is not None
        messages = session_messages if use_session else self._messages

        # 直接指定技能 (cron 任务等场景，跳过匹配)
        forced_skill = None
        if skill_id and self._skill_manager:
            try:
                forced_skill = await self._skill_manager.get_skill(skill_id)
            except Exception:
                pass

        # 注入学习上下文 (记忆 + 技能匹配)
        injected = None
        if self._learning_loop and not forced_skill:
            try:
                injected = await self._learning_loop.inject_context(
                    user_message=user_message,
                    user_id="default",
                    model_router=self._router,
                )
            except Exception as e:
                logger.debug("Learning context injection failed: %s", e)

        # 记忆 + 技能概览 → system prompt (稳定前缀，prompt cache 友好)
        system_content = self._system_prompt
        if injected and injected.system_context:
            system_content += "\n" + injected.system_context

        # 匹配到的技能 → user message 前缀 (按需注入，不破坏 cache)
        actual_user_message = user_message
        if forced_skill:
            skill_content = forced_skill.to_full_content()
            if skill_content:
                actual_user_message = (
                    f"[已激活技能]\n{skill_content}\n\n"
                    f"[用户消息]\n{user_message}"
                )
        elif injected and injected.skill_message:
            actual_user_message = (
                f"[已激活技能]\n{injected.skill_message}\n\n"
                f"[用户消息]\n{user_message}"
            )

        # Pipeline 技能 → 提升工具轮次上限 + 设置沙箱上下文
        effective_max_rounds = self._max_tool_rounds
        self._active_skill = None
        if forced_skill:
            self._active_skill = forced_skill
            logger.info("Forced skill: %s (via skill_id)", forced_skill.name)
            if "pipeline" in (forced_skill.tags or []):
                effective_max_rounds = max(self._max_tool_rounds, 20)
        elif injected and injected.matched_skill_id and self._skill_manager:
            try:
                _matched = await self._skill_manager.get_skill(injected.matched_skill_id)
                if _matched:
                    self._active_skill = _matched
                    if "pipeline" in (_matched.tags or []):
                        effective_max_rounds = max(self._max_tool_rounds, 20)
            except Exception:
                pass

        # 工具作用域: 一次性计算本轮工具列表 (局部变量, 并发安全)
        scoped_names = None
        if not self._active_skill:
            from agent.tools.tool_selector import select_tool_names_for_message
            scoped_names = select_tool_names_for_message(user_message)
        turn_tools = self._resolve_scoped_tools(self._active_skill, scoped_names) or None

        # 意图明确且工具少时，第一轮强制调用工具 (防止模型跳过)
        force_tool_round0 = bool(
            scoped_names
            and turn_tools
            and len(turn_tools) <= 8
            and any(t.name in ("send_to_contact", "generate_ecommerce_image") for t in turn_tools)
        )

        # cron 执行时排除 scheduled_task，防止模型在定时任务中创建新的定时任务
        if skill_id and turn_tools:
            turn_tools = [t for t in turn_tools if t.name != "scheduled_task"]

        # 添加用户消息
        messages.append(Message(role="user", content=actual_user_message))

        full_messages = [Message(role="system", content=system_content)] + messages

        # Agentic loop — 无硬性轮次上限，跑到模型返回最终回复为止
        # 安全兜底: deadline (外层 timeout) + token 上限 + 绝对轮次上限
        max_safety_rounds = 50
        round_idx = 0
        while round_idx < max_safety_rounds:
            if abort_check and abort_check():
                logger.info("run_turn aborted at round %d (client disconnected)", round_idx)
                final = "连接已断开，任务中止。"
                messages.append(Message(role="assistant", content=final))
                self._active_skill = None
                return TurnResult(
                    content=final,
                    tool_calls_made=total_tool_calls,
                    total_usage=total_usage,
                    duration_ms=(time.time() - start_time) * 1000,
                )
            if deadline and time.time() > deadline:
                logger.warning("run_turn deadline exceeded at round %d", round_idx)
                final = "处理超时，已返回当前结果。"
                messages.append(Message(role="assistant", content=final))
                self._active_skill = None
                return TurnResult(
                    content=final,
                    tool_calls_made=total_tool_calls,
                    total_usage=total_usage,
                    duration_ms=(time.time() - start_time) * 1000,
                )
            # 调用模型
            _llm_start = time.time()
            if on_llm_event:
                on_llm_event("request", {
                    "messages_count": len(full_messages),
                    "has_tools": bool(turn_tools),
                    "round": round_idx,
                })
            response = await self._router.complete_with_failover(
                messages=full_messages,
                user_message=user_message if round_idx == 0 else "",
                tools=turn_tools,
                temperature=temperature,
                thinking=thinking,
                tool_choice="required" if (round_idx == 0 and force_tool_round0) else None,
            )

            # Validate tool_calls structure
            if not isinstance(response.tool_calls, (list, type(None))):
                response.tool_calls = None

            # 累加用量
            total_usage.prompt_tokens += response.usage.prompt_tokens
            total_usage.completion_tokens += response.usage.completion_tokens
            total_usage.total_tokens += response.usage.total_tokens

            if on_llm_event:
                on_llm_event("response", {
                    "prompt_tokens": response.usage.prompt_tokens,
                    "completion_tokens": response.usage.completion_tokens,
                    "has_tool_calls": bool(response.tool_calls),
                    "duration_ms": round((time.time() - _llm_start) * 1000),
                    "round": round_idx,
                })

            # 思考过程
            if response.thinking and on_thinking:
                on_thinking(response.thinking)

            # 没有 tool calls → 最终回复
            if not response.tool_calls:
                if response.content and on_stream:
                    on_stream(response.content)

                # 保存 assistant 回复
                messages.append(
                    Message(role="assistant", content=response.content)
                )

                turn_result = TurnResult(
                    content=response.content,
                    thinking=response.thinking,
                    tool_calls_made=total_tool_calls,
                    total_usage=total_usage,
                    duration_ms=(time.time() - start_time) * 1000,
                )

                # 触发学习闭环
                if self._learning_loop:
                    try:
                        await asyncio.wait_for(
                            self._learning_loop.on_turn_complete(
                                messages=[m.__dict__ if hasattr(m, '__dict__') else m for m in messages],
                                result=turn_result,
                                user_id="default",
                                model_router=self._router,
                                matched_skill_id=getattr(injected, "matched_skill_id", "") if injected else "",
                            ),
                            timeout=10.0,
                        )
                    except asyncio.TimeoutError:
                        logger.debug("Learning loop callback timed out")
                    except Exception as e:
                        logger.debug("Learning loop callback failed: %s", e)

                # 有用性反馈 — 记录注入的记忆是否有帮助
                if (
                    injected
                    and injected.injected_memory_ids
                    and self._memory_manager
                ):
                    try:
                        is_success = bool(
                            turn_result.content
                            and not any(
                                kw in turn_result.content.lower()
                                for kw in ("error:", "failed:", "错误:", "失败:")
                            )
                        )
                        await asyncio.wait_for(
                            self._memory_manager.record_feedback(
                                memory_ids=injected.injected_memory_ids,
                                signal="positive" if is_success else "negative",
                            ),
                            timeout=5.0,
                        )
                    except asyncio.TimeoutError:
                        logger.debug("Memory feedback recording timed out")
                    except Exception as e:
                        logger.debug("Memory feedback recording failed: %s", e)

                self._active_skill = None
                return turn_result


            tool_names = [tc["function"]["name"] for tc in response.tool_calls]
            logger.info("Round %d tool calls: %s", round_idx + 1, tool_names)
            # 如果模型在 tool call 同时输出了文本，也发给前端
            if response.content and on_stream:
                on_stream(response.content)

            # 保存 assistant 的 tool_calls 消息
            messages.append(
                Message(
                    role="assistant",
                    content=response.content,
                    tool_calls=response.tool_calls,
                )
            )
            full_messages.append(
                Message(
                    role="assistant",
                    content=response.content,
                    tool_calls=response.tool_calls,
                )
            )

            # 并行执行所有 tool calls (独立工具可并发)
            async def _run_one_tool(tc):
                func_name = tc["function"]["name"]
                func_args = tc["function"]["arguments"]
                if on_tool_call:
                    on_tool_call(func_name, func_args)
                result = await self._execute_tool(func_name, func_args)
                if on_tool_result:
                    on_tool_result(func_name, result)
                return tc, result

            tool_results = await asyncio.gather(
                *[_run_one_tool(tc) for tc in response.tool_calls],
                return_exceptions=True,
            )

            for i, item in enumerate(tool_results):
                if isinstance(item, Exception):
                    logger.error("Tool execution exception: %s", item)
                    # 必须返回 tool message，否则 tool_call/tool_result 不匹配
                    tc = response.tool_calls[i]
                    tool_msg = Message(
                        role="tool",
                        content=f"Error: {item}",
                        tool_call_id=tc["id"],
                    )
                    messages.append(tool_msg)
                    full_messages.append(tool_msg)
                    total_tool_calls += 1
                    continue
                tc, result = item
                total_tool_calls += 1
                tool_msg = Message(
                    role="tool",
                    content=result,
                    tool_call_id=tc["id"],
                )
                messages.append(tool_msg)
                full_messages.append(tool_msg)

            round_idx += 1

        # 安全上限 (正常不应到达 — 模型会在任务完成时停止调用工具)
        final_content = "已达到安全轮次上限，当前进度已保存。如需继续，请再次发送指令。"
        messages.append(Message(role="assistant", content=final_content))
        self._active_skill = None

        return TurnResult(
            content=final_content,
            tool_calls_made=total_tool_calls,
            total_usage=total_usage,
            duration_ms=(time.time() - start_time) * 1000,
        )
