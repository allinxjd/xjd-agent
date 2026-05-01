"""Company — 团队编排主循环."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from agent.company.action import USER_REQUIREMENT, EVALUATE_REQUIREMENT
from agent.company.environment import CompanyEnvironment
from agent.company.feishu_bridge import FeishuBotConfig, FeishuBridge
from agent.company.memory import CompanyMemory
from agent.company.message import CompanyMessage
from agent.company.role import CompanyRole
from agent.company.store import CompanyStore
from agent.company.task import CompanyTask
from agent.core.config import get_projects_dir

logger = logging.getLogger(__name__)

KARPATHY_SKILLS_DIR = Path(__file__).resolve().parent.parent.parent / "skills"
KARPATHY_SKILL_NAMES = [
    "karpathy-think-before-coding",
    "karpathy-simplicity-first",
    "karpathy-surgical-changes",
    "karpathy-goal-driven",
]


def _load_karpathy_guidelines() -> str:
    """从 skills/ 目录加载 Karpathy 四原则，拼接为 system prompt 片段."""
    parts = []
    for name in KARPATHY_SKILL_NAMES:
        skill_file = KARPATHY_SKILLS_DIR / name / "SKILL.md"
        if not skill_file.exists():
            continue
        text = skill_file.read_text(encoding="utf-8")
        sep = text.find("---", 3)
        if sep > 0:
            body = text[sep + 3:].strip()
        else:
            body = text
        parts.append(body)
    if not parts:
        return ""
    return "## Karpathy 行为准则\n\n" + "\n\n---\n\n".join(parts)


class Company:
    """多角色编排器."""

    def __init__(
        self,
        router: Any,
        tool_registry: Any = None,
        memory_manager: Any = None,
        feishu_chat_id: str = "",
        feishu_bots: Optional[list[FeishuBotConfig]] = None,
    ) -> None:
        self._env = CompanyEnvironment()
        self._router = router
        self._registry = tool_registry
        self._memory = memory_manager
        self._tasks: dict[str, CompanyTask] = {}
        self._karpathy_prompt = _load_karpathy_guidelines()
        self._feishu_bridge: Optional[FeishuBridge] = None
        self._store = CompanyStore()
        self._store.open()
        self._shared_memory = CompanyMemory(memory_manager)
        self._pipeline_running = False
        self._pipeline_user_msgs: list[CompanyMessage] = []

        if feishu_chat_id and feishu_bots:
            self._feishu_bridge = FeishuBridge(
                group_chat_id=feishu_chat_id,
                bot_configs=feishu_bots,
                environment=self._env,
            )
            self._env._feishu_bridge = self._feishu_bridge

    @property
    def environment(self) -> CompanyEnvironment:
        return self._env

    def hire(self, role: CompanyRole) -> None:
        """注册角色到环境，注入运行时依赖和 Karpathy 准则."""
        role._runtime_router = self._router
        role._runtime_registry = self._registry
        if self._karpathy_prompt and self._karpathy_prompt not in (role.system_prompt or ""):
            role.system_prompt = (role.system_prompt or "") + "\n\n" + self._karpathy_prompt
        self._env.add_role(role)

    def hire_team(self, roles: list[CompanyRole]) -> None:
        for role in roles:
            self.hire(role)

    async def start_feishu(self) -> None:
        """启动飞书桥接（如果已配置）."""
        if self._feishu_bridge:
            await self._feishu_bridge.start()

    async def stop_feishu(self) -> None:
        """停止飞书桥接."""
        if self._feishu_bridge:
            await self._feishu_bridge.stop()

    async def assign(self, task: CompanyTask) -> None:
        """分配任务，发布触发消息."""
        self._tasks[task.task_id] = task
        task.status = "in_progress"
        self._store.save_task(task)

        msg = CompanyMessage(
            content=task.description,
            cause_by=USER_REQUIREMENT.name,
            sent_from="System",
            task_id=task.task_id,
        )
        if task.assigned_to:
            msg.send_to = task.assigned_to

        await self._env.publish(msg)
        self._store.save_message(msg)

    _REQUIREMENT_ISSUE_INDICATORS = [
        "需求不清", "需求缺失", "需求为空", "没有需求", "需求有问题",
        "设计与需求下面是空", "没提供需求", "没有 diff", "没有可审查",
        "交白卷", "无从审起", "没有变更集",
        "安全轮次上限", "如需继续",
    ]

    def _has_requirement_issue(self, content: str) -> bool:
        """检测角色输出是否表示需求/输入有问题."""
        short = content[:500]
        return any(ind in short for ind in self._REQUIREMENT_ISSUE_INDICATORS)

    _NO_WORK_INDICATORS = [
        "没有需求", "没有任务", "没需求", "没任务", "nothing to do",
        "没有新的", "没有待处理", "无需操作", "无需部署",
        "没有代码变更", "没有变更", "没有改动",
        "部署完成（无需操作）",
    ]

    def _is_no_work(self, content: str) -> bool:
        """检测角色输出是否表示无实际工作可做。长内容不可能是空转。"""
        if len(content) > 300:
            return False
        short = content[:200]
        return any(ind in short for ind in self._NO_WORK_INDICATORS)

    def _is_review_approved(self, content: str) -> bool:
        upper = content.upper()
        return "APPROVED" in upper and "REJECTED" not in upper

    def _is_test_passed(self, content: str) -> bool:
        indicators = ["全部通过", "测试通过", "PASS", "pass", "通过率 100", "没毛病", "稳了"]
        fail_indicators = ["失败", "FAIL", "fail", "不通过", "未通过", "error", "Error"]
        has_pass = any(ind in content for ind in indicators)
        has_fail = any(ind in content for ind in fail_indicators)
        return has_pass and not has_fail

    @staticmethod
    def _extract_workspace_from_requirement(requirement: str) -> Optional[Path]:
        """从需求文本中提取项目工作目录路径."""
        import re
        match = re.search(r"## 项目工作目录\n(.+)\n", requirement)
        if match:
            p = Path(match.group(1).strip())
            if p.exists():
                return p
        return None

    @staticmethod
    def _collect_project_files(project_dir: Path, max_chars: int = 8000) -> str:
        """收集项目 src/ 目录下的所有代码文件内容，用于 Reviewer 审查."""
        src_dir = project_dir / "src"
        if not src_dir.exists():
            src_dir = project_dir
        files_content = []
        total = 0
        for f in sorted(src_dir.rglob("*")):
            if not f.is_file():
                continue
            if f.suffix in (".pyc", ".class", ".o", ".so", ".db", ".sqlite"):
                continue
            if f.name.startswith("."):
                continue
            try:
                text = f.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            rel = f.relative_to(project_dir)
            entry = f"\n### {rel}\n```\n{text}\n```\n"
            if total + len(entry) > max_chars:
                files_content.append(f"\n... (更多文件省略，共 {total} 字符)")
                break
            files_content.append(entry)
            total += len(entry)
        return "".join(files_content) if files_content else ""

    async def run(self, requirement: str, max_rounds: int = 20) -> str:
        """主循环：发布需求 → 角色轮转 → 直到空闲或达到上限."""
        import uuid
        run_id = uuid.uuid4().hex[:8]

        req_for_display = requirement
        if "## 用户最新需求\n" in requirement:
            req_for_display = requirement.split("## 用户最新需求\n", 1)[1].split("\n\n")[0].strip()
        elif "## 项目工作目录\n" in requirement:
            parts = requirement.split("\n\n", 2)
            req_for_display = parts[-1][:200] if len(parts) > 2 else requirement[:200]
        req_for_display = req_for_display[:200]

        task = CompanyTask(
            title=req_for_display[:60],
            description=requirement,
        )
        await self.assign(task)
        self._store.save_run(run_id, req_for_display, task.task_id)

        round_num = 0
        rework_counts: dict[str, int] = {}
        max_rework = 3
        stages_done: dict[str, bool] = {
            "PRD": False, "Design": False, "Code": False,
            "Review": False, "Test": False, "Deploy": False,
        }
        idle_rounds = 0
        supplement_injected: set[str] = set()

        for round_num in range(1, max_rounds + 1):
            if self._pipeline_user_msgs:
                new_msgs = [m for m in self._pipeline_user_msgs
                            if m.content not in supplement_injected]
                if new_msgs:
                    supplement = "\n".join(m.content for m in new_msgs)
                    for m in new_msgs:
                        supplement_injected.add(m.content)
                    inject_msg = CompanyMessage(
                        content=f"## 老板补充需求\n{supplement}",
                        cause_by="HumanDirective",
                        sent_from="Human",
                        task_id=task.task_id,
                    )
                    await self._env.publish(inject_msg)
                    logger.info("已注入用户补充需求到 pipeline")
                self._pipeline_user_msgs.clear()

            if self._env.is_idle():
                logger.info("所有角色空闲，结束 (round %d)", round_num)
                break

            if all(stages_done.values()):
                logger.info("所有阶段已完成，pipeline 结束 (round %d)", round_num)
                break

            logger.info("=== Round %d === stages=%s", round_num, stages_done)
            round_had_work = False
            for role in self._env.roles.values():
                if not role.has_pending:
                    continue
                status_msg = CompanyMessage(
                    content=f"{role.name} 开始工作...",
                    cause_by="StatusUpdate",
                    sent_from=role.name,
                    task_id=task.task_id,
                )
                await self._env.publish(status_msg)
                result_msg = await role.run()
                if not result_msg:
                    continue

                result_msg.task_id = task.task_id
                task.result = result_msg.content
                self._store.save_message(result_msg)

                if self._is_no_work(result_msg.content):
                    logger.info("[%s] 无实际工作，跳过", role.name)
                    continue

                round_had_work = True

                if result_msg.cause_by == "WritePRD":
                    stages_done["PRD"] = True
                elif result_msg.cause_by == "WriteDesign":
                    stages_done["PRD"] = True
                    stages_done["Design"] = True
                elif result_msg.cause_by == "WriteCode":
                    if self._has_requirement_issue(result_msg.content):
                        logger.warning("[Developer] 输出有需求问题，回退给 PM 核实")
                        self._clear_downstream_inboxes(role.name)
                        escalate = CompanyMessage(
                            content=(
                                f"## Developer 反馈需求问题\n{result_msg.content[:1000]}\n\n"
                                "请检查对话记录，确认需求是否清晰。如果需求没有跟老板确认过，"
                                "请在群里向老板核实后，重新整理需求和设计方案给 Developer。"
                            ),
                            cause_by="WriteCode",
                            sent_from="Developer",
                            send_to="PM",
                            task_id=task.task_id,
                        )
                        await self._env.publish(escalate)
                        continue
                    stages_done["Code"] = True
                    workspace = self._extract_workspace_from_requirement(requirement)
                    if workspace:
                        code_listing = self._collect_project_files(workspace)
                        if code_listing:
                            result_msg.content += f"\n\n## 代码文件内容\n{code_listing}"
                            logger.info("已附加项目代码文件到 WriteCode 输出 (%d 字符)", len(code_listing))
                elif result_msg.cause_by == "CodeReview":
                    if self._has_requirement_issue(result_msg.content):
                        logger.warning("[Reviewer] 输出表示输入有问题，回退给 PM")
                        self._clear_downstream_inboxes(role.name)
                        escalate = CompanyMessage(
                            content=(
                                f"## Reviewer 反馈输入问题\n{result_msg.content[:1000]}\n\n"
                                "Reviewer 无法审查，因为收到的代码变更内容不完整。"
                                "请检查 Developer 的产出是否正常，必要时重新安排开发。"
                            ),
                            cause_by="CodeReview",
                            sent_from="Reviewer",
                            send_to="PM",
                            task_id=task.task_id,
                        )
                        await self._env.publish(escalate)
                        stages_done["Code"] = False
                        continue
                    if self._is_review_approved(result_msg.content):
                        stages_done["Review"] = True
                elif result_msg.cause_by in ("WriteTest", "RunTest"):
                    if result_msg.cause_by == "RunTest" and self._is_test_passed(result_msg.content):
                        stages_done["Test"] = True
                elif result_msg.cause_by in ("DeployPlan", "ExecuteDeploy"):
                    if result_msg.cause_by == "ExecuteDeploy":
                        stages_done["Deploy"] = True

                rework_target = self._check_rework(role.name, result_msg.content)
                role_rework = rework_counts.get(role.name, 0)
                if rework_target and role_rework < max_rework:
                    rework_counts[role.name] = role_rework + 1
                    logger.info("返工 #%d: %s 要求 %s 修改", role_rework + 1, role.name, rework_target)
                    stages_done["Code"] = False
                    stages_done["Review"] = False
                    stages_done["Test"] = False
                    self._clear_downstream_inboxes(role.name)
                    rework_msg = CompanyMessage(
                        content=f"## {role.name} 反馈（请修改后重新提交）\n{result_msg.content}",
                        cause_by=result_msg.cause_by,
                        sent_from=role.name,
                        send_to=rework_target,
                        task_id=task.task_id,
                    )
                    await self._env.publish(rework_msg)
                    self._store.save_message(rework_msg)
                    rework_status = CompanyMessage(
                        content=f"{role.name} 打回了代码，{rework_target} 正在修改（第 {role_rework + 1} 次返工）",
                        cause_by="StatusUpdate",
                        sent_from=role.name,
                        task_id=task.task_id,
                    )
                    await self._env.publish(rework_status)
                elif rework_target and role_rework >= max_rework:
                    logger.warning("返工次数已达上限 %d，强制通过 %s 阶段", max_rework, role.name)
                    stages_done["Review"] = True
                    stages_done["Test"] = True
                    escalate = CompanyMessage(
                        content=f"老板，{role.name} 已经打回 {max_rework} 次了，团队尽力修了但还有问题。先继续推进，后续再优化 🫡",
                        cause_by="ChatReply",
                        sent_from="PM",
                        task_id=task.task_id,
                    )
                    await self._env.publish(escalate)
                    try:
                        idx = self._PIPELINE_ORDER.index(role.name)
                        next_role = self._PIPELINE_ORDER[idx + 1] if idx + 1 < len(self._PIPELINE_ORDER) else None
                    except ValueError:
                        next_role = None
                    forced_approve = CompanyMessage(
                        content=f"APPROVED（已达最大返工次数，强制通过）\n\n原始审查意见：{result_msg.content[:500]}",
                        cause_by=result_msg.cause_by,
                        sent_from=role.name,
                        send_to=next_role or "",
                        task_id=task.task_id,
                    )
                    await self._env.publish(forced_approve)
                else:
                    await self._env.publish(result_msg)

            if not round_had_work:
                idle_rounds += 1
                logger.info("本轮无实际工作产出 (连续空转 %d 轮)", idle_rounds)
                if idle_rounds >= 2:
                    logger.info("连续 %d 轮无工作产出，pipeline 结束", idle_rounds)
                    break
            else:
                idle_rounds = 0
        else:
            logger.warning("达到最大轮次 %d，强制结束", max_rounds)

        if task.result and task.expected_output:
            passed, feedback = await task.verify(self._router)
            if passed:
                task.status = "done"
            else:
                task.status = "failed"
                task.retry_count += 1
                if task.retry_count <= task.max_retries:
                    logger.info("任务验证失败，重试 (%d/%d): %s",
                                task.retry_count, task.max_retries, feedback)
                    retry_msg = CompanyMessage(
                        content=f"任务验证未通过，请根据以下反馈修改：\n{feedback}\n\n原始需求：{requirement}",
                        cause_by="CodeReview",
                        sent_from="System",
                        task_id=task.task_id,
                    )
                    await self._env.publish(retry_msg)
                    self._store.save_message(retry_msg)
                    self._store.save_task(task)
                    return await self._continue_run(task, requirement, max_rounds - round_num)
                else:
                    logger.warning("任务验证失败且已达最大重试: %s", feedback)
        else:
            task.status = "done"

        self._store.save_task(task)
        self._store.finish_run(run_id, task.status, round_num, task.result[:500] if task.result else "")
        return task.result

    async def _continue_run(self, task: CompanyTask, requirement: str, remaining_rounds: int) -> str:
        """验证失败后继续执行剩余轮次."""
        for round_num in range(1, remaining_rounds + 1):
            if self._env.is_idle():
                break
            for role in self._env.roles.values():
                if not role.has_pending:
                    continue
                status_msg = CompanyMessage(
                    content=f"{role.name} 开始工作...",
                    cause_by="StatusUpdate",
                    sent_from=role.name,
                )
                await self._env.publish(status_msg)
                result_msg = await role.run()
                if result_msg:
                    task.result = result_msg.content
                    await self._env.publish(result_msg)

        if task.result and task.expected_output:
            passed, feedback = await task.verify(self._router)
            if passed:
                task.status = "done"
            else:
                task.retry_count += 1
                if task.retry_count <= task.max_retries:
                    retry_msg = CompanyMessage(
                        content=f"任务验证未通过，请修改：\n{feedback}",
                        cause_by="CodeReview",
                        sent_from="System",
                        task_id=task.task_id,
                    )
                    await self._env.publish(retry_msg)
                    return await self._continue_run(task, requirement, remaining_rounds // 2)
                task.status = "failed"
        else:
            task.status = "done"

        return task.result

    async def run_standby(self, is_recovery: bool = False) -> str:
        """待命模式：启动飞书 → 角色报到 → 持续监听消息循环."""
        import asyncio

        await self.start_feishu()

        if is_recovery:
            recovery_msg = CompanyMessage(
                content="老板，系统刚重启，团队已自动恢复上线 🫡 随时待命！",
                cause_by="RoleCheckin",
                sent_from="PM",
            )
            await self._env.publish(recovery_msg)
        else:
            checkin_lines = {
                "PM": "老板好 🫡 PM 诸葛到岗了，有什么需求随时说，我来安排～",
                "Developer": "老板，码农就位 💪 随时开搞",
                "Reviewer": "老板好，审查员在线 👀 代码质量我盯着",
                "QA": "老板～测试就绪，准备找茬 🔍",
                "DevOps": "老板，运维到位 ✅ 部署环境一切正常",
            }

            for role in self._env.roles.values():
                line = checkin_lines.get(role.name, f"老板好，{role.name} 已就绪，等待指令。")
                checkin = CompanyMessage(
                    content=line,
                    cause_by="RoleCheckin",
                    sent_from=role.name,
                )
                await self._env.publish(checkin)

        self._standby_stop = asyncio.Event()
        self._standby_history: list[tuple[str, str]] = self._restore_standby_history()
        try:
            while not self._standby_stop.is_set():
                await self._process_standby_messages()
                try:
                    await asyncio.wait_for(self._standby_stop.wait(), timeout=1.0)
                except asyncio.TimeoutError:
                    pass
        finally:
            await self.stop_feishu()
        return "AI Company 待命模式已结束"

    _TASK_TRIGGER_KEYWORDS = [
        "开发一个", "写一个", "做一个", "帮我开发", "帮我写", "帮我做",
        "开始开发", "开始写", "开始做", "开干", "开搞", "启动流水线", "开始干活",
        "写个", "做个", "搞一个", "搞个", "实现一个", "实现个",
        "写PRD", "写 PRD", "出PRD", "出 PRD",
        "马上开发", "立刻开发", "赶紧开发", "直接开发",
        "马上做", "赶紧做", "赶紧搞", "快做", "快搞",
        "创建一个", "建一个", "生成一个",
        "安排开发", "安排一下", "动手吧", "动手做", "你就开始",
        "现在就做", "现在就开发", "现在开始",
        "加入", "加个", "加一个", "增加", "添加", "新增",
        "改一下", "改个", "修改", "优化一下", "优化个",
        "支持一下", "支持个", "接入",
    ]

    def _detect_task_intent(self, text: str) -> bool:
        """检测用户消息是否包含开发任务意图."""
        return any(kw in text for kw in self._TASK_TRIGGER_KEYWORDS)

    _QUICK_TASK_KEYWORDS: dict[str, list[str]] = {
        "Developer": [
            "跑起来", "启动项目", "启动服务", "运行项目", "运行服务",
            "执行一下", "调试", "查日志", "看日志", "查看日志",
            "修个bug", "修一下bug", "改个bug", "热修复",
        ],
        "DevOps": [
            "部署一下", "上线", "发布一下", "重启服务", "重启一下",
            "回滚", "检查服务", "健康检查", "看看服务",
        ],
        "QA": [
            "跑测试", "跑一下测试", "测试一下", "回归测试", "运行测试",
        ],
    }

    def _detect_quick_task(self, messages: list[CompanyMessage]) -> Optional[str]:
        """检测操作类意图，返回目标角色名或 None."""
        text = " ".join(m.content for m in messages)
        for role_name, keywords in self._QUICK_TASK_KEYWORDS.items():
            if any(kw in text for kw in keywords):
                return role_name
        return None

    async def _handle_quick_task(self, role_name: str, messages: list[CompanyMessage]) -> None:
        """快速任务：直接派给角色带工具执行，不走完整 pipeline."""
        from agent.company.action import QUICK_TASK

        role = self._env.roles.get(role_name)
        if not role:
            role = self._env.roles.get("Developer")
        if not role:
            return

        task_text = "\n".join(m.content for m in messages)
        history_lines = [f"[{s}]: {c}" for s, c in self._standby_history[-10:]]
        project_status = self._build_project_status()
        context = (
            f"{project_status}"
            f"## 对话上下文\n" + "\n".join(history_lines) +
            f"\n\n## 用户指令\n{task_text}"
        )

        ack = CompanyMessage(
            content=f"收到老板，让{role.name}马上处理 🫡",
            cause_by="ChatReply",
            sent_from="PM",
        )
        await self._env.publish(ack)
        self._standby_history.append(("PM", ack.content))

        result_msg = await role._act(QUICK_TASK, context)
        self._standby_history.append((role.name, result_msg.content))
        self._store.save_message(result_msg)
        await self._env.publish(result_msg)

    _ROLE_NICK_MAP: dict[str, list[str]] = {
        "PM": ["诸葛", "小诸葛", "PM", "pm", "产品", "产品经理"],
        "Developer": ["小码", "码农", "开发", "程序员", "developer"],
        "Reviewer": ["小审", "审查", "审查员", "reviewer"],
        "QA": ["小茬", "测试", "QA", "qa"],
        "DevOps": ["小布", "运维", "部署", "devops"],
    }

    _ROLE_TOPIC_KEYWORDS: dict[str, list[str]] = {
        "Developer": ["代码", "写代码", "编码", "bug", "报错", "接口", "函数", "变量", "编译"],
        "Reviewer": ["审查", "review", "代码质量", "代码规范"],
        "QA": ["测试", "用例", "测试结果", "通过率", "覆盖率"],
        "DevOps": ["部署", "上线", "服务器", "运维", "环境", "发布"],
    }

    def _route_message_to_role(self, msg: CompanyMessage) -> Optional[str]:
        """根据消息内容智能路由到对应角色。返回角色名或 None（默认 PM）."""
        if msg.send_to and msg.send_to in self._env.roles:
            return msg.send_to

        text = msg.content
        for role_name, nicks in self._ROLE_NICK_MAP.items():
            if role_name == "PM":
                continue
            for nick in nicks:
                if nick in text:
                    return role_name

        for role_name, keywords in self._ROLE_TOPIC_KEYWORDS.items():
            for kw in keywords:
                if kw in text:
                    return role_name

        return None

    async def _process_standby_messages(self) -> None:
        """处理待命模式下的消息：关键词检测触发流水线，否则聊天回复."""
        from datetime import datetime
        from agent.company.action import CHAT_REPLY

        if self._pipeline_running:
            queue = self._env._pipeline_user_queue or []
            if queue:
                collected = list(queue)
                queue.clear()
                for m in collected:
                    self._standby_history.append((m.sent_from, m.content))
                    self._store.save_message(m)

                req_msgs = [m for m in collected if self._detect_task_intent(m.content)]
                chat_msgs = [m for m in collected if m not in req_msgs]

                if req_msgs:
                    self._pipeline_user_msgs.extend(req_msgs)
                    summary = "、".join(m.content[:20] for m in req_msgs)
                    ack = CompanyMessage(
                        content=f"收到老板 🫡 已记录补充需求（{summary}），会纳入当前开发",
                        cause_by="ChatReply",
                        sent_from="PM",
                    )
                    await self._env.publish(ack)

                if chat_msgs:
                    for cm in chat_msgs:
                        target = self._route_message_to_role(cm)
                        responder = self._env.roles.get(target) if target else self._env.roles.get("PM")
                        if not responder:
                            responder = self._env.roles.get("PM")
                        if responder:
                            history_lines = [f"[{s}]: {c}" for s, c in self._standby_history[-10:]]
                            chat_context = (
                                "当前团队正在开发中（pipeline 运行中）。\n\n"
                                f"## 对话记录\n" + "\n".join(history_lines)
                            )
                            reply_msg = await responder._act(CHAT_REPLY, chat_context)
                            self._standby_history.append((responder.name, reply_msg.content))
                            self._store.save_message(reply_msg)
                            await self._env.publish(reply_msg)
            return

        pm_role = self._env.roles.get("PM")
        if not pm_role or not pm_role.has_pending:
            return
        messages = await pm_role._observe()
        if not messages:
            return

        for m in messages:
            self._standby_history.append((m.sent_from, m.content))
            self._store.save_message(m)

        user_messages = [m for m in messages if m.sent_from not in self._env.roles]
        has_task_intent = any(self._detect_task_intent(m.content) for m in user_messages)

        if has_task_intent:
            if len(self._standby_history) > 40:
                self._standby_history = self._standby_history[-30:]
            task_context = "\n\n".join(m.content for m in user_messages)
            history_context = "\n".join(
                f"[{s}]: {c}" for s, c in self._standby_history[-10:]
            )
            project_status = self._build_project_status()
            full_context = f"{project_status}## 对话上下文\n{history_context}\n\n## 用户最新需求\n{task_context}"

            eval_result = await pm_role._act(EVALUATE_REQUIREMENT, full_context)
            eval_text = eval_result.content if hasattr(eval_result, "content") else str(eval_result)
            first_line = eval_text.strip().split("\n")[0].strip()

            if first_line.startswith("NEED_CLARIFY"):
                clarify_text = eval_text.strip().split("\n", 1)[1].strip() if "\n" in eval_text.strip() else "老板，需求不太明确，能再说具体点吗？"
                clarify_msg = CompanyMessage(
                    content=clarify_text,
                    cause_by="ChatReply",
                    sent_from=pm_role.name,
                )
                await self._env.publish(clarify_msg)
                self._standby_history.append((pm_role.name, clarify_msg.content))
                self._store.save_message(clarify_msg)
                return

            confirm_msg = CompanyMessage(
                content="收到老板 👌 需求已确认，我这就安排团队开干！",
                cause_by="ChatReply",
                sent_from=pm_role.name,
            )
            await self._env.publish(confirm_msg)
            self._standby_history.append((pm_role.name, confirm_msg.content))

            req_summary = eval_text.strip().split("\n", 1)[1].strip() if "\n" in eval_text.strip() else task_context
            project_dir = self._create_project_workspace(task_context)
            enriched = (
                f"## 项目工作目录\n{project_dir}\n"
                f"所有文件必须创建在此目录下。PRD 写入 docs/prd.md，设计写入 docs/design.md，"
                f"代码写入 src/，测试写入 tests/。\n\n{full_context}"
            )

            async def _run_pipeline(req: str, pdir: Path) -> None:
                try:
                    result = await self.run(req, max_rounds=20)
                    status = "done" if result else "failed"
                except Exception as e:
                    logger.error("Pipeline 执行异常: %s", e)
                    status = "failed"
                finally:
                    self._pipeline_running = False
                    self._env._pipeline_user_queue = None
                    self._update_project_status(pdir, status)
                    done_msg = CompanyMessage(
                        content=f"老板，任务{'完成' if status == 'done' else '执行出错了'}！项目目录: {pdir}",
                        cause_by="StatusUpdate",
                        sent_from="PM",
                    )
                    await self._env.publish(done_msg)

            import asyncio
            self._pipeline_running = True
            self._env._pipeline_user_queue = []
            asyncio.create_task(_run_pipeline(enriched, project_dir))
            return

        quick_target = self._detect_quick_task(user_messages)
        if quick_target:
            await self._handle_quick_task(quick_target, user_messages)
            return

        if len(self._standby_history) > 40:
            self._standby_history = self._standby_history[-30:]

        now = datetime.now().strftime("%Y-%m-%d %H:%M (%A)")
        history_lines = [f"[{s}]: {c}" for s, c in self._standby_history[-10:]]
        history_text = "\n".join(history_lines)
        project_status = self._build_project_status()

        responded_roles: set[str] = set()
        for m in user_messages:
            target = self._route_message_to_role(m)
            role_name = target if target and target in self._env.roles else "PM"
            if role_name in responded_roles:
                continue
            responded_roles.add(role_name)
            responder = self._env.roles.get(role_name) or pm_role
            chat_context = f"当前时间: {now}\n\n{project_status}## 对话记录\n{history_text}"
            reply_msg = await responder._act(CHAT_REPLY, chat_context)
            self._standby_history.append((responder.name, reply_msg.content))
            self._store.save_message(reply_msg)
            await self._env.publish(reply_msg)

    def stop_standby(self) -> None:
        """外部调用停止待命模式."""
        if hasattr(self, "_standby_stop"):
            self._standby_stop.set()

    _PIPELINE_ORDER = ["PM", "Developer", "Reviewer", "QA", "DevOps"]

    def _check_rework(self, role_name: str, content: str) -> Optional[str]:
        """检查角色输出是否需要返工。返回需要返工的目标角色名，或 None."""
        if role_name == "Reviewer":
            upper = content.upper()
            if "REJECTED" in upper or "拒收" in content or "打回" in content:
                return "Developer"
        if role_name == "QA":
            indicators = ["失败", "FAIL", "fail", "不通过", "未通过", "error", "Error"]
            if any(ind in content for ind in indicators):
                return "Developer"
        return None

    def _clear_downstream_inboxes(self, role_name: str) -> None:
        """清空当前角色下游所有角色的 inbox，防止基于被拒代码继续工作."""
        try:
            idx = self._PIPELINE_ORDER.index(role_name)
        except ValueError:
            return
        for downstream in self._PIPELINE_ORDER[idx + 1:]:
            role = self._env.roles.get(downstream)
            if role:
                cleared = len(role._inbox)
                role._inbox.clear()
                if cleared:
                    logger.info("清空 %s 的 inbox（%d 条），等待返工完成", downstream, cleared)

    def _restore_standby_history(self) -> list[tuple[str, str]]:
        """从 CompanyStore 恢复最近的对话历史."""
        try:
            messages = self._store.list_messages(limit=30)
            messages.reverse()
            history = []
            for m in messages:
                if m.cause_by in ("RoleCheckin", "StatusUpdate"):
                    continue
                history.append((m.sent_from, m.content[:200]))
            return history[-20:]
        except Exception as e:
            logger.warning("恢复对话历史失败: %s", e)
            return []

    def _build_project_status(self) -> str:
        """构建最近项目/任务状态摘要，注入到 ChatReply 上下文."""
        import json

        parts = []
        try:
            runs = self._store.list_runs(limit=5)
            if runs:
                lines = []
                for r in runs:
                    req = r.get("requirement", "")[:80]
                    status = r.get("status", "unknown")
                    summary = r.get("result_summary", "")[:100]
                    lines.append(f"- [{status}] {req}" + (f" → {summary}" if summary else ""))
                parts.append(
                    "## 最近任务记录（流水线执行历史）\n"
                    "注意：done 表示流水线跑完，不代表服务正在运行。你无法确认服务是否在线。\n"
                    + "\n".join(lines)
                )
        except Exception as e:
            logger.debug("读取任务记录失败: %s", e)

        try:
            projects_dir = get_projects_dir()
            if projects_dir.exists():
                project_dirs = sorted(projects_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)[:3]
                proj_lines = []
                for pd in project_dirs:
                    meta_file = pd / ".project.json"
                    if meta_file.exists():
                        meta = json.loads(meta_file.read_text())
                        status = meta.get("status", "unknown")
                        req = meta.get("requirement", "")[:80]
                        proj_lines.append(f"- [{status}] {pd.name}: {req}")
                if proj_lines:
                    parts.append("## 最近项目目录（代码产出，不代表服务在运行）\n" + "\n".join(proj_lines))
        except Exception as e:
            logger.debug("读取项目目录失败: %s", e)

        if parts:
            return "\n\n".join(parts) + "\n\n"
        return "## 任务状态\n当前没有任何已执行的任务记录。\n\n"

    @staticmethod
    def _extract_project_name(text: str) -> str:
        """从 PM 评估文本中提取简短项目名（去掉寒暄/口语前缀）."""
        import re
        clean = text.strip()
        clean = re.sub(
            r'^(老板[，,]?\s*|核心需求[是：:]*\s*|我(们)?理解[的是：:]*\s*|'
            r'需求[我们]*[看理解说][^，,。\n]*[，,。：:]\s*|'
            r'需求(是|已|很)[^，,。\n]*[，,。]\s*|'
            r'[^，,。\n]*已经对齐了[，,]\s*|'
            r'基于[^，,。\n]*[，,]\s*|'
            r'[^，,。\n]*核心需求是[，,：:]*\s*|'
            r'核心是[，,：:]*\s*)',
            '', clean,
        )
        clean = re.sub(r'^[，,：:。\s]+', '', clean)
        for prefix in ("做一个", "开发一个", "写一个", "搞一个", "实现一个", "创建一个", "建一个"):
            if clean.startswith(prefix):
                clean = clean[len(prefix):]
                break
        first_line = clean.split('\n')[0].strip()
        first_sentence = re.split(r'[。！？\n，,的]', first_line)[0].strip()
        return first_sentence[:10] if first_sentence else text.strip()[:10]

    def _create_project_workspace(self, requirement: str) -> Path:
        """根据需求创建项目工作目录，返回项目路径."""
        import json
        import re
        from datetime import datetime

        date_str = datetime.now().strftime("%Y%m%d")
        short_name = self._extract_project_name(requirement)
        slug = short_name[:10].strip()
        slug = re.sub(r'[^\w\u4e00-\u9fff-]', '_', slug)
        slug = re.sub(r'_+', '_', slug).strip('_') or "project"
        project_name = f"{date_str}-{slug}"

        project_dir = get_projects_dir() / project_name
        for sub in ("docs", "src", "tests"):
            (project_dir / sub).mkdir(parents=True, exist_ok=True)

        meta_file = project_dir / ".project.json"
        if not meta_file.exists():
            meta = {
                "requirement": requirement[:500],
                "created_at": datetime.now().isoformat(),
                "status": "in_progress",
            }
            meta_file.write_text(json.dumps(meta, ensure_ascii=False, indent=2))

        logger.info("项目工作目录已创建: %s", project_dir)
        return project_dir

    def _update_project_status(self, project_dir: Path, status: str) -> None:
        """更新项目元数据状态."""
        import json
        from datetime import datetime

        meta_file = project_dir / ".project.json"
        if not meta_file.exists():
            return
        try:
            meta = json.loads(meta_file.read_text())
            meta["status"] = status
            meta["completed_at"] = datetime.now().isoformat()
            meta_file.write_text(json.dumps(meta, ensure_ascii=False, indent=2))
        except Exception as e:
            logger.warning("更新项目状态失败: %s", e)

    async def run_interactive(self, requirement: str, max_rounds: int = 50) -> str:
        """交互模式：每轮结束后等待用户输入."""
        import asyncio
        import sys

        task = CompanyTask(
            title=requirement[:60],
            description=requirement,
        )
        await self.assign(task)

        for round_num in range(1, max_rounds + 1):
            if self._env.is_idle():
                print(f"\n[Company] 所有角色空闲，结束 (round {round_num})")
                break

            print(f"\n=== Round {round_num} ===")
            for role in self._env.roles.values():
                if not role.has_pending:
                    continue
                result_msg = await role.run()
                if result_msg:
                    task.result = result_msg.content
                    print(f"\n[{result_msg.sent_from}] ({result_msg.cause_by})")
                    print(result_msg.content[:500])
                    if len(result_msg.content) > 500:
                        print("... (truncated)")
                    await self._env.publish(result_msg)

            if self._env.is_idle():
                break

            print("\n[输入指令 / 回车继续 / q 退出]> ", end="", flush=True)
            loop = asyncio.get_event_loop()
            user_input = await loop.run_in_executor(None, sys.stdin.readline)
            user_input = user_input.strip()

            if user_input.lower() in ("q", "quit", "exit"):
                print("[Company] 用户退出")
                break
            elif user_input:
                directive = CompanyMessage(
                    content=user_input,
                    cause_by="HumanDirective",
                    sent_from="Human",
                )
                await self._env.publish(directive)

        task.status = "done" if task.result else "failed"
        return task.result
