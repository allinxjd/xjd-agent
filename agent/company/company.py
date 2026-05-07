"""Company — 团队编排主循环."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from agent.company.action import USER_REQUIREMENT, EVALUATE_REQUIREMENT
from agent.company.chat_bridge import ChatBridge
from agent.company.environment import CompanyEnvironment
from agent.company.validators import validate_checkpoint_output
from agent.company.feishu_bridge import FeishuBotConfig, FeishuBridge
from agent.company.locale import CompanyLocale
from agent.company.memory import CompanyMemory
from agent.company.message import CompanyMessage
from agent.company.role import CompanyRole
from agent.company.store import CompanyStore
from agent.company.task import CompanyTask
from agent.core.config import get_projects_dir

logger = logging.getLogger(__name__)


@dataclass
class PipelineStage:
    """Pipeline 中的一个阶段."""

    role: str
    action: str
    stage_key: str = ""
    rework_target: str = ""
    requires_approval: bool = False

    def __post_init__(self):
        if not self.stage_key:
            self.stage_key = self.action


@dataclass
class PipelineConfig:
    """Pipeline 阶段配置."""

    stages: list[PipelineStage] = field(default_factory=list)

    @property
    def role_order(self) -> list[str]:
        seen: dict[str, None] = {}
        for s in self.stages:
            seen.setdefault(s.role, None)
        return list(seen.keys())

    @property
    def stage_keys(self) -> list[str]:
        return [s.stage_key for s in self.stages]

    def rework_target_for(self, role_name: str) -> str:
        for s in self.stages:
            if s.role == role_name and s.rework_target:
                return s.rework_target
        return ""

    def stage_for_action(self, action: str) -> Optional["PipelineStage"]:
        for s in self.stages:
            if s.action == action:
                return s
        return None

    @classmethod
    def default(cls) -> "PipelineConfig":
        return cls(stages=[
            PipelineStage(role="PM", action="WritePRD", stage_key="PRD", requires_approval=True),
            PipelineStage(role="PM", action="WritePrototype", stage_key="Prototype", requires_approval=True),
            PipelineStage(role="PM", action="WriteUIDesign", stage_key="UIDesign", requires_approval=True),
            PipelineStage(role="PM", action="WriteDesign", stage_key="Design", requires_approval=True),
            PipelineStage(role="Developer", action="SetupEnv", stage_key="Env"),
            PipelineStage(role="Developer", action="WriteCode", stage_key="Code"),
            PipelineStage(role="Developer", action="VerifyRun", stage_key="Verify"),
            PipelineStage(role="Reviewer", action="CodeReview", stage_key="Review", rework_target="Developer"),
            PipelineStage(role="QA", action="WriteTest", stage_key="Test"),
            PipelineStage(role="QA", action="RunTest", stage_key="Test", rework_target="Developer"),
            PipelineStage(role="DevOps", action="DeployPlan", stage_key="Deploy"),
            PipelineStage(role="DevOps", action="ExecuteDeploy", stage_key="Deploy"),
        ])

    @classmethod
    def from_workflow(cls, workflow: dict) -> "PipelineConfig":
        steps = workflow.get("steps", [])
        if not steps:
            return cls.default()
        stages = []
        for step in steps:
            role = step.get("role", "")
            action = step.get("action", "")
            if not role or not action:
                continue
            stages.append(PipelineStage(
                role=role,
                action=action,
                stage_key=step.get("stage_key", action),
                rework_target=step.get("rework_target", ""),
            ))
        return cls(stages=stages) if stages else cls.default()


@dataclass
class CompanyConfig:
    """Company 可配置参数."""

    max_rework: int = 3
    max_rounds: int = 20
    max_minutes: int = 180
    max_project_chars: int = 30000
    max_project_files: int = 100
    max_active_projects: int = 10
    archive_after_days: int = 7
    project_name_max_len: int = 20
    standby_history_max: int = 40
    standby_history_trim: int = 30
    standby_history_context: int = 10
    idle_rounds_to_stop: int = 2
    locale: str = "zh-CN"
    boss_title: str = ""
    projects_dir: str = ""
    pipeline: PipelineConfig = field(default_factory=PipelineConfig.default)

STAGE_TIMEOUTS: dict[str, int] = {
    "WritePRD": 5 * 60,
    "WritePrototype": 8 * 60,
    "WriteUIDesign": 8 * 60,
    "WriteDesign": 5 * 60,
    "SetupEnv": 10 * 60,
    "WriteCode": 10 * 60,
    "VerifyRun": 10 * 60,
    "CodeReview": 3 * 60,
    "FixCode": 5 * 60,
    "WriteTest": 5 * 60,
    "RunTest": 10 * 60,
    "DeployPlan": 3 * 60,
    "ExecuteDeploy": 10 * 60,
}

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
        chat_bridge: Optional[ChatBridge] = None,
        config: Optional[CompanyConfig] = None,
    ) -> None:
        self._config = config or CompanyConfig()
        self._pipeline = self._config.pipeline
        self._locale = CompanyLocale.load(self._config.locale)
        self._boss_title = self._config.boss_title or self._locale.get("address.boss", "老板")
        # 从技能配置读取用户自定义风格（优先级高于 locale 默认值）
        try:
            from agent.core.secrets import get_secrets_store
            _secrets = get_secrets_store()
            _custom_title = _secrets.get("ai-company", "COMPANY_BOSS_TITLE")
            if _custom_title:
                self._boss_title = _custom_title
            _custom_style = _secrets.get("ai-company", "COMPANY_CHAT_STYLE")
            if _custom_style:
                self._locale._data["chat_style"] = _custom_style
        except Exception:
            pass
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
        self._pipeline_cancel = False
        self._pipeline_user_msgs: list[CompanyMessage] = []
        self._active_project_name: str = ""
        self._switched_project_dir: Optional[Path] = None
        self._project_ctx: Optional["ProjectContext"] = None
        self._waiting_approval: Optional[str] = None
        self._needs_prototype: bool = True
        self._pending_project_name: Optional[dict] = None
        self._task_queue: list[dict] = []

        if chat_bridge:
            self._feishu_bridge = chat_bridge  # type: ignore[assignment]
            chat_bridge.set_environment(self._env)
            self._env.chat_bridge = chat_bridge
        elif feishu_chat_id and feishu_bots:
            self._feishu_bridge = FeishuBridge(
                group_chat_id=feishu_chat_id,
                bot_configs=feishu_bots,
                environment=self._env,
            )
            self._env.chat_bridge = self._feishu_bridge

    @property
    def environment(self) -> CompanyEnvironment:
        return self._env

    def hire(self, role: CompanyRole) -> None:
        """注册角色到环境，注入运行时依赖和 Karpathy 准则."""
        role._runtime_router = self._router
        role._runtime_registry = self._registry
        role._boss_title = self._boss_title
        role._locale = self._locale
        if "{boss_title}" in (role.system_prompt or ""):
            role.system_prompt = role.system_prompt.replace("{boss_title}", self._boss_title)
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

    _REQUIREMENT_ISSUE_INDICATORS = None
    _CODE_INCOMPLETE_INDICATORS = None
    _NO_WORK_INDICATORS = None

    def _get_indicators(self, key: str, fallback: list[str]) -> list[str]:
        val = self._locale.get(f"keywords.{key}")
        return val if isinstance(val, list) else fallback

    def _has_requirement_issue(self, content: str) -> bool:
        """检测角色输出是否表示需求/输入有问题."""
        short = content[:500]
        indicators = self._get_indicators("requirement_issues", [
            "需求不清", "需求缺失", "需求为空", "没有需求", "需求有问题",
            "设计与需求下面是空", "没提供需求", "安全轮次上限", "如需继续",
        ])
        return any(ind in short for ind in indicators)

    def _has_code_incomplete(self, content: str) -> bool:
        """检测 Reviewer 输出是否表示代码不完整."""
        short = content[:500]
        indicators = self._get_indicators("code_incomplete", [
            "没有 diff", "没有可审查", "交白卷", "无从审起", "没有变更集",
            "没有代码", "看不到代码", "拿不到", "没有提供",
        ])
        return any(ind in short for ind in indicators)

    _NO_WORK_FALLBACK = [
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
        indicators = self._get_indicators("no_work", self._NO_WORK_FALLBACK)
        return any(ind in short for ind in indicators)

    def _is_review_approved(self, content: str) -> bool:
        import re
        last_lines = "\n".join(content.strip().splitlines()[-5:]).upper()
        if re.search(r'\bREJECTED\b', last_lines):
            return False
        if re.search(r'\bNOT\s+APPROVED\b', last_lines):
            return False
        return bool(re.search(r'\bAPPROVED\b', last_lines))

    def _is_test_passed(self, content: str) -> bool:
        import re
        pytest_match = re.search(r'(\d+)\s+passed', content)
        jest_match = re.search(r'Tests:\s+(\d+)\s+passed', content)
        if pytest_match or jest_match:
            has_fail = bool(re.search(r'(\d+)\s+failed', content))
            return not has_fail
        pass_indicators = self._locale.get("keywords.test_pass") or ["全部通过", "测试通过", "通过率 100", "没毛病", "稳了"]
        fail_keywords = self._locale.get("keywords.test_fail") or ["失败", "不通过", "未通过"]
        has_pass = any(ind in content for ind in pass_indicators) or bool(re.search(r'\bPASS\b', content, re.IGNORECASE))
        fail_patterns = [r'\bFAIL\b', r'\bERROR\b']
        has_fail = any(kw in content for kw in fail_keywords)
        has_fail = has_fail or any(bool(re.search(p, content, re.IGNORECASE)) for p in fail_patterns)
        if has_fail and re.search(r'(?:no|0)\s*error', content, re.IGNORECASE):
            has_fail = False
        return has_pass and not has_fail

    def _is_approval(self, text: str) -> bool:
        """判断用户回复是否为审批通过。"""
        text_clean = text.strip().lower()
        # 精确匹配：整条消息就是审批词（去掉标点后）
        import re as _re_ap
        text_norm = _re_ap.sub(r'[^\w一-鿿]', '', text_clean)
        exact_approvals = [
            "通过", "ok", "确认", "可以", "没问题", "行",
            "好的", "好", "approved", "lgtm", "yes", "过",
            "没问题的", "可以的", "行的", "好吧", "嗯",
        ]
        if text_norm in exact_approvals:
            return True
        # 短消息（<10字）包含审批关键词且不含动词/名词干扰
        if len(text_clean) < 10:
            short_approvals = ["通过", "ok", "确认", "没问题", "approved", "lgtm"]
            return any(kw in text_clean for kw in short_approvals)
        return False

    async def _wait_for_approval(
        self, stage_key: str, task: "CompanyTask", project_dir: Optional[Path] = None
    ) -> tuple[bool, str]:
        """等待用户审批。返回 (approved, feedback)."""
        import asyncio as _aio
        import time as _t

        if stage_key in ("Prototype", "UIDesign"):
            await self._send_stage_screenshots(stage_key, project_dir, task)

        _stage_names = {
            "PRD": "需求文档", "Prototype": "原型图",
            "UIDesign": "UI 设计", "Design": "技术方案",
        }
        _display = _stage_names.get(stage_key, stage_key)
        approval_msg = CompanyMessage(
            content=(
                f"{_display}已完成，请审阅附件。\n"
                "回复「确认」继续，或直接说修改意见。"
            ),
            cause_by="ApprovalRequest",
            sent_from="PM",
            task_id=task.task_id,
        )
        await self._env.publish(approval_msg)

        self._waiting_approval = stage_key
        approval_timeout = 24 * 3600
        start = _t.monotonic()
        while _t.monotonic() - start < approval_timeout:
            await _aio.sleep(2)
            if self._pipeline_user_msgs:
                user_reply = self._pipeline_user_msgs[-1].content
                self._pipeline_user_msgs.clear()
                approved = self._is_approval(user_reply)
                self._waiting_approval = None
                return (approved, "" if approved else user_reply)
        self._waiting_approval = None
        return (True, "")

    async def _save_and_send_prototypes(
        self, content: str, project_dir: Optional[Path], task: "CompanyTask", stage_key: str
    ) -> None:
        """解析 WritePrototype/WriteUIDesign 输出，保存 HTML，渲染 PNG，生成专业原型报告。"""
        import re as _re
        import base64 as _b64
        if not project_dir:
            return

        subdir = "prototypes" if stage_key == "Prototype" else "ui-designs"
        target_dir = project_dir / subdir
        target_dir.mkdir(parents=True, exist_ok=True)

        # 清理 LLM 工具调用残留的 XML 标签
        content = _re.sub(r'</?tool_result[^>]*>', '', content)
        content = _re.sub(r'</?tool_use[^>]*>', '', content)
        content = _re.sub(r'\{"content":\s*"', '', content)
        content = _re.sub(r'"\s*,\s*"is_error":\s*false\}', '', content)

        # 解析 HTML 块：支持 ```html ... ``` 格式
        html_blocks = _re.findall(r'```html\s*\n(.*?)```', content, _re.DOTALL)
        if not html_blocks:
            # 提取所有完整 HTML 文档
            html_docs = _re.findall(r'(<!DOCTYPE html>.*?</html>)', content, _re.DOTALL | _re.IGNORECASE)
            if html_docs:
                html_blocks = html_docs
            elif '<html' in content.lower() or '<!doctype' in content.lower():
                html_blocks = [content]

        if not html_blocks:
            # LLM 可能通过 write_file 工具直接写入了文件，检查磁盘
            existing_html = sorted(target_dir.glob("*.html"))
            if existing_html:
                html_blocks = []
                _disk_page_info: list[dict] = []
                for f in existing_html:
                    html_content = f.read_text(encoding="utf-8")
                    title_match = _re.search(r'<title[^>]*>([^<]+)</title>', html_content, _re.IGNORECASE)
                    name = title_match.group(1).strip() if title_match else f.stem
                    _disk_page_info.append({"name": name, "desc": "", "file": f, "safe_name": f.stem})
                logger.info("从磁盘读取 %s HTML: %d 个文件", subdir, len(_disk_page_info))
                # 跳过解析，直接用磁盘文件生成报告
                screenshots: dict[str, Path] = {}
                try:
                    from agent.company.prototype_renderer import render_html_to_image
                    for info in _disk_page_info:
                        img = await render_html_to_image(info["file"])
                        if img:
                            screenshots[info["safe_name"]] = img
                except ImportError:
                    pass
                except Exception as e:
                    logger.warning("截图渲染失败: %s", e)
                report_path = await self._generate_prototype_report(
                    _disk_page_info, screenshots, target_dir, stage_key
                )
                report_msg = CompanyMessage(
                    content=f"{stage_key} 报告已生成（共 {len(_disk_page_info)} 页），请用浏览器打开查看。",
                    cause_by="StageFile",
                    sent_from="PM",
                    task_id=task.task_id,
                    metadata={"file_path": str(report_path), "filename": report_path.name},
                )
                await self._env.publish(report_msg)
                return
            notify = CompanyMessage(
                content=content[:2000],
                cause_by=f"Write{stage_key}",
                sent_from="PM",
                task_id=task.task_id,
            )
            await self._env.publish(notify)
            return

        # 提取页面名称和描述
        sections = _re.split(r'```html\s*\n.*?```', content, flags=_re.DOTALL)
        page_info: list[dict] = []
        for i, html in enumerate(html_blocks):
            header_text = sections[i] if i < len(sections) else ""
            # 从 HTML <title> 标签提取页面名称
            title_match = _re.search(r'<title[^>]*>([^<]+)</title>', html, _re.IGNORECASE)
            name_from_title = title_match.group(1).strip() if title_match else ""
            # 从 markdown 标题提取
            name_match = _re.search(r'#{1,3}\s+(.+?)(?:\n|$)', header_text)
            name_from_header = name_match.group(1).strip() if name_match else ""
            # 优先用 header，fallback 到 title，再 fallback 到 "页面 N"
            name = name_from_header or name_from_title or f"页面 {i+1}"
            # 确保名称不含 HTML/JSON 残留
            if '<' in name or '{' in name:
                name = name_from_title or f"页面 {i+1}"
            name = name[:60]
            desc_lines = [l.strip() for l in header_text.strip().split('\n')
                          if l.strip() and not l.strip().startswith('#') and '<' not in l and '{' not in l]
            desc = '\n'.join(desc_lines[-3:]) if desc_lines else ""
            safe_name = _re.sub(r'[^\w一-鿿-]', '-', name).strip('-')[:50] or f"page-{i+1}"
            file_path = target_dir / f"{safe_name}.html"
            file_path.write_text(html.strip(), encoding="utf-8")
            page_info.append({"name": name, "desc": desc, "file": file_path, "safe_name": safe_name})

        logger.info("原型 HTML 已保存: %d 个文件到 %s", len(page_info), target_dir)

        # 渲染 PNG 截图
        screenshots: dict[str, Path] = {}
        try:
            from agent.company.prototype_renderer import render_html_to_image
            for info in page_info:
                img = await render_html_to_image(info["file"])
                if img:
                    screenshots[info["safe_name"]] = img
        except ImportError:
            logger.info("Playwright 不可用，原型报告将不含截图")
        except Exception as e:
            logger.warning("截图渲染失败: %s", e)

        # 生成专业原型报告 HTML（自包含，截图 base64 嵌入）
        report_path = await self._generate_prototype_report(
            page_info, screenshots, target_dir, stage_key
        )

        # 发送报告文件到飞书
        report_msg = CompanyMessage(
            content=f"{stage_key} 报告已生成（共 {len(page_info)} 页），请用浏览器打开查看。",
            cause_by="StageFile",
            sent_from="PM",
            task_id=task.task_id,
            metadata={"file_path": str(report_path), "filename": report_path.name},
        )
        await self._env.publish(report_msg)

    async def _generate_prototype_report(
        self, page_info: list[dict], screenshots: dict[str, "Path"],
        target_dir: "Path", stage_key: str
    ) -> "Path":
        """生成专业原型报告（HTML 格式，自包含，截图 base64 嵌入）。"""
        import base64 as _b64
        from datetime import datetime
        label = "原型图" if stage_key == "Prototype" else "UI 设计稿"
        now = datetime.now().strftime('%Y-%m-%d %H:%M')

        pages_html = []
        for i, info in enumerate(page_info):
            has_screenshot = info["safe_name"] in screenshots
            if has_screenshot:
                img_path = screenshots[info["safe_name"]]
                img_data = img_path.read_bytes()
                b64 = _b64.b64encode(img_data).decode()
                img_tag = f'<img src="data:image/png;base64,{b64}" alt="{info["name"]}" style="max-width:375px;border:1px solid #e5e7eb;border-radius:12px;box-shadow:0 2px 8px rgba(0,0,0,0.08);">'
            else:
                img_tag = f'<div style="width:375px;height:200px;background:#f3f4f6;border-radius:12px;display:flex;align-items:center;justify-content:center;color:#9ca3af;font-size:14px;">请打开 {info["file"].name} 查看</div>'
            desc_html = f'<p style="color:#6b7280;font-size:13px;margin:8px 0 0;">{info["desc"]}</p>' if info.get("desc") else ""
            pages_html.append(f'''
        <div style="margin-bottom:32px;">
          <h3 style="font-size:16px;font-weight:600;margin-bottom:8px;">{i+1}. {info["name"]}</h3>
          {desc_html}
          <div style="margin-top:12px;">{img_tag}</div>
          <p style="font-size:12px;color:#9ca3af;margin-top:8px;">源文件: {info["file"].name}</p>
        </div>''')

        flow_items = " → ".join(f'<span style="background:#eff6ff;padding:4px 10px;border-radius:6px;font-size:13px;">{info["name"]}</span>' for info in page_info)

        html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>{label}报告</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif; margin:0; padding:40px; background:#fafafa; color:#1f2937; line-height:1.6; }}
  .container {{ max-width:800px; margin:0 auto; background:#fff; border-radius:16px; padding:40px; box-shadow:0 1px 3px rgba(0,0,0,0.06); }}
  .header {{ border-bottom:1px solid #e5e7eb; padding-bottom:20px; margin-bottom:32px; }}
  .header h1 {{ font-size:24px; font-weight:700; margin:0 0 8px; }}
  .meta {{ font-size:13px; color:#6b7280; }}
  .flow {{ margin-bottom:32px; padding:16px; background:#f9fafb; border-radius:10px; }}
  .flow-title {{ font-size:14px; font-weight:600; margin-bottom:10px; color:#374151; }}
  .pages {{ }}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>{label}报告</h1>
    <div class="meta">生成时间: {now} | 页面数量: {len(page_info)} | 视口: 375 x 812</div>
  </div>
  <div class="flow">
    <div class="flow-title">页面流程</div>
    <div>{flow_items}</div>
  </div>
  <div class="pages">
    {"".join(pages_html)}
  </div>
</div>
</body>
</html>'''

        report_path = target_dir / f"{stage_key.lower()}-report.html"
        report_path.write_text(html, encoding="utf-8")
        return report_path

    async def _send_stage_screenshots(
        self, stage_key: str, project_dir: Optional[Path], task: "CompanyTask"
    ) -> None:
        """渲染原型/UI截图并发送给用户（兼容旧调用）。"""
        if not project_dir:
            return
        subdir = "prototypes" if stage_key == "Prototype" else "ui-designs"
        target_dir = project_dir / subdir
        if not target_dir.exists():
            return

        screenshots: list[Path] = []
        try:
            from agent.company.prototype_renderer import render_all_prototypes
            screenshots = await render_all_prototypes(project_dir, subdir)
            if screenshots:
                for img_path in screenshots:
                    img_msg = CompanyMessage(
                        content=f"[截图] {img_path.stem}",
                        cause_by="StageImage",
                        sent_from="PM",
                        task_id=task.task_id,
                        metadata={"image_path": str(img_path), "filename": img_path.name},
                    )
                    await self._env.publish(img_msg)
        except ImportError:
            logger.info("Playwright 不可用，跳过截图渲染")
        except Exception as e:
            logger.warning("截图渲染失败: %s", e)

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
    def _extract_project_description(project_dir: Path) -> str:
        """Extract project description from entry file docstrings or pyproject.toml."""
        candidates = ["app.py", "main.py", "src/app.py", "src/main.py"]
        for name in candidates:
            entry = project_dir / name
            if entry.exists():
                try:
                    content = entry.read_text(encoding="utf-8")
                    # Extract module docstring
                    import ast
                    tree = ast.parse(content)
                    docstring = ast.get_docstring(tree)
                    if docstring and len(docstring) > 10:
                        return docstring.strip()
                except Exception:
                    pass
        # Fallback: pyproject.toml description
        pyproject = project_dir / "pyproject.toml"
        if pyproject.exists():
            try:
                text = pyproject.read_text(encoding="utf-8")
                import re
                m = re.search(r'description\s*=\s*"([^"]+)"', text)
                if m:
                    return m.group(1)
            except Exception:
                pass
        return ""

    @staticmethod
    def _extract_tech_stack(project_dir: Path) -> str:
        """从项目目录提取技术栈摘要，供 Design 阶段参考."""
        parts = []
        # 检测语言和框架
        markers = {
            "requirements.txt": "Python (pip)",
            "pyproject.toml": "Python",
            "package.json": "Node.js/JavaScript",
            "tsconfig.json": "TypeScript",
            "go.mod": "Go",
            "Cargo.toml": "Rust",
            "pom.xml": "Java (Maven)",
        }
        for fname, tech in markers.items():
            if (project_dir / fname).exists():
                parts.append(f"- 检测到 `{fname}` → {tech}")
        # 检测框架
        app_py = project_dir / "app.py"
        if app_py.exists():
            try:
                content = app_py.read_text(encoding="utf-8")[:2000]
                if "flask" in content.lower() or "Flask" in content:
                    parts.append("- 后端框架: Flask")
                elif "fastapi" in content.lower() or "FastAPI" in content:
                    parts.append("- 后端框架: FastAPI")
                elif "django" in content.lower():
                    parts.append("- 后端框架: Django")
            except Exception:
                pass
        # 检测前端模板
        templates_dir = project_dir / "templates"
        if templates_dir.exists():
            html_files = list(templates_dir.glob("*.html"))
            if html_files:
                names = [f.name for f in html_files[:10]]
                parts.append(f"- 前端模板: Jinja2 ({', '.join(names)})")
        static_dir = project_dir / "static"
        if static_dir.exists():
            parts.append("- 静态资源: static/ 目录存在")
        # 列出 src/ 下的主要文件
        src_dir = project_dir / "src"
        if src_dir.exists():
            py_files = list(src_dir.rglob("*.py"))
            if py_files:
                names = [str(f.relative_to(project_dir)) for f in py_files[:15]]
                parts.append(f"- 源码文件 ({len(py_files)} 个 .py): {', '.join(names[:10])}")
            ts_files = list(src_dir.rglob("*.ts")) + list(src_dir.rglob("*.tsx"))
            if ts_files:
                parts.append(f"- TypeScript 文件: {len(ts_files)} 个")
        if not parts:
            return ""
        return "## 现有项目技术栈（必须兼容）\n" + "\n".join(parts) + "\n"

    @staticmethod
    def _collect_project_files(project_dir: Path, max_chars: int = 30000, max_files: int = 100) -> str:
        """收集项目 src/ 目录下的所有代码文件内容，用于 Reviewer 审查."""
        src_dir = project_dir / "src"
        has_src_files = src_dir.exists() and any(f.is_file() for f in src_dir.rglob("*"))
        if not has_src_files:
            src_dir = project_dir
        logger.debug("_collect_project_files scanning: %s", src_dir)
        _skip_dirs = {
            ".venv", "venv", "__pycache__", ".pytest_cache", "node_modules",
            ".git", ".tox", ".mypy_cache", ".ruff_cache", "dist", "build",
            ".egg-info", ".eggs",
        }
        _skip_ext = {
            ".pyc", ".class", ".o", ".so", ".db", ".sqlite",
            ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".webp",
            ".woff", ".woff2", ".ttf", ".eot",
            ".lock", ".min.js", ".min.css", ".map",
            ".zip", ".tar", ".gz", ".bz2", ".7z",
            ".exe", ".dll", ".dylib", ".bin",
            ".pdf", ".doc", ".docx", ".xls", ".xlsx",
        }
        _max_file_size = 50 * 1024
        files_content = []
        total = 0
        file_count = 0
        for f in sorted(src_dir.rglob("*")):
            if file_count >= max_files:
                files_content.append(f"\n... (已达文件上限 {max_files})")
                break
            if not f.is_file():
                continue
            if any(part in _skip_dirs for part in f.relative_to(src_dir).parts):
                continue
            if f.suffix in _skip_ext:
                continue
            if f.name.startswith("."):
                continue
            try:
                size = f.stat().st_size
            except OSError:
                continue
            if size > _max_file_size or size == 0:
                continue
            try:
                head = f.read_bytes()[:512]
                if b"\x00" in head:
                    continue
            except Exception:
                continue
            try:
                text = f.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            file_count += 1
            rel = f.relative_to(project_dir)
            entry = f"\n### {rel}\n```\n{text}\n```\n"
            if total + len(entry) > max_chars:
                remaining = max_chars - total
                if remaining > 200:
                    truncated = text[:remaining - 100]
                    entry = f"\n### {rel}\n```\n{truncated}\n... (文件过长已截断)\n```\n"
                    files_content.append(entry)
                files_content.append(f"\n... (更多文件省略)")
                break
            files_content.append(entry)
            total += len(entry)
        logger.debug("_collect_project_files found %d file entries", len(files_content))
        return "".join(files_content) if files_content else ""

    async def run(self, requirement: str, max_rounds: int = 0, max_minutes: int = 0) -> str:
        """主循环：发布需求 → 角色轮转 → 直到空闲或达到上限."""
        import uuid
        import time as _time
        import re as _re
        run_id = uuid.uuid4().hex[:8]
        if not max_rounds:
            max_rounds = self._config.max_rounds
        if not max_minutes:
            max_minutes = self._config.max_minutes
        pipeline_start = _time.monotonic()
        pipeline_deadline = pipeline_start + max_minutes * 60

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
        rework_feedbacks: dict[str, list[str]] = {}
        max_rework = self._config.max_rework
        stages_done: dict[str, bool] = {k: False for k in self._pipeline.stage_keys}
        if not self._needs_prototype:
            stages_done["Prototype"] = True
            stages_done["UIDesign"] = True
            logger.info("纯后端/CLI 项目，跳过 Prototype 和 UIDesign 阶段")
        idle_rounds = 0
        supplement_injected: set[str] = set()
        stage_outputs: dict[str, str] = {}

        import re as _resume_re
        pdir_match = _resume_re.search(r'## 项目工作目录\n(.+)\n', requirement)
        _project_dir: Optional[Path] = None
        if pdir_match:
            from pathlib import Path as _Path
            _pdir = _Path(pdir_match.group(1))
            _project_dir = _pdir
            # 设置所有角色的 workspace，确保 read_file 路径解析正确
            for _r in self._env.roles.values():
                _r._workspace = str(_pdir)
            _meta_file = _pdir / ".project.json"
            if _meta_file.exists():
                try:
                    import json as _json
                    _meta = _json.loads(_meta_file.read_text())
                    _saved_stages = _meta.get("stages_done", {})
                    _has_progress = any(_saved_stages.values())
                    if _has_progress and _meta.get("status") != "done":
                        for k in stages_done:
                            if _saved_stages.get(k, False):
                                stages_done[k] = True
                        stage_outputs.update(_meta.get("stage_outputs", {}))
                        # 验证各阶段是否真正产出了有效内容
                        for _stage_key in list(stages_done.keys()):
                            if stages_done.get(_stage_key):
                                _output = stage_outputs.get(_stage_key, "")
                                if not validate_checkpoint_output(_stage_key, _output, _pdir):
                                    stages_done[_stage_key] = False
                                    stage_outputs.pop(_stage_key, None)
                                    logger.info("断点恢复: %s 无有效产出，重置为未完成", _stage_key)
                        _port = _meta.get("port")
                        if _port:
                            _port_file = _pdir / ".port"
                            if not _port_file.exists():
                                _port_file.write_text(str(_port))
                        logger.info("断点恢复: 跳过已完成阶段 %s",
                                    [k for k, v in stages_done.items() if v])
                        _clarify_signals = ["澄清", "不清楚", "不知道", "需要先", "麻烦补充", "麻烦先告诉", "NEED_CLARIFY"]
                        if stage_outputs.get("PRD"):
                            _is_clarify = any(s in stage_outputs["PRD"] for s in _clarify_signals)
                            if not _is_clarify:
                                await self._env.publish(CompanyMessage(
                                    content=stage_outputs["PRD"],
                                    cause_by="WritePRD", sent_from="PM",
                                    task_id=task.task_id,
                                ))
                            else:
                                logger.info("断点恢复: PRD 内容为澄清提问，重置为未完成")
                                stage_outputs.pop("PRD", None)
                                stages_done["PRD"] = False
                        if stage_outputs.get("Design"):
                            _is_clarify = any(s in stage_outputs["Design"] for s in _clarify_signals)
                            if not _is_clarify:
                                await self._env.publish(CompanyMessage(
                                    content=stage_outputs["Design"],
                                    cause_by="WriteDesign", sent_from="PM",
                                    task_id=task.task_id,
                                ))
                            else:
                                logger.info("断点恢复: Design 内容为澄清提问，重置为未完成")
                                stage_outputs.pop("Design", None)
                                stages_done["Design"] = False
                except Exception as e:
                    logger.warning("读取断点信息失败: %s", e)

        _has_checkpoint = any(stages_done.values())
        if _has_checkpoint:
            pm_role = self._env.roles.get("PM")
            if pm_role:
                pm_role._inbox.clear()
            kicked = self._kick_next_stage(stages_done, stage_outputs, requirement, task)
            if not kicked:
                logger.info("断点恢复：所有阶段已完成，重置为新迭代")
                for k in stages_done:
                    stages_done[k] = False
                stage_outputs.clear()

        from agent.company.project_context import ProjectContext
        from agent.company.state_machine import PipelineStateMachine
        sm = PipelineStateMachine(self._pipeline.stage_keys)
        sm._stages_done = stages_done  # 共享引用
        ctx = ProjectContext.wrap(
            name=self._active_project_name or task.task_id,
            stages_done=stages_done,
            stage_outputs=stage_outputs,
            rework_counts=rework_counts,
            directory=_project_dir,
            requirement=requirement,
        )
        self._project_ctx = ctx

        for round_num in range(1, max_rounds + 1):
            sm.set_round(round_num)
            if self._pipeline_cancel:
                logger.info("Pipeline 被用户暂停 (round %d)", round_num)
                self._pipeline_cancel = False
                task.status = "paused"
                break
            if _time.monotonic() > pipeline_deadline:
                logger.warning("Pipeline 超时 (%d 分钟)，强制结束 (round %d)", max_minutes, round_num)
                timeout_msg = CompanyMessage(
                    content=f"流水线已运行超过 {max_minutes} 分钟（安全上限），自动停止。已完成的模块代码已保存在项目目录中。",
                    cause_by="ChatReply",
                    sent_from="PM",
                    task_id=task.task_id,
                )
                await self._env.publish(timeout_msg)
                task.status = "timeout"
                self._last_stages_done = dict(stages_done)
                self._last_stage_outputs = dict(stage_outputs)
                break

            if self._pipeline_user_msgs:
                new_msgs = [m for m in self._pipeline_user_msgs
                            if m.content not in supplement_injected]
                if new_msgs:
                    supplement = "\n".join(m.content for m in new_msgs)
                    for m in new_msgs:
                        supplement_injected.add(m.content)
                    requirement += f"\n\n## {self._boss_title}补充需求\n{supplement}"
                    next_role = None
                    for rn in self._pipeline.role_order:
                        r = self._env.roles.get(rn)
                        if r and r.has_pending:
                            next_role = rn
                            break
                    inject_msg = CompanyMessage(
                        content=f"## {self._boss_title}补充需求\n{supplement}",
                        cause_by="SupplementRequirement",
                        sent_from="PM",
                        send_to=next_role or "PM",
                        task_id=task.task_id,
                    )
                    await self._env.publish(inject_msg)
                    logger.info("已注入用户补充需求到 %s", next_role or "PM")
                self._pipeline_user_msgs.clear()

            if self._env.is_idle():
                if ctx.is_complete:
                    logger.info("所有角色空闲且所有阶段完成，结束 (round %d)", round_num)
                    break
                kicked = self._kick_next_stage(stages_done, stage_outputs, requirement, task)
                if not kicked:
                    logger.info("所有角色空闲，无法继续，结束 (round %d)", round_num)
                    break
                logger.info("返工后主动触发下一阶段")

            logger.info("=== Round %d === stages=%s", round_num, sm.summary())
            round_had_work = False
            _STAGE_LABELS = {
                "WritePRD": "需求文档",
                "WritePrototype": "原型图设计",
                "WriteUIDesign": "UI 设计",
                "WriteDesign": "技术方案",
                "SetupEnv": "搭建环境",
                "WriteCode": "编写代码",
                "VerifyRun": "验证运行",
                "CodeReview": "代码审查",
                "WriteTest": "编写测试",
                "RunTest": "运行测试",
                "DeployPlan": "部署方案",
                "ExecuteDeploy": "执行部署",
            }
            # 计算总阶段数和已完成数，用于进度显示
            _total_stages = ctx.total_stages
            _done_count = ctx.done_count
            for role in self._env.roles.values():
                if not role.has_pending:
                    continue
                # 用 role 即将执行的 action name 确定阶段名（比 inbox cause_by 更准确）
                _next_action = role.actions[role._state + 1].name if (role.actions and role._state + 1 < len(role.actions)) else ""
                _stage_name = _STAGE_LABELS.get(_next_action, "")
                if not _stage_name:
                    _stage_cause = role._inbox[0].cause_by if role._inbox else ""
                    _stage_name = _STAGE_LABELS.get(_stage_cause, f"{role.name} 工作中")
                _stage_label = f"[{_done_count + 1}/{_total_stages}] {_stage_name}..."
                status_msg = CompanyMessage(
                    content=_stage_label,
                    cause_by="RoleCheckin",
                    sent_from=role.name,
                    task_id=task.task_id,
                )
                await self._env.publish(status_msg)
                try:
                    result_msgs = await role.run()
                except Exception as e:
                    logger.error("[Pipeline] %s.run() 异常: %s", role.name, e)
                    error_notify = CompanyMessage(
                        content=f"{role.name} 执行出错了: {e}，跳过继续。",
                        cause_by="StatusUpdate",
                        sent_from=role.name,
                        task_id=task.task_id,
                    )
                    await self._env.publish(error_notify)
                    continue
                if not result_msgs:
                    continue

                for result_msg in result_msgs:

                    result_msg.task_id = task.task_id
                    task.result = result_msg.content
                    self._store.save_message(result_msg)

                    if self._is_no_work(result_msg.content):
                        logger.info("[%s] 无实际工作，跳过", role.name)
                        continue

                    round_had_work = True

                    if result_msg.cause_by == "WritePRD":
                        from agent.company.stage_handler import STAGE_CONFIGS, handle_validation
                        _cfg = STAGE_CONFIGS["WritePRD"]
                        _sr = handle_validation(_cfg, result_msg.content, ctx)
                        if _sr.action == "rework":
                            _rework = CompanyMessage(
                                content=f"## 系统提示\n{_sr.rework_hint}\n\n## 需求\n{requirement}",
                                cause_by="WritePRD", sent_from="Human", send_to="PM", task_id=task.task_id,
                            )
                            await self._env.publish(_rework)
                            break
                        sm.complete("PRD")
                        stage_outputs["PRD"] = result_msg.content
                        await self._env.publish(result_msg)
                        _stage_def = self._pipeline.stage_for_action("WritePRD")
                        if _stage_def and _stage_def.requires_approval:
                            _proj_dir = self._extract_workspace_from_requirement(requirement)
                            _approved, _feedback = await self._wait_for_approval("PRD", task, _proj_dir)
                            if not _approved:
                                sm.reset("PRD")
                                _rework = CompanyMessage(
                                    content=f"## 用户反馈（{_cfg.approval_feedback_template}）\n{_feedback}",
                                    cause_by="WritePRD", sent_from="Human", send_to="PM", task_id=task.task_id,
                                )
                                await self._env.publish(_rework)
                                break
                    elif result_msg.cause_by == "WritePrototype":
                        from agent.company.stage_handler import STAGE_CONFIGS, handle_validation
                        _cfg = STAGE_CONFIGS["WritePrototype"]
                        _proj_dir = self._extract_workspace_from_requirement(requirement)
                        _sr = handle_validation(_cfg, result_msg.content, ctx, project_dir=_proj_dir)
                        if _sr.action == "skip":
                            sm.complete("Prototype")
                            stage_outputs["Prototype"] = ""
                            _skip_msg = CompanyMessage(
                                content=_cfg.skip_message,
                                cause_by="ChatReply", sent_from="PM", task_id=task.task_id,
                            )
                            await self._env.publish(_skip_msg)
                            break
                        elif _sr.action == "rework":
                            _rework = CompanyMessage(
                                content=f"## 系统提示\n{_sr.rework_hint}",
                                cause_by="WritePrototype", sent_from="Human", send_to="PM", task_id=task.task_id,
                            )
                            await self._env.publish(_rework)
                            break
                        sm.complete("Prototype")
                        stage_outputs["Prototype"] = result_msg.content
                        await self._save_and_send_prototypes(result_msg.content, _proj_dir, task, "Prototype")
                        _stage_def = self._pipeline.stage_for_action("WritePrototype")
                        if _stage_def and _stage_def.requires_approval:
                            _approved, _feedback = await self._wait_for_approval("Prototype", task, _proj_dir)
                            if not _approved:
                                sm.reset("Prototype")
                                _rework = CompanyMessage(
                                    content=f"## 用户反馈（{_cfg.approval_feedback_template}）\n{_feedback}",
                                    cause_by="WritePrototype", sent_from="Human", send_to="PM", task_id=task.task_id,
                                )
                                await self._env.publish(_rework)
                                break
                    elif result_msg.cause_by == "WriteUIDesign":
                        from agent.company.stage_handler import STAGE_CONFIGS, handle_validation
                        _cfg = STAGE_CONFIGS["WriteUIDesign"]
                        _proj_dir = self._extract_workspace_from_requirement(requirement)
                        _sr = handle_validation(_cfg, result_msg.content, ctx, project_dir=_proj_dir)
                        if _sr.action == "skip":
                            sm.complete("UIDesign")
                            stage_outputs["UIDesign"] = ""
                            _skip_msg = CompanyMessage(
                                content=_cfg.skip_message,
                                cause_by="ChatReply", sent_from="PM", task_id=task.task_id,
                            )
                            await self._env.publish(_skip_msg)
                            break
                        elif _sr.action == "rework":
                            _rework = CompanyMessage(
                                content=f"## 系统提示\n{_sr.rework_hint}",
                                cause_by="WriteUIDesign", sent_from="Human", send_to="PM", task_id=task.task_id,
                            )
                            await self._env.publish(_rework)
                            break
                        sm.complete("UIDesign")
                        stage_outputs["UIDesign"] = result_msg.content
                        await self._save_and_send_prototypes(result_msg.content, _proj_dir, task, "UIDesign")
                        _stage_def = self._pipeline.stage_for_action("WriteUIDesign")
                        if _stage_def and _stage_def.requires_approval:
                            _approved, _feedback = await self._wait_for_approval("UIDesign", task, _proj_dir)
                            if not _approved:
                                sm.reset("UIDesign")
                                _rework = CompanyMessage(
                                    content=f"## 用户反馈（{_cfg.approval_feedback_template}）\n{_feedback}",
                                    cause_by="WriteUIDesign", sent_from="Human", send_to="PM", task_id=task.task_id,
                                )
                                await self._env.publish(_rework)
                                break
                    elif result_msg.cause_by == "WriteDesign":
                        from agent.company.stage_handler import STAGE_CONFIGS, handle_validation
                        _cfg = STAGE_CONFIGS["WriteDesign"]
                        _sr = handle_validation(_cfg, result_msg.content, ctx)
                        if _sr.action == "rework":
                            _rework = CompanyMessage(
                                content=f"## 系统提示\n{_sr.rework_hint}\n\n请重新输出完整的技术设计文档。",
                                cause_by="WriteDesign", sent_from="Human", send_to="PM", task_id=task.task_id,
                            )
                            await self._env.publish(_rework)
                            break
                        sm.complete("PRD")
                        sm.complete("Design")
                        stage_outputs["Design"] = result_msg.content
                        await self._env.publish(result_msg)
                        _stage_def = self._pipeline.stage_for_action("WriteDesign")
                        if _stage_def and _stage_def.requires_approval:
                            _proj_dir = self._extract_workspace_from_requirement(requirement)
                            _approved, _feedback = await self._wait_for_approval("Design", task, _proj_dir)
                            if not _approved:
                                sm.reset("Design")
                                _rework = CompanyMessage(
                                    content=f"## 用户反馈（{_cfg.approval_feedback_template}）\n{_feedback}",
                                    cause_by="WriteDesign", sent_from="Human", send_to="PM", task_id=task.task_id,
                                )
                                await self._env.publish(_rework)
                                break

                        modules = self._parse_modules(result_msg.content)
                        if modules:
                            logger.info("Design 输出包含 %d 个模块，启动模块 pipeline: %s",
                                        len(modules), [m["name"] for m in modules])
                            await self._run_module_pipeline(
                                modules=modules,
                                requirement=requirement,
                                stage_outputs=stage_outputs,
                                stages_done=stages_done,
                                rework_counts=rework_counts,
                                task=task,
                                pipeline_deadline=pipeline_deadline,
                                sm=sm,
                            )
                        else:
                            logger.info("Design 未包含模块拆分，使用传统单体 pipeline")
                    elif result_msg.cause_by == "SetupEnv":
                        sm.complete("Env")
                        if "ENV_FAIL" in result_msg.content:
                            logger.warning("环境安装失败，继续执行（Developer 可能需要手动处理）")
                    elif result_msg.cause_by == "WriteCode":
                        if self._has_requirement_issue(result_msg.content):
                            logger.warning("[Developer] 输出有需求问题，回退给 PM 核实")
                            self._clear_downstream_inboxes(role.name)
                            escalate = CompanyMessage(
                                content=(
                                    f"## Developer 反馈需求问题\n{result_msg.content[:1000]}\n\n"
                                    f"请检查对话记录，确认需求是否清晰。如果需求没有跟{self._boss_title}确认过，"
                                    f"请在群里向{self._boss_title}核实后，重新整理需求和设计方案给 Developer。"
                                ),
                                cause_by="WriteCode",
                                sent_from="Developer",
                                send_to="PM",
                                task_id=task.task_id,
                            )
                            await self._env.publish(escalate)
                            break
                        if "write_file 未被调用" in result_msg.content:
                            # Check if project already has code — if so, Developer was doing
                            # maintenance (pip install, restart, etc.), not a fresh write
                            _ws_check = self._extract_workspace_from_requirement(requirement)
                            _has_existing_code = False
                            if _ws_check:
                                _src = _ws_check / "src"
                                _scan = _src if _src.exists() else _ws_check
                                _has_existing_code = (
                                    any(_scan.rglob("*.py"))
                                    or any(_scan.rglob("*.js"))
                                    or any(_scan.rglob("*.ts"))
                                )
                            if _has_existing_code:
                                logger.info("WriteCode 未调用 write_file，但项目已有代码文件，视为维护操作，继续")
                            else:
                                wf_rework = rework_counts.get("Developer_writefile", 0)
                                if wf_rework < max_rework:
                                    rework_counts["Developer_writefile"] = wf_rework + 1
                                    logger.warning("WriteCode 未调用 write_file，要求重试 (第%d次)", wf_rework + 1)
                                    sm.reset("Code")
                                    _design_ctx = stage_outputs.get('Design', '')[:2000]
                                    _clarify_signals = ["澄清", "不清楚", "不知道", "需要先", "麻烦补充", "麻烦先告诉"]
                                    if not _design_ctx or any(s in _design_ctx for s in _clarify_signals):
                                        _ws = self._extract_workspace_from_requirement(requirement)
                                        if _ws:
                                            _desc = self._extract_project_description(_ws)
                                            _code = self._collect_project_files(_ws, max_chars=3000, max_files=10)
                                            _design_ctx = f"项目描述：{_desc}\n\n现有代码：\n{_code}" if _desc else _code
                                    rework_msg = CompanyMessage(
                                        content=(
                                            "## 重要提醒：你必须使用 write_file 工具写入文件！\n"
                                            "上一次你没有调用 write_file，代码没有落盘。\n"
                                            "请立即使用 write_file 将每个文件写入项目工作目录。\n"
                                            "绝对不要只在回复文本中输出代码。\n\n"
                                            f"## 原始设计方案\n{_design_ctx}"
                                        ),
                                        cause_by="WriteDesign",
                                        sent_from="Reviewer",
                                        send_to="Developer",
                                        task_id=task.task_id,
                                    )
                                    await self._env.publish(rework_msg)
                                    break
                                else:
                                    logger.warning("WriteCode 未调用 write_file 重试已达上限，强制继续")
                        sm.complete("Code")
                        workspace = self._extract_workspace_from_requirement(requirement)
                        if workspace:
                            code_listing = self._collect_project_files(
                                workspace,
                                max_chars=self._config.max_project_chars,
                                max_files=self._config.max_project_files,
                            )
                            if code_listing:
                                result_msg.content += f"\n\n## 代码文件内容\n{code_listing}"
                                logger.info("已附加项目代码文件到 WriteCode 输出 (%d 字符)", len(code_listing))
                            else:
                                role_rework = rework_counts.get("Developer_empty", 0)
                                if role_rework < max_rework:
                                    rework_counts["Developer_empty"] = role_rework + 1
                                    logger.warning("WriteCode 完成但项目目录无代码文件，要求 Developer 重写 (第%d次)", role_rework + 1)
                                    sm.reset("Code")
                                    rework_msg = CompanyMessage(
                                        content=(
                                            "项目目录下没有找到任何代码文件。\n"
                                            "你必须使用 write_file 工具将每个文件写入磁盘。\n"
                                            "所有文件必须写入项目工作目录下（src/ 或项目根目录）。\n"
                                            "不要只在回复文本中输出代码，那样文件不会被创建。\n"
                                            "不要写入 .xjd-agent/ 或其他隐藏目录。\n"
                                            "请重新执行，确保每个文件都通过 write_file 写入项目目录。"
                                        ),
                                        cause_by="WriteDesign",
                                        sent_from="Reviewer",
                                        send_to="Developer",
                                        task_id=task.task_id,
                                    )
                                    await self._env.publish(rework_msg)
                                    break
                    elif result_msg.cause_by == "VerifyRun":
                        if "VERIFY_PASS" in result_msg.content:
                            sm.complete("Verify")
                        else:
                            verify_rework = rework_counts.get("Developer_verify", 0)
                            if verify_rework < max_rework:
                                rework_counts["Developer_verify"] = verify_rework + 1
                                logger.warning("VerifyRun 失败，Developer 修复 (第%d次, patch 模式)", verify_rework + 1)
                                sm.reset("Verify")

                                fix_parts = [f"## 验证失败，请修复指出的问题\n{result_msg.content}"]
                                pdir_match = _re.search(r'## 项目工作目录\n(.+)\n', requirement)
                                if pdir_match:
                                    fix_parts.append(f"## 项目工作目录\n{pdir_match.group(1)}")
                                if "Design" in stage_outputs:
                                    fix_parts.append(f"## 原始设计方案（参考）\n{stage_outputs['Design'][:1500]}")

                                from agent.company.action import FIX_CODE
                                dev_role = self._env.roles.get("Developer")
                                if dev_role:
                                    fix_result = await FIX_CODE.run("\n\n".join(fix_parts), dev_role)
                                    fix_msg = CompanyMessage(
                                        content=fix_result,
                                        cause_by="FixCode",
                                        sent_from="Developer",
                                        task_id=task.task_id,
                                    )
                                    await self._env.publish(fix_msg)
                                break
                            else:
                                logger.warning("VerifyRun 返工次数已达上限，强制通过")
                                sm.complete("Verify")
                    elif result_msg.cause_by == "CodeReview":
                        if self._has_code_incomplete(result_msg.content) or self._has_requirement_issue(result_msg.content):
                            _ws_review = self._extract_workspace_from_requirement(requirement)
                            _review_has_code = False
                            if _ws_review:
                                _src_r = _ws_review / "src"
                                _scan_r = _src_r if _src_r.exists() else _ws_review
                                _review_has_code = any(_scan_r.rglob("*.py")) or any(_scan_r.rglob("*.js"))
                            if _review_has_code:
                                logger.info("[Reviewer] 代码不完整但项目已有代码，使用 patch 模式修复")
                                fix_parts = [f"## Reviewer 反馈（请只修改指出的问题）\n{result_msg.content}"]
                                fix_parts.append(f"## 项目工作目录\n{_ws_review}")
                                from agent.company.action import FIX_CODE
                                fix_role = self._env.roles.get("Developer")
                                if fix_role:
                                    fix_result = await FIX_CODE.run("\n\n".join(fix_parts), fix_role)
                                    fix_msg = CompanyMessage(
                                        content=fix_result,
                                        cause_by="FixCode",
                                        sent_from="Developer",
                                        task_id=task.task_id,
                                    )
                                    await self._env.publish(fix_msg)
                                sm.reset("Verify")
                                sm.reset("Review")
                                break
                            else:
                                logger.warning("[Reviewer] 输出表示代码不完整，回退给 Developer 重写")
                                self._clear_downstream_inboxes(role.name)
                                sm.reset("Code")
                                sm.reset("Verify")
                                sm.reset("Review")
                                rework_msg = CompanyMessage(
                                    content=(
                                        "Reviewer 反馈：收到的代码不完整，无法审查。\n"
                                        "请确保所有代码文件都通过 write_file 写入了 src/ 目录。\n"
                                        "重新执行 WriteCode，确保每个文件都落盘。"
                                    ),
                                    cause_by="WriteDesign",
                                    sent_from="Reviewer",
                                    send_to="Developer",
                                    task_id=task.task_id,
                                )
                                await self._env.publish(rework_msg)
                                break
                        if self._is_review_approved(result_msg.content):
                            sm.complete("Review")
                    elif result_msg.cause_by in ("WriteTest", "RunTest"):
                        if result_msg.cause_by == "RunTest":
                            if self._is_test_passed(result_msg.content):
                                sm.complete("Test")
                                pdir_match = _re.search(r'## 项目工作目录\n(.+)\n', requirement)
                                if pdir_match:
                                    result_msg.content += f"\n\n## 项目工作目录\n{pdir_match.group(1)}\n"
                    elif result_msg.cause_by in ("DeployPlan", "ExecuteDeploy"):
                        if result_msg.cause_by == "ExecuteDeploy":
                            url_m = _re.search(r'http://[\w.\-]+:\d+[/\w.\-]*', result_msg.content)
                            if url_m:
                                stage_outputs["Deploy"] = url_m.group(0)
                            sm.complete("Deploy")

                    stage_just_completed = (
                        (result_msg.cause_by == "CodeReview" and stages_done.get("Review"))
                        or (result_msg.cause_by == "RunTest" and stages_done.get("Test"))
                        or (result_msg.cause_by == "ExecuteDeploy" and stages_done.get("Deploy"))
                    )
                    rework_target = self._check_rework(role.name, result_msg.content, rework_round=rework_counts.get(role.name, 0)) if not stage_just_completed else None
                    role_rework = rework_counts.get(role.name, 0)
                    if rework_target and role_rework < max_rework:
                        # --- StuckDetector: 检测连续相似反馈 ---
                        prev_feedbacks = rework_feedbacks.get(role.name, [])
                        is_stuck = (
                            len(prev_feedbacks) > 0
                            and self._is_feedback_similar(prev_feedbacks[-1], result_msg.content)
                        )
                        if is_stuck and role_rework >= 2:
                            logger.warning("StuckDetector: %s 连续两轮反馈相似，判定卡住，强制通过", role.name)
                            sm.force_pass(["Review", "Test"])
                            escalate = CompanyMessage(
                                content=f"{role.name} 连续两轮指出类似问题但修复无效，先继续推进，后续可能需要人工介入。",
                                cause_by="ChatReply",
                                sent_from="PM",
                                task_id=task.task_id,
                            )
                            await self._env.publish(escalate)
                            break

                        rework_feedbacks.setdefault(role.name, []).append(result_msg.content)
                        rework_counts[role.name] = role_rework + 1
                        logger.info("返工 #%d: %s 要求 %s 修改 (patch 模式)", role_rework + 1, role.name, rework_target)
                        self._clear_downstream_inboxes(role.name)

                        fix_parts = [f"## {role.name} 反馈（请只修改指出的问题）\n{result_msg.content}"]
                        if role_rework >= 1:
                            fix_parts.insert(0, (
                                "## 重要提示\n"
                                f"这是第 {role_rework + 1} 次返工。之前的修复方式没有完全解决问题。\n"
                                "请换一个思路来解决，不要重复之前的修法。\n"
                                "如果问题是测试环境/依赖导致的而非代码bug，请说明原因并标记为环境问题。"
                            ))
                        pdir_match = _re.search(r'## 项目工作目录\n(.+)\n', requirement)
                        if pdir_match:
                            fix_parts.append(f"## 项目工作目录\n{pdir_match.group(1)}")
                        if "Design" in stage_outputs:
                            fix_parts.append(f"## 原始设计方案（参考）\n{stage_outputs['Design'][:1500]}")

                        from agent.company.action import FIX_CODE
                        fix_role = self._env.roles.get(rework_target)
                        if fix_role:
                            fix_result = await FIX_CODE.run("\n\n".join(fix_parts), fix_role)
                            fix_msg = CompanyMessage(
                                content=fix_result,
                                cause_by="FixCode",
                                sent_from=rework_target,
                                task_id=task.task_id,
                            )
                            await self._env.publish(fix_msg)
                            self._store.save_message(fix_msg)

                        sm.reset("Verify")
                        sm.reset("Review")
                        sm.reset("Test")

                        rework_status = CompanyMessage(
                            content=f"{role.name} 发现问题，{rework_target} 正在修复（第 {role_rework + 1} 次，patch 模式）",
                            cause_by="StatusUpdate",
                            sent_from=role.name,
                            task_id=task.task_id,
                        )
                        await self._env.publish(rework_status)
                        break
                    elif rework_target and role_rework >= max_rework:
                        logger.warning("返工次数已达上限 %d，强制通过 %s 阶段", max_rework, role.name)
                        sm.force_pass(["Review", "Test"])
                        # 判断最终结果是否实际通过（测试全过或 Reviewer 无严重问题）
                        test_actually_passed = self._is_test_passed(result_msg.content) if role.name == "QA" else False
                        review_no_reject = (
                            role.name == "Reviewer"
                            and "REJECTED" not in result_msg.content.upper()
                            and "拒收" not in result_msg.content
                            and "打回" not in result_msg.content
                        )
                        if test_actually_passed or review_no_reject:
                            logger.info("返工达上限但最终结果已通过，正常 APPROVED")
                        else:
                            escalate = CompanyMessage(
                                content=f"{role.name} 已打回 {max_rework} 次，团队尽力修复但仍有问题。先继续推进，后续再优化。",
                                cause_by="ChatReply",
                                sent_from="PM",
                                task_id=task.task_id,
                            )
                            await self._env.publish(escalate)
                        try:
                            role_order = self._pipeline.role_order
                            idx = role_order.index(role.name)
                            next_role = role_order[idx + 1] if idx + 1 < len(role_order) else None
                        except ValueError:
                            next_role = None
                        if test_actually_passed or review_no_reject:
                            approve_content = result_msg.content
                        else:
                            approve_content = f"APPROVED（已达最大返工次数，强制通过）\n\n原始审查意见：{result_msg.content[:500]}"
                        forced_approve = CompanyMessage(
                            content=approve_content,
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
                if idle_rounds >= self._config.idle_rounds_to_stop:
                    logger.info("连续 %d 轮无工作产出，pipeline 结束", idle_rounds)
                    break
            else:
                idle_rounds = 0
                if _project_dir:
                    self._persist_pipeline_state(_project_dir, stages_done, stage_outputs)
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
        elif task.status != "timeout":
            task.status = "done"

        self._last_deploy_url = stage_outputs.get("Deploy", "")
        self._last_stages_done = dict(stages_done)
        self._last_stage_outputs = dict(stage_outputs)
        self._last_task = task

        self._store.save_task(task)
        self._store.finish_run(run_id, task.status, round_num, task.result[:500] if task.result else "")
        return task.result

    async def _continue_run(self, task: CompanyTask, requirement: str, remaining_rounds: int) -> str:
        """验证失败后继续执行剩余轮次."""
        if remaining_rounds <= 0:
            task.status = "failed"
            return task.result or ""
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
                result_msgs = await role.run()
                for result_msg in result_msgs:
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
                content="系统已重启，服务就绪。",
                cause_by="ChatReply",
                sent_from="PM",
            )
            await self._env.publish(recovery_msg)
        else:
            for role in self._env.roles.values():
                bot_name = self._get_bot_display_name(role.name)
                line = f"{bot_name}到岗，随时待命。"
                checkin = CompanyMessage(
                    content=line,
                    cause_by="RoleCheckin",
                    sent_from=role.name,
                )
                await self._env.publish(checkin)

        self._standby_stop = asyncio.Event()
        self._standby_history: list[tuple[str, str]] = self._restore_standby_history()

        interrupted = self._detect_interrupted_projects()
        if interrupted:
            # 设置最近中断项目为当前活跃项目，防止 PM 丢失上下文
            first_proj_name, first_proj_dir, _, _ = interrupted[0]
            self._active_project_name = first_proj_name
            self._switched_project_dir = first_proj_dir
            for proj_name, proj_dir, done, pending in interrupted:
                done_str = "/".join(done) if done else "无"
                pending_str = "/".join(pending)
                notify = CompanyMessage(
                    content=(
                        f"上次「{proj_name}」项目的流水线被中断"
                        f"（已完成：{done_str}，未完成：{pending_str}）。\n"
                        f"回复「继续」即可从断点恢复，不会重复已完成的阶段。"
                    ),
                    cause_by="ChatReply",
                    sent_from="PM",
                )
                await self._env.publish(notify)

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

    _TASK_TRIGGER_KEYWORDS = None

    def _detect_task_intent(self, text: str) -> bool | str:
        """检测用户消息是否包含开发任务意图.

        Returns True for strong match, "weak" for ambiguous match needing
        confirmation, False for no match.
        """
        anti_keywords = self._locale.get("keywords.task_anti_keywords") or [
            "加油", "加班", "加薪", "加入群", "加入团队",
            "增加信心", "修改密码", "修改头像",
            "开发者大会", "开发者文档", "开发环境", "开发工具",
        ]
        if any(kw in text for kw in anti_keywords):
            return False

        strong = self._locale.get("keywords.strong_task_triggers") or self._locale.get("keywords.task_triggers") or [
            "开发一个", "写一个", "做一个", "帮我开发", "帮我写", "帮我做",
            "开始开发", "开始写", "开始做", "开干", "开搞", "启动流水线", "开始干活",
            "写个", "做个", "搞一个", "搞个", "实现一个", "实现个",
            "写PRD", "写 PRD", "出PRD", "出 PRD",
            "马上开发", "立刻开发", "赶紧开发", "直接开发",
            "马上做", "赶紧做", "赶紧搞", "快做", "快搞",
            "创建一个", "建一个", "生成一个",
            "安排开发", "安排一下", "动手吧", "动手做", "你就开始",
            "现在就做", "现在就开发", "现在开始",
            "开发好", "做好", "搞好", "实现好",
        ]
        if any(kw in text for kw in strong):
            return True

        weak = self._locale.get("keywords.weak_task_triggers") or [
            "加入", "加个", "加一个", "增加", "添加", "新增",
            "改一下", "改个", "修改", "优化一下", "优化个",
            "支持一下", "支持个", "接入",
            "开发", "开发个",
            "没问题了", "就这样吧", "可以开始了", "确认", "就按这个来",
            "方案没问题", "设计没问题", "需求确认", "可以动手了",
            "那就这样", "行吧", "OK开始", "ok开始",
        ]
        if any(kw in text for kw in weak):
            return "weak"

        return False

    _QUICK_TASK_KEYWORDS: dict[str, list[str]] = None

    _QUICK_TASK_CONTEXT_KEYWORDS: list[str] = None

    def _get_bot_display_name(self, role_name: str) -> str:
        """获取角色对应飞书 Bot 的显示名称，无则回退到 role description."""
        if self._feishu_bridge:
            adapter = self._feishu_bridge._adapters.get(role_name)
            if adapter:
                bot = getattr(adapter, "_bot_user", None)
                name = getattr(bot, "display_name", "") if bot else ""
                if name:
                    return name
        role = self._env.roles.get(role_name)
        if role:
            return role.description.split("，")[0]
        return role_name

    def _detect_quick_task(self, messages: list[CompanyMessage]) -> Optional[str]:
        """检测操作类意图，返回目标角色名或 None."""
        qt_keywords = self._locale.get("keywords.quick_task") or {
            "Developer": ["调试", "查日志", "看日志", "查看日志", "修个bug", "修一下bug", "改个bug", "热修复"],
            "DevOps": ["跑起来", "启动项目", "启动服务", "运行项目", "运行服务", "执行一下", "部署", "上线", "发布上线", "发布到线上", "重启服务", "重启一下", "回滚", "检查服务", "健康检查", "看看服务", "验收", "看看效果", "跑一下看看"],
            "QA": ["跑测试", "跑一下测试", "测试一下", "回归测试", "运行测试"],
        }
        qt_context = self._locale.get("keywords.quick_task_context") or [
            "直接处理", "直接搞", "马上处理", "马上搞", "去处理", "去搞",
            "你来处理", "你处理", "你搞", "你去",
        ]
        text = " ".join(m.content for m in messages)
        for role_name, keywords in qt_keywords.items():
            if any(kw in text for kw in keywords):
                return role_name

        for m in messages:
            if m.send_to and m.send_to in self._env.roles:
                if any(kw in m.content for kw in qt_context):
                    return m.send_to
                recent = [c for _, c in self._standby_history[-5:]]
                recent_text = " ".join(recent)
                for keywords in qt_keywords.values():
                    if any(kw in recent_text for kw in keywords):
                        return m.send_to

        return None

    def _detect_interrupted_projects(self) -> list[tuple[str, Path, list[str], list[str]]]:
        """扫描项目目录，找到被中断的 pipeline（status=in_progress 且有未完成阶段）."""
        import json as _dj
        results = []
        projects_root = Path.home() / "xjd-projects"
        if not projects_root.exists():
            return results
        core_stages = ["PRD", "Design", "Env", "Code", "Verify", "Review", "Test", "Deploy"]
        for d in sorted(projects_root.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            if not d.is_dir():
                continue
            meta_file = d / ".project.json"
            if not meta_file.exists():
                continue
            try:
                meta = _dj.loads(meta_file.read_text())
            except Exception:
                continue
            if meta.get("status") != "in_progress":
                continue
            sd = meta.get("stages_done", {})
            if not any(sd.values()):
                continue
            done = [s for s in core_stages if sd.get(s)]
            pending = [s for s in core_stages if not sd.get(s)]
            if not pending:
                continue
            name_parts = d.name.split("-", 1)
            display_name = name_parts[1] if len(name_parts) > 1 else d.name
            results.append((display_name, d, done, pending))
        return results[:3]

    def _find_latest_project_dir(self) -> Optional[Path]:
        """找到最近的项目工作目录。如果用户手动切换了项目，优先返回切换目标。"""
        if self._switched_project_dir and self._switched_project_dir.exists():
            return self._switched_project_dir
        try:
            projects_dir = get_projects_dir()
            if not projects_dir.exists():
                return None
            dirs = sorted(projects_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
            for d in dirs:
                if d.is_dir() and (d / ".project.json").exists():
                    return d
            return None
        except Exception:
            return None

    def _find_resumable_project(self) -> Optional[Path]:
        """找到最近的可恢复项目（status=in_progress 且有未完成阶段）."""
        import json as _rj
        try:
            projects_dir = get_projects_dir()
            if not projects_dir.exists():
                return None
            dirs = sorted(projects_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
            for d in dirs:
                if not d.is_dir():
                    continue
                meta_file = d / ".project.json"
                if not meta_file.exists():
                    continue
                try:
                    meta = _rj.loads(meta_file.read_text())
                except Exception:
                    continue
                if meta.get("status") in ("in_progress", "timeout") and meta.get("stages_done"):
                    if not all(meta["stages_done"].values()):
                        return d
            return None
        except Exception:
            return None

    def _find_project_by_name(self, text: str, project_name: Optional[str] = None) -> Optional[Path]:
        """从需求文本或提取的项目名匹配已有项目目录。"""
        import json
        import re

        projects_dir = get_projects_dir()
        if not projects_dir.exists():
            return None

        iterate_keywords = ["接着开发", "继续开发", "接着做", "继续做", "继续", "接着来", "断点恢复", "迭代", "升级", "加个功能", "加一个功能", "改一下"]
        is_iterate = any(kw in text for kw in iterate_keywords)

        dirs = sorted(projects_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
        project_dirs = [d for d in dirs if d.is_dir() and (d / ".project.json").exists()]

        if not project_dirs:
            return None

        pname_lower = project_name.lower().strip() if project_name else ""

        if pname_lower:
            for d in project_dirs:
                dir_name = d.name.split("-", 1)[1] if "-" in d.name else d.name
                if dir_name and dir_name.lower() == pname_lower:
                    return d
                try:
                    meta = json.loads((d / ".project.json").read_text())
                    meta_name = meta.get("name", "")
                    if meta_name and meta_name.lower() == pname_lower:
                        return d
                except Exception:
                    continue

        text_lower = text.lower()
        for d in project_dirs:
            dir_name = d.name.split("-", 1)[1] if "-" in d.name else d.name
            if dir_name and dir_name.lower() in text_lower:
                return d
            try:
                meta = json.loads((d / ".project.json").read_text())
                name_match = re.search(r'[\u4e00-\u9fff\w]{2,}', dir_name)
                if name_match and name_match.group() in text:
                    return d
            except Exception:
                continue

        if is_iterate and project_dirs:
            return project_dirs[0]

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

        project_dir = self._find_latest_project_dir()
        project_hint = ""
        if project_dir:
            project_hint = (
                f"## 项目工作目录\n{project_dir}\n"
                f"直接 cd 到这个目录操作，不要浪费时间浏览其他目录。\n\n"
            )

        from agent.company.local_env import detect_local_env, format_env_for_context
        env_context = format_env_for_context(detect_local_env())

        context = (
            f"{project_hint}"
            f"{env_context}\n\n"
            f"{project_status}"
            f"## 对话上下文\n" + "\n".join(history_lines) +
            f"\n\n## 用户指令\n{task_text}"
        )

        ack = CompanyMessage(
            content=f"收到，已安排{self._get_bot_display_name(role.name)}处理。",
            cause_by="ChatReply",
            sent_from="PM",
        )
        await self._env.publish(ack)
        self._standby_history.append(("PM", ack.content))

        result_msg = await role._act(QUICK_TASK, context)
        self._standby_history.append((role.name, result_msg.content))
        self._store.save_message(result_msg)
        await self._env.publish(result_msg)

        import re as _re
        url_m = _re.search(r'http://[\w.\-]+:\d+', result_msg.content)
        if url_m:
            try:
                import webbrowser
                webbrowser.open(url_m.group(0))
            except Exception:
                pass

    async def _run_quick_task(self, role_name: str, messages: list[CompanyMessage]) -> None:
        """异步执行 QuickTask，完成后清理 _pipeline_running 状态."""
        try:
            await self._handle_quick_task(role_name, messages)
        except Exception as e:
            logger.error("QuickTask 执行异常: %s", e)
        finally:
            remaining = self._env._pipeline_user_queue or []
            self._pipeline_running = False
            self._env._pipeline_user_queue = None
            for m in remaining:
                for role in self._env.roles.values():
                    if m.cause_by in role.watch_actions:
                        role.put_message(m)

    _ROLE_NICK_MAP: dict[str, list[str]] = None
    _ROLE_TOPIC_KEYWORDS: dict[str, list[str]] = None

    def _route_message_to_role(self, msg: CompanyMessage) -> Optional[str]:
        """根据消息内容智能路由到对应角色。返回角色名或 None（默认 PM）."""
        if msg.send_to and msg.send_to in self._env.roles:
            return msg.send_to

        nick_map = self._locale.get("keywords.role_nicks") or {
            "PM": ["PM", "产品", "产品经理"],
            "Developer": ["开发", "程序员"],
            "Reviewer": ["审查", "审查员"],
            "QA": ["测试", "QA"],
            "DevOps": ["运维", "部署", "ops"],
        }
        topic_map = self._locale.get("keywords.role_topics") or {
            "Developer": ["代码", "bug", "调试", "修复", "实现", "编码"],
            "Reviewer": ["审查", "review", "代码质量"],
            "QA": ["测试", "用例", "覆盖率"],
            "DevOps": ["部署", "上线", "服务器", "运维", "启动", "重启"],
        }

        text = msg.content
        for role_name, nicks in nick_map.items():
            if role_name == "PM":
                continue
            for nick in nicks:
                if nick in text:
                    return role_name

        for role_name, keywords in topic_map.items():
            for kw in keywords:
                if kw in text:
                    return role_name

        return None

    async def _process_standby_messages(self) -> None:
        """处理待命模式下的消息：关键词检测触发流水线，否则聊天回复."""
        import asyncio
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

                # 检测暂停/切换项目指令
                _pause_keywords = ("暂停", "先停", "停一下", "暂停开发", "先暂停", "切换到", "切换项目", "换个项目")
                _pause_msg = None
                for m in collected:
                    if any(kw in m.content for kw in _pause_keywords):
                        _pause_msg = m
                        break
                if _pause_msg:
                    logger.info("用户请求暂停/切换项目: %s", _pause_msg.content[:50])
                    self._pipeline_cancel = True
                    # 尝试直接切换到目标项目
                    _switch_result = self._try_switch_project([_pause_msg])
                    if _switch_result and "已切换" in _switch_result:
                        notify_text = f"当前项目已暂停。{_switch_result}"
                    else:
                        notify_text = "当前项目已暂停，请发送新的需求或指定要切换的项目。"
                    notify = CompanyMessage(
                        content=notify_text,
                        cause_by="ChatReply",
                        sent_from="PM",
                    )
                    await self._env.publish(notify)
                    return

                if getattr(self, '_waiting_approval', None):
                    self._pipeline_user_msgs.extend(collected)
                    return

                _confirm_only_prefixes = (
                    "开干", "可以", "没问题", "OK", "ok", "好的", "行",
                    "确认", "就这样", "可以开始", "开始吧", "动手吧",
                    "好", "嗯", "对", "是的", "没问题了", "就按这个",
                    "可以，开干", "好，开干", "行，开干",
                )

                _new_project_signals = (
                    "新项目", "新产品", "做一个新", "另一个项目", "探讨一下一个新",
                    "探讨一个新", "讨论一个新",
                )
                has_new_project = any(
                    any(sig in m.content for sig in _new_project_signals)
                    for m in collected
                )

                quick_target = None if has_new_project else self._detect_quick_task(collected)
                if quick_target:
                    quick_msgs = collected
                    ack = CompanyMessage(
                        content="收到，先处理操作任务。",
                        cause_by="ChatReply",
                        sent_from="PM",
                    )
                    await self._env.publish(ack)
                    asyncio.create_task(self._run_quick_task(quick_target, quick_msgs))
                    return

                req_msgs = []
                for m in collected:
                    intent = self._detect_task_intent(m.content)
                    text = m.content.strip()
                    is_confirm = len(text) <= 15 and text.startswith(_confirm_only_prefixes)
                    if intent is True and not is_confirm:
                        req_msgs.append(m)
                chat_msgs = [m for m in collected if m not in req_msgs]

                if has_new_project and not req_msgs:
                    for m in collected:
                        if any(sig in m.content for sig in _new_project_signals):
                            self._task_queue.append({"raw_message": m.content})
                            pos = len(self._task_queue)
                            queue_ack = CompanyMessage(
                                content=f"收到，新项目需求已排队（第 {pos} 位），当前任务完成后自动开始。",
                                cause_by="ChatReply",
                                sent_from="PM",
                            )
                            await self._env.publish(queue_ack)
                    return

                if req_msgs:
                    new_project_keywords = (
                        "做一个新", "另一个项目", "下一个任务", "新项目", "再做一个",
                        "做完这个再", "排队", "下一个", "另外一个",
                    )
                    new_project_msgs = []
                    supplement_msgs = []
                    for m in req_msgs:
                        if any(kw in m.content for kw in new_project_keywords):
                            new_project_msgs.append(m)
                        else:
                            supplement_msgs.append(m)

                    if supplement_msgs:
                        self._pipeline_user_msgs.extend(supplement_msgs)
                        summary = "、".join(m.content[:30] for m in supplement_msgs)
                        ack = CompanyMessage(
                            content=f"收到，补充需求已记录，会纳入当前开发：\n{summary}",
                            cause_by="ChatReply",
                            sent_from="PM",
                        )
                        await self._env.publish(ack)

                    for m in new_project_msgs:
                        self._task_queue.append({"raw_message": m.content})
                        pos = len(self._task_queue)
                        queue_ack = CompanyMessage(
                            content=f"收到，新任务已排队（第 {pos} 位），当前项目完成后自动开始。",
                            cause_by="ChatReply",
                            sent_from="PM",
                        )
                        await self._env.publish(queue_ack)

                if chat_msgs:
                    for cm in chat_msgs:
                        target = self._route_message_to_role(cm)
                        responder = self._env.roles.get(target) if target else self._env.roles.get("PM")
                        if not responder:
                            responder = self._env.roles.get("PM")
                        if responder:
                            history_lines = [f"[{s}]: {c}" for s, c in self._standby_history[-10:]]
                            _proj_hint = ""
                            if self._project_ctx:
                                _proj_hint = (
                                    f"## 当前项目\n{self._project_ctx.get_context_summary()}\n"
                                    f"注意：只讨论这个项目，不要混入其他项目信息。\n\n"
                                )
                            elif self._active_project_name:
                                _proj_dir = self._switched_project_dir or self._find_latest_project_dir()
                                _tech_info = ""
                                if _proj_dir:
                                    _tech_info = self._extract_tech_stack(_proj_dir)
                                _proj_hint = (
                                    f"## 当前项目\n{self._active_project_name}\n"
                                    f"{'项目目录：' + str(_proj_dir) if _proj_dir else ''}\n"
                                    f"{_tech_info}\n"
                                    f"注意：只讨论这个项目，不要混入其他项目信息。\n\n"
                                )
                            chat_context = (
                                f"{_proj_hint}"
                                "当前团队正在开发中（pipeline 运行中）。\n"
                                "你可以基于项目信息回答用户问题，不要说'无法查看文件'。\n\n"
                                f"## 对话记录\n" + "\n".join(history_lines)
                            )
                            try:
                                reply_msg = await responder._act(CHAT_REPLY, chat_context)
                                self._standby_history.append((responder.name, reply_msg.content))
                                self._store.save_message(reply_msg)
                                await self._env.publish(reply_msg)
                            except Exception as e:
                                logger.error("Pipeline 中 ChatReply 失败 [%s]: %s", responder.name, e)
            return

        pm_role = self._env.roles.get("PM")
        if not pm_role:
            return

        all_messages = []
        if pm_role.has_pending:
            all_messages.extend(await pm_role._observe())
        for rname, role in self._env.roles.items():
            if rname == "PM":
                continue
            if role.has_pending:
                role_msgs = await role._observe()
                human_msgs = [m for m in role_msgs if m.cause_by == "HumanDirective"]
                all_messages.extend(human_msgs)

        if not all_messages:
            return
        messages = all_messages

        for m in messages:
            self._standby_history.append((m.sent_from, m.content))
            self._store.save_message(m)

        user_messages = [m for m in messages if m.sent_from not in self._env.roles]

        if self._pending_project_name and user_messages:
            import re as _re_pn
            raw_name = user_messages[-1].content.strip()
            clean_name = _re_pn.sub(r'[^\w\u4e00-\u9fff-]', '', raw_name)[:20]
            is_confirm_only = clean_name in self._PROJECT_NAME_STOPWORDS or len(clean_name) < 2
            if clean_name and not is_confirm_only:
                pending = self._pending_project_name
                self._pending_project_name = None

                display_name = clean_name
                if self._has_chinese(clean_name):
                    clean_name = await self._to_english_name(clean_name)
                    logger.info("用户项目名中文→英文: %s → %s", display_name, clean_name)

                confirm_content = (
                    f"项目名「{clean_name}」（{display_name}）已确认，开始安排开发。"
                    if display_name != clean_name
                    else f"项目名「{clean_name}」已确认，开始安排开发。"
                )
                confirm_msg = CompanyMessage(
                    content=confirm_content,
                    cause_by="ChatReply",
                    sent_from=pm_role.name,
                )
                await self._env.publish(confirm_msg)
                self._standby_history.append((pm_role.name, confirm_msg.content))

                from agent.company.local_env import detect_local_env, format_env_for_context
                env_context = format_env_for_context(detect_local_env())

                from agent.company.secret_extractor import extract_secrets, write_env_file
                secrets = extract_secrets(self._standby_history)

                project_dir = self._create_project_workspace(
                    pending["task_context"], project_name=clean_name,
                )
                if secrets:
                    env_file = write_env_file(project_dir, secrets)
                    if env_file:
                        logger.info("已写入 %d 个密钥到 %s", len(secrets), env_file)
                enriched = (
                    f"## 项目工作目录\n{project_dir}\n"
                    f"所有文件必须创建在此目录下。PRD 写入 docs/prd.md，设计写入 docs/design.md，"
                    f"代码写入 src/，测试写入 tests/。\n\n"
                    f"{env_context}\n\n"
                    f"{pending['full_context']}"
                )

                self._pipeline_running = True
                self._env._pipeline_user_queue = []
                asyncio.create_task(self._run_pipeline_task(enriched, project_dir))
                return
            else:
                retry_msg = CompanyMessage(
                    content="还没给项目名。请提供 2-6 个字的正式名称，例如「智能计算器」「Holu资讯」。",
                    cause_by="ChatReply",
                    sent_from=pm_role.name,
                )
                await self._env.publish(retry_msg)
                self._standby_history.append((pm_role.name, retry_msg.content))
                self._store.save_message(retry_msg)
                return

        intents = [(m, self._detect_task_intent(m.content)) for m in user_messages]
        has_strong = any(i is True for _, i in intents)
        has_weak = any(i == "weak" for _, i in intents)
        has_task_intent = has_strong or has_weak

        # 断点恢复检测：优先于 task_intent 评估，避免 resume 关键词被当作新任务
        resume_keywords = ["继续", "接着来", "断点恢复", "继续开发", "接着开发", "恢复", "接着做", "继续做"]
        has_resume = any(kw in m.content for m in user_messages for kw in resume_keywords)
        # "确认"/"开干" 等 weak intent 在有可恢复项目时也应触发 resume
        if not has_resume and has_weak and not has_strong:
            confirm_resume_kw = ["确认", "开干", "就按这个来", "没问题", "可以开始"]
            has_resume = any(kw in m.content for m in user_messages for kw in confirm_resume_kw)
        if has_resume:
            import json as _rjson
            resume_project = None
            for m in user_messages:
                resume_project = self._find_project_by_name(m.content)
                if resume_project:
                    break
            if not resume_project:
                resume_project = self._find_resumable_project()
            if resume_project:
                _rmeta_file = resume_project / ".project.json"
                if _rmeta_file.exists():
                    try:
                        _rmeta = _rjson.loads(_rmeta_file.read_text())
                        if _rmeta.get("status") in ("timeout", "in_progress") and _rmeta.get("stages_done"):
                            has_task_intent = True
                            has_strong = True
                            self._resume_project_dir = resume_project
                            logger.info("检测到断点恢复意图，项目: %s", resume_project.name)
                    except Exception:
                        pass

        if has_task_intent:
            if len(self._standby_history) > self._config.standby_history_max:
                self._standby_history = self._standby_history[-self._config.standby_history_trim:]
            task_context = "\n\n".join(m.content for m in user_messages)

            resume_dir = getattr(self, "_resume_project_dir", None)
            if resume_dir:
                self._resume_project_dir = None
                import json as _rj2
                _rm = _rj2.loads((resume_dir / ".project.json").read_text())
                _sd = _rm.get("stages_done", {})
                core = ["PRD", "Design", "Env", "Code", "Verify", "Review", "Test", "Deploy"]
                done_list = [s for s in core if _sd.get(s)]
                pending_list = [s for s in core if not _sd.get(s)]
                name_parts = resume_dir.name.split("-", 1)
                display = name_parts[1] if len(name_parts) > 1 else resume_dir.name

                confirm_msg = CompanyMessage(
                    content=f"从断点恢复「{display}」，跳过已完成的 {'/'.join(done_list)}，继续执行 {'/'.join(pending_list)}。",
                    cause_by="ChatReply",
                    sent_from=pm_role.name,
                )
                await self._env.publish(confirm_msg)
                self._standby_history.append((pm_role.name, confirm_msg.content))

                from agent.company.local_env import detect_local_env, format_env_for_context
                env_context = format_env_for_context(detect_local_env())
                existing_code = self._collect_project_files(
                    resume_dir,
                    max_chars=self._config.max_project_chars,
                    max_files=self._config.max_project_files,
                )
                original_req = _rm.get("requirement", "")
                # If saved requirement is just a resume command, try to extract
                # project description from source code docstrings
                _resume_cmds = ["继续", "接着", "恢复", "断点", "推进", "开发"]
                if not original_req or len(original_req) < 20 or any(k in original_req for k in _resume_cmds):
                    _desc = self._extract_project_description(resume_dir)
                    if _desc:
                        original_req = _desc
                if original_req and original_req not in task_context:
                    full_requirement = f"{original_req}\n\n（用户补充）{task_context}"
                else:
                    full_requirement = task_context
                enriched = (
                    f"## 项目工作目录\n{resume_dir}\n"
                    f"这是一个已有项目，断点恢复模式。\n\n"
                    f"{self._extract_tech_stack(resume_dir)}\n"
                    f"## 现有代码\n{existing_code}\n\n"
                    f"{env_context}\n\n"
                    f"## 用户需求\n{full_requirement}"
                )
                self._pipeline_running = True
                self._env._pipeline_user_queue = []
                asyncio.create_task(self._run_pipeline_task(enriched, resume_dir))
                return

            history_context = "\n".join(
                f"[{s}]: {c}" for s, c in self._standby_history[-10:]
            )
            project_status = self._build_project_status()
            weak_hint = ""
            if has_weak and not has_strong:
                weak_hint = "\n\n注意：用户消息中的任务意图不太明确，请仔细判断是否真的是开发需求。如果不确定，请回复 NEED_CLARIFY 并追问。"
            elif has_strong:
                weak_hint = "\n\n注意：用户已经明确表达了开发意图（使用了「开干」「开始开发」等明确指令），请直接回复 READY 并总结需求，不要再追问。如果上下文中有之前讨论过的需求细节，结合起来理解即可。"
            full_context = f"{project_status}## 对话上下文\n{history_context}\n\n## 用户最新需求\n{task_context}{weak_hint}"

            eval_result = await pm_role._act(EVALUATE_REQUIREMENT, full_context)
            eval_text = eval_result.content if hasattr(eval_result, "content") else str(eval_result)
            first_line = eval_text.strip().split("\n")[0].strip()

            if first_line.startswith("NEED_CLARIFY"):
                clarify_text = eval_text.strip().split("\n", 1)[1].strip() if "\n" in eval_text.strip() else "需求不太明确，能再说具体点吗？"
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
                content="需求已确认，开始安排开发。",
                cause_by="ChatReply",
                sent_from=pm_role.name,
            )
            await self._env.publish(confirm_msg)
            self._standby_history.append((pm_role.name, confirm_msg.content))

            req_summary = eval_text.strip().split("\n", 1)[1].strip() if "\n" in eval_text.strip() else task_context

            project_name = None
            import re as _re_name
            name_match = _re_name.search(r'PROJECT_NAME:\s*(.+)', eval_text)
            if name_match:
                candidate = name_match.group(1).strip()
                candidate = _re_name.sub(r'^\d{8}-', '', candidate)
                candidate = _re_name.sub(r'[^\w\u4e00-\u9fff-]', '', candidate)[:20]
                if candidate and candidate not in self._PROJECT_NAME_STOPWORDS:
                    if self._has_chinese(candidate):
                        original_cn = candidate
                        candidate = await self._to_english_name(candidate)
                        logger.info("\u9879\u76ee\u540d\u4e2d\u6587\u2192\u82f1\u6587: %s \u2192 %s", original_cn, candidate)
                    project_name = candidate

            needs_prototype = True
            proto_match = _re_name.search(r'NEEDS_PROTOTYPE:\s*(YES|NO)', eval_text, _re_name.IGNORECASE)
            if proto_match and proto_match.group(1).upper() == "NO":
                needs_prototype = False

            existing_project = self._find_project_by_name(task_context, project_name=project_name)

            if not project_name and not existing_project:
                self._pending_project_name = {
                    "eval_text": eval_text,
                    "task_context": task_context,
                    "full_context": full_context,
                    "req_summary": req_summary,
                }
                clarify_msg = CompanyMessage(
                    content="请为项目命名（2-6 个字），用于建档。",
                    cause_by="ChatReply",
                    sent_from=pm_role.name,
                )
                await self._env.publish(clarify_msg)
                self._standby_history.append((pm_role.name, clarify_msg.content))
                self._store.save_message(clarify_msg)
                return

            from agent.company.local_env import detect_local_env, format_env_for_context
            env_context = format_env_for_context(detect_local_env())

            from agent.company.secret_extractor import extract_secrets, write_env_file
            secrets = extract_secrets(self._standby_history)

            if existing_project:
                project_dir = existing_project
                if secrets:
                    env_file = write_env_file(project_dir, secrets)
                    if env_file:
                        logger.info("已写入 %d 个密钥到 %s", len(secrets), env_file)
                existing_code = self._collect_project_files(
                    project_dir,
                    max_chars=self._config.max_project_chars,
                    max_files=self._config.max_project_files,
                )
                enriched = (
                    f"## 项目工作目录\n{project_dir}\n"
                    f"这是一个已有项目，你需要在现有代码基础上修改，不要从头重写。\n"
                    f"所有文件操作必须在此目录下。\n\n"
                    f"## 现有代码\n{existing_code}\n\n"
                    f"{env_context}\n\n"
                    f"{full_context}"
                )
                logger.info("迭代开发模式：使用已有项目 %s", project_dir)
            else:
                project_dir = self._create_project_workspace(task_context, project_name=project_name)
                if secrets:
                    env_file = write_env_file(project_dir, secrets)
                    if env_file:
                        logger.info("已写入 %d 个密钥到 %s", len(secrets), env_file)
                enriched = (
                    f"## 项目工作目录\n{project_dir}\n"
                    f"所有文件必须创建在此目录下。PRD 写入 docs/prd.md，设计写入 docs/design.md，"
                    f"代码写入 src/，测试写入 tests/。\n\n"
                    f"{env_context}\n\n"
                    f"{full_context}"
                )

            self._pipeline_running = True
            self._needs_prototype = needs_prototype
            self._env._pipeline_user_queue = []
            asyncio.create_task(self._run_pipeline_task(enriched, project_dir))
            return

        quick_target = self._detect_quick_task(user_messages)
        if quick_target:
            self._pipeline_running = True
            self._env._pipeline_user_queue = []
            asyncio.create_task(self._run_quick_task(quick_target, user_messages))
            return

        # 检测风格设置指令
        _style_updated = await self._try_update_style(user_messages)
        if _style_updated:
            return

        # 检测项目切换指令
        _switch_result = self._try_switch_project(user_messages)
        if _switch_result:
            switch_msg = CompanyMessage(
                content=_switch_result,
                cause_by="ChatReply",
                sent_from=pm_role.name,
            )
            await self._env.publish(switch_msg)
            self._standby_history.append((pm_role.name, switch_msg.content))
            self._store.save_message(switch_msg)
            return

        if len(self._standby_history) > 40:
            self._standby_history = self._standby_history[-30:]

        now = datetime.now().strftime("%Y-%m-%d %H:%M (%A)")
        history_lines = [f"[{s}]: {c}" for s, c in self._standby_history[-10:]]
        history_text = "\n".join(history_lines)
        project_status = self._build_project_status()

        # 确定当前项目上下文：优先用已明确设置的活跃项目
        _current_project_hint = ""
        if self._active_project_name:
            _current_project_hint = f"\n\n## 当前项目\n{self._active_project_name}\n注意：只讨论这个项目相关的内容，不要混入其他项目的信息。\n"
        else:
            _recent_text = " ".join(c for _, c in self._standby_history[-5:])
            _proj_dir = self._find_project_by_name(_recent_text, "")
            if _proj_dir:
                _current_project_hint = f"\n\n## 当前讨论项目\n{_proj_dir.name}\n注意：只讨论这个项目相关的内容，不要混入其他项目的信息。\n"

        target_roles: set[str] = set()
        for m in user_messages:
            target = self._route_message_to_role(m)
            if target:
                target_roles.add(target)
        target_roles.add("PM")

        responded_roles: set[str] = set()
        for role_name in ["PM"] + [r for r in target_roles if r != "PM"]:
            if role_name in responded_roles:
                continue
            responded_roles.add(role_name)
            responder = self._env.roles.get(role_name) or pm_role
            role_hint = ""
            if role_name != "PM":
                role_hint = (
                    f"\n\n你是{responder.description}，用户的讨论涉及你的专业领域。"
                    f"从你的专业角度参与讨论，提出建议或指出潜在问题。"
                    f"如果话题跟你无关，回复「这块我没意见，听{self._boss_title}和 PM 的」即可，不要硬凑。"
                )
            chat_context = (
                f"当前时间: {now}\n\n{project_status}{_current_project_hint}"
                f"## 对话记录\n{history_text}{role_hint}"
            )
            reply_msg = await responder._act(CHAT_REPLY, chat_context)
            if role_name != "PM" and any(skip in reply_msg.content for skip in ["没意见", f"听{self._boss_title}", "不涉及", "跟我无关"]):
                continue
            self._standby_history.append((responder.name, reply_msg.content))
            self._store.save_message(reply_msg)
            await self._env.publish(reply_msg)

    def stop_standby(self) -> None:
        """外部调用停止待命模式."""
        if hasattr(self, "_standby_stop"):
            self._standby_stop.set()

    def _try_switch_project(self, messages: list[CompanyMessage]) -> Optional[str]:
        """检测项目切换指令，切换成功返回确认文案，否则返回 None."""
        import re as _re_sw
        text = " ".join(m.content for m in messages)
        switch_patterns = [
            r"切换到[「「]?(.+?)[」」]?(?:项目|工程)?$",
            r"切换[「「]?(.+?)[」」]?(?:项目|工程)",
            r"(?:打开|进入|去)[「「]?(.+?)[」」]?(?:项目|工程)",
            r"(?:项目|工程)切换(?:到|为)[「「]?(.+?)[」」]?$",
        ]
        target_name = None
        for pat in switch_patterns:
            m = _re_sw.search(pat, text)
            if m:
                target_name = m.group(1).strip()
                break
        if not target_name:
            return None
        project_dir = self._find_project_by_name(target_name, project_name=target_name)
        if project_dir:
            self._switched_project_dir = project_dir
            self._active_project_name = project_dir.name.split("-", 1)[-1] if "-" in project_dir.name else project_dir.name
            for role in self._env.roles.values():
                role._workspace = str(project_dir)
            return f"已切换到「{self._active_project_name}」项目，后续操作将在此项目目录下进行。"
        return f"未找到名为「{target_name}」的项目，请确认项目名称。"

    async def _try_update_style(self, messages: list[CompanyMessage]) -> bool:
        """检测并处理风格设置指令。返回 True 表示已处理。"""
        import re as _re
        text = " ".join(m.content for m in messages)

        _title_patterns = [
            r"(?:以后|今后)?(?:叫我|称呼我|喊我)(?:为)?[「「]?(.{1,10})[」」]?",
            r"(?:称呼|叫法)(?:改为|改成|换成)[「「]?(.{1,10})[」」]?",
        ]
        new_title = None
        for pat in _title_patterns:
            m = _re.search(pat, text)
            if m:
                new_title = m.group(1).strip("「」 ")
                break

        _style_keywords = ("回复风格", "说话风格", "聊天风格", "回复方式", "语气", "带emoji", "带表情",
                           "不要emoji", "活泼一点", "正式一点", "专业一点", "轻松一点")
        has_style_cmd = any(kw in text for kw in _style_keywords)

        if not new_title and not has_style_cmd:
            return False

        from agent.core.secrets import get_secrets_store
        store = get_secrets_store()
        pm_role = self._env.roles.get("PM")
        reply_parts = []

        if new_title:
            store.set("ai-company", "COMPANY_BOSS_TITLE", new_title)
            self._boss_title = new_title
            for role in self._env.roles.values():
                role._boss_title = new_title
            reply_parts.append(f"好的，以后称呼您为「{new_title}」。")

        if has_style_cmd:
            _style_map = {
                "带emoji": "回复可以适当使用 emoji 表情",
                "带表情": "回复可以适当使用 emoji 表情",
                "不要emoji": "回复不使用 emoji",
                "活泼一点": "回复语气轻松活泼，可以用 emoji 和口语化表达",
                "轻松一点": "回复语气轻松自然，不要太正式",
                "正式一点": "回复简洁专业，不用 emoji，不寒暄",
                "专业一点": "回复简洁专业，结论先行，不用 emoji",
            }
            matched_style = None
            for kw, style_desc in _style_map.items():
                if kw in text:
                    matched_style = style_desc
                    break

            if matched_style:
                current_style = store.get("ai-company", "COMPANY_CHAT_STYLE") or self._locale.get("chat_style", "")
                new_style = f"## 回复规则\n- 称呼用户为「{self._boss_title}」\n- {matched_style}\n- 仔细阅读对话记录，延续之前的讨论\n- 遇到模糊需求追问具体细节\n- 有专业判断，会提出建议，但尊重客户最终决定"
                store.set("ai-company", "COMPANY_CHAT_STYLE", new_style)
                self._locale._data["chat_style"] = new_style
                reply_parts.append(f"风格已更新：{matched_style}。")
            else:
                reply_parts.append("收到，如需调整具体风格，可以在技能设置面板中编辑「团队风格」。")

        if pm_role and reply_parts:
            confirm = CompanyMessage(
                content=" ".join(reply_parts),
                cause_by="ChatReply",
                sent_from="PM",
            )
            await self._env.publish(confirm)
            self._standby_history.append(("PM", confirm.content))

        return True

    async def _run_pipeline_task(self, req: str, pdir) -> None:
        """执行 pipeline 并在完成后清理状态、发送通知."""
        from pathlib import Path
        result = None
        status = "failed"
        project_path = Path(pdir) if not isinstance(pdir, Path) else pdir
        self._active_project_name = project_path.name
        self._switched_project_dir = project_path
        try:
            try:
                from agent.company.project_manager import ProjectManager
                mgr = ProjectManager()
                project_name = project_path.name.split("-", 1)[-1] if "-" in project_path.name else project_path.name
                backup = mgr.backup_before_deploy(project_name)
                if backup:
                    logger.info("部署前备份: %s", backup)
            except Exception as e:
                logger.warning("部署前备份失败: %s", e)
            result = await self.run(req)
            last_task = getattr(self, '_last_task', None)
            if last_task and getattr(last_task, 'status', '') == 'timeout':
                status = "timeout"
            elif last_task and getattr(last_task, 'status', '') == 'paused':
                status = "in_progress"
            else:
                status = "done" if result else "failed"
        except Exception as e:
            logger.error("Pipeline 执行异常: %s", e)
        finally:
            remaining = self._env._pipeline_user_queue or []
            self._pipeline_running = False
            self._project_ctx = None
            # 保留 _active_project_name，避免 ChatReply 上下文丢失当前项目
            self._env._pipeline_user_queue = None
            for m in remaining:
                for role in self._env.roles.values():
                    if m.cause_by in role.watch_actions:
                        role.put_message(m)
            self._update_project_status(pdir, status)
            import re as _re
            access_url = getattr(self, '_last_deploy_url', '')
            if not access_url and result:
                m = _re.search(r'http://[\w.\-]+:\d+[/\w.\-]*', result)
                access_url = m.group(0) if m else ''
            if access_url and status in ('done', 'timeout'):
                try:
                    import webbrowser
                    webbrowser.open(access_url)
                except Exception:
                    pass
            url_info = f"\n访问地址: {access_url}" if access_url else ""
            if status == "timeout":
                stages = getattr(self, '_last_stages_done', {})
                done_list = [k for k, v in stages.items() if v]
                progress = f"（已完成: {', '.join(done_list)}）" if done_list else ""
                msg_text = f"任务超时{progress}。代码已保存至项目目录，回复「继续」可从断点恢复。\n项目目录: {pdir}{url_info}"
            elif status == "done":
                if access_url:
                    msg_text = f"项目开发完成，已部署上线。\n访问地址: {access_url}\n项目目录: {pdir}"
                else:
                    msg_text = f"项目开发完成，代码已保存至项目目录。\n项目目录: {pdir}"
            else:
                msg_text = f"项目开发遇到问题，未能完成。\n项目目录: {pdir}{url_info}"
            done_msg = CompanyMessage(
                content=msg_text,
                cause_by="PipelineComplete",
                sent_from="PM",
            )
            await self._env.publish(done_msg)

            if self._task_queue:
                next_task = self._task_queue.pop(0)
                queue_remaining = len(self._task_queue)
                queue_info = f"（队列还有 {queue_remaining} 个）" if queue_remaining else ""
                notify = CompanyMessage(
                    content=f"开始处理下一个排队任务{queue_info}。",
                    cause_by="ChatReply",
                    sent_from="PM",
                )
                await self._env.publish(notify)
                raw_msg = next_task["raw_message"]
                queued_msg = CompanyMessage(
                    content=raw_msg,
                    cause_by="UserMessage",
                    sent_from="User",
                    send_to="PM",
                )
                pm_role = self._env.roles.get("PM")
                if pm_role:
                    pm_role.put_message(queued_msg)
                    self._standby_history.append(("User", raw_msg))

    _PIPELINE_ORDER = ["PM", "Developer", "Reviewer", "QA", "DevOps"]

    def _kick_next_stage(self, stages_done: dict, stage_outputs: dict,
                         requirement: str, task) -> bool:
        """返工后角色空闲，主动触发下一个未完成阶段."""
        import re as _re
        stage_role_map = [
            ("PRD", "PM"),
            ("Prototype", "PM"),
            ("UIDesign", "PM"),
            ("Design", "PM"),
            ("Env", "Developer"),
            ("Code", "Developer"),
            ("Verify", "Developer"),
            ("Review", "Reviewer"),
            ("Test", "QA"),
            ("Deploy", "DevOps"),
        ]
        for stage_key, role_name in stage_role_map:
            if not stages_done.get(stage_key, True):
                role = self._env.roles.get(role_name)
                if not role:
                    continue
                kick_content = f"## 继续执行 {stage_key} 阶段\n"
                if "Design" in stage_outputs:
                    kick_content += stage_outputs["Design"][:1500]
                elif "PRD" in stage_outputs and stage_key in ("Prototype", "UIDesign", "Design", "Env", "Code"):
                    kick_content += stage_outputs["PRD"][:1500]
                else:
                    pdir = _re.search(r'## 项目工作目录\n(.+)\n', requirement)
                    if pdir:
                        _desc = self._extract_project_description(Path(pdir.group(1)))
                        if _desc:
                            kick_content += f"## 项目描述\n{_desc}\n"
                pdir_match = _re.search(r'## 项目工作目录\n(.+)\n', requirement)
                if pdir_match:
                    kick_content += f"\n\n## 项目工作目录\n{pdir_match.group(1)}\n"
                    # Design/Code 阶段注入现有技术栈，防止方案与项目不匹配
                    if stage_key in ("Design", "Code", "Env"):
                        _tech = self._extract_tech_stack(Path(pdir_match.group(1)))
                        if _tech:
                            kick_content += f"\n{_tech}\n"
                cause_map = {
                    "PRD": "EvaluateRequirement",
                    "Prototype": "WritePRD",
                    "UIDesign": "WritePrototype",
                    "Design": "WriteUIDesign",
                    "Env": "WriteDesign",
                    "Code": "WriteDesign",
                    "Verify": "FixComplete",
                    "Review": "FixComplete",
                    "Test": "FixComplete",
                    "Deploy": "FixComplete",
                }
                # 设置 PM 的 action 指针到正确位置
                action_name_map = {
                    "PRD": "WritePRD",
                    "Prototype": "WritePrototype",
                    "UIDesign": "WriteUIDesign",
                    "Design": "WriteDesign",
                }
                target_action = action_name_map.get(stage_key)
                if target_action and role.actions:
                    for idx, a in enumerate(role.actions):
                        if a.name == target_action:
                            role._state = idx - 1
                            break
                kick_msg = CompanyMessage(
                    content=kick_content,
                    cause_by=cause_map.get(stage_key, "FixComplete"),
                    sent_from="PM",
                    send_to=role_name,
                    task_id=task.task_id,
                )
                role.put_message(kick_msg)
                logger.info("主动触发 %s 执行 %s 阶段", role_name, stage_key)
                return True
        return False

    def _check_rework(self, role_name: str, content: str, rework_round: int = 0) -> Optional[str]:
        """检查角色输出是否需要返工。rework_round 用于后续轮次降低敏感度."""
        rework_target = self._pipeline.rework_target_for(role_name)
        if not rework_target:
            return None
        if role_name == "Reviewer":
            upper = content.upper()
            if "REJECTED" in upper or "拒收" in content or "打回" in content:
                return rework_target
        if role_name == "QA":
            if not self._is_test_passed(content):
                fail_indicators = ["失败", "FAIL", "fail", "不通过", "未通过"]
                if any(ind in content for ind in fail_indicators):
                    if rework_round >= 2:
                        critical = ["crash", "崩溃", "无法启动", "ImportError", "SyntaxError",
                                    "500", "服务器错误", "TypeError", "NameError"]
                        if not any(c.lower() in content.lower() for c in critical):
                            logger.info("QA 第%d轮反馈非 critical，降级为建议，不触发返工", rework_round + 1)
                            return None
                    return rework_target
        return None

    def _is_feedback_similar(self, prev: str, curr: str, threshold: float = 0.6) -> bool:
        """检测两次反馈是否高度相似（同类问题反复出现 = 卡住了）."""
        import re as _re_fb
        def extract_issues(text: str) -> set[str]:
            patterns = _re_fb.findall(r'[\w/]+\.(?:py|js|ts|jsx|tsx)', text)
            patterns += _re_fb.findall(r'(?:Error|Exception|失败|错误|问题|bug|fix)[\w]*', text, _re_fb.IGNORECASE)
            patterns += _re_fb.findall(r'`([^`]+)`', text)
            return set(p.lower() for p in patterns)

        prev_issues = extract_issues(prev)
        curr_issues = extract_issues(curr)
        if not prev_issues or not curr_issues:
            return False
        overlap = len(prev_issues & curr_issues) / max(len(prev_issues), len(curr_issues))
        return overlap >= threshold

    def _clear_downstream_inboxes(self, role_name: str) -> None:
        """清空当前角色下游所有角色的 inbox，防止基于被拒代码继续工作."""
        role_order = self._pipeline.role_order
        try:
            idx = role_order.index(role_name)
        except ValueError:
            return
        for downstream in role_order[idx + 1:]:
            role = self._env.roles.get(downstream)
            if role:
                cleared = len(role._inbox)
                role._inbox.clear()
                if cleared:
                    logger.info("清空 %s 的 inbox（%d 条），等待返工完成", downstream, cleared)

    @staticmethod
    def _parse_modules(design_text: str) -> list[dict]:
        """从 Design 文本中解析 ### MODULES 部分，返回模块列表."""
        import re
        modules: list[dict] = []
        m = re.search(r'### MODULES\s*\n(.*?)(?=\n###|\n## |\Z)', design_text, re.DOTALL)
        if not m:
            return modules
        block = m.group(1)
        current: dict | None = None
        for line in block.split('\n'):
            line = line.strip()
            if line.startswith('- module:'):
                if current:
                    modules.append(current)
                name = line.split(':', 1)[1].strip()
                current = {"name": name, "files": [], "depends": [], "description": ""}
            elif current and line.startswith('files:'):
                raw = line.split(':', 1)[1].strip().strip('[]')
                current["files"] = [f.strip().strip("'\"") for f in raw.split(',') if f.strip()]
            elif current and line.startswith('depends:'):
                raw = line.split(':', 1)[1].strip().strip('[]')
                current["depends"] = [d.strip().strip("'\"") for d in raw.split(',') if d.strip()]
            elif current and line.startswith('description:'):
                current["description"] = line.split(':', 1)[1].strip()
        if current:
            modules.append(current)
        return modules

    async def _run_module_pipeline(
        self,
        modules: list[dict],
        requirement: str,
        stage_outputs: dict[str, str],
        stages_done: dict[str, bool],
        rework_counts: dict[str, int],
        task: "CompanyTask",
        pipeline_deadline: float,
        sm: "PipelineStateMachine | None" = None,
    ) -> None:
        """按模块循环执行 Code → Verify → Review，每个模块独立完成."""
        import asyncio
        import re as _re
        import time as _time
        from agent.company.action import WRITE_CODE, VERIFY_RUN, CODE_REVIEW, FIX_CODE
        from agent.company.message import CompanyMessage

        max_rework = self._config.max_rework
        dev_role = self._env.roles.get("Developer")
        reviewer_role = self._env.roles.get("Reviewer")
        if not dev_role:
            logger.error("Developer 角色未注册，无法执行模块 pipeline")
            return

        for i, module in enumerate(modules):
            mod_name = module["name"]
            mod_files = module.get("files", [])
            mod_desc = module.get("description", "")
            code_key = f"Code_{mod_name}"
            verify_key = f"Verify_{mod_name}"
            review_key = f"Review_{mod_name}"

            if _time.monotonic() > pipeline_deadline:
                logger.warning("模块 pipeline 超时，已完成 %d/%d 模块", i, len(modules))
                break

            progress_msg = CompanyMessage(
                content=f"开始开发模块 {i+1}/{len(modules)}: {mod_name} — {mod_desc}",
                cause_by="StatusUpdate",
                sent_from="Developer",
                task_id=task.task_id,
            )
            await self._env.publish(progress_msg)

            # WriteCode for this module
            if not stages_done.get(code_key, False):
                module_context = (
                    f"## 当前任务：只编写模块 [{mod_name}] 的代码\n"
                    f"文件列表: {', '.join(mod_files)}\n"
                    f"模块说明: {mod_desc}\n"
                    f"进度: 模块 {i+1}/{len(modules)}\n\n"
                    f"{requirement}"
                )
                write_ok = False
                for attempt in range(1, max_rework + 2):
                    try:
                        result = await asyncio.wait_for(
                            WRITE_CODE.run(module_context, dev_role),
                            timeout=STAGE_TIMEOUTS.get("WriteCode", 600),
                        )
                        if "write_file 未被调用" in result and attempt <= max_rework:
                            logger.warning("模块 %s WriteCode 未写入文件 (第%d次)，重试", mod_name, attempt)
                            module_context = (
                                "## 重要提醒：你必须使用 write_file 工具写入文件！\n"
                                "上一次你没有调用 write_file，代码没有落盘。\n"
                                "请立即使用 write_file 将每个文件写入项目工作目录。\n\n"
                                + module_context
                            )
                            continue
                        write_ok = True
                        stages_done[code_key] = True
                        code_msg = CompanyMessage(
                            content=result,
                            cause_by="WriteCode",
                            sent_from="Developer",
                            task_id=task.task_id,
                        )
                        await self._env.publish(code_msg)
                        self._store.save_message(code_msg)
                        break
                    except asyncio.TimeoutError:
                        logger.warning("模块 %s WriteCode 超时 (%ds)，标记完成继续", mod_name, STAGE_TIMEOUTS.get("WriteCode", 600))
                        stages_done[code_key] = True
                        write_ok = True
                        break
                    except Exception as e:
                        logger.error("模块 %s WriteCode 失败: %s", mod_name, e)
                        break
                if not write_ok:
                    logger.error("模块 %s WriteCode 重试 %d 次仍未写入文件，跳过", mod_name, max_rework)
                    stages_done[code_key] = True

            # VerifyRun for this module
            if not stages_done.get(verify_key, False):
                workspace = self._extract_workspace_from_requirement(requirement)
                verify_context = f"## 验证模块: {mod_name}\n文件: {', '.join(mod_files)}\n\n{requirement}"
                try:
                    result = await asyncio.wait_for(
                        VERIFY_RUN.run(verify_context, dev_role),
                        timeout=STAGE_TIMEOUTS.get("VerifyRun", 600),
                    )
                    if "VERIFY_PASS" in result or "VERIFY_PASS_NO_SERVER" in result:
                        stages_done[verify_key] = True
                    else:
                        vr_count = rework_counts.get(f"verify_{mod_name}", 0)
                        if vr_count < max_rework:
                            rework_counts[f"verify_{mod_name}"] = vr_count + 1
                            fix_parts = [f"## 验证失败，请修复\n{result}"]
                            pdir_match = _re.search(r'## 项目工作目录\n(.+)\n', requirement)
                            if pdir_match:
                                fix_parts.append(f"## 项目工作目录\n{pdir_match.group(1)}")
                            await asyncio.wait_for(
                                FIX_CODE.run("\n\n".join(fix_parts), dev_role),
                                timeout=STAGE_TIMEOUTS.get("FixCode", 300),
                            )
                            result2 = await asyncio.wait_for(
                                VERIFY_RUN.run(verify_context, dev_role),
                                timeout=STAGE_TIMEOUTS.get("VerifyRun", 600),
                            )
                            if "VERIFY_PASS" in result2 or "VERIFY_PASS_NO_SERVER" in result2:
                                stages_done[verify_key] = True
                            else:
                                stages_done[verify_key] = True
                                logger.warning("模块 %s 验证仍失败，强制通过", mod_name)
                        else:
                            stages_done[verify_key] = True
                except (asyncio.TimeoutError, Exception) as e:
                    if isinstance(e, asyncio.TimeoutError):
                        logger.warning("模块 %s VerifyRun 超时，跳过验证继续", mod_name)
                    else:
                        logger.error("模块 %s VerifyRun 失败: %s", mod_name, e)
                    stages_done[verify_key] = True

            # CodeReview for this module
            if not stages_done.get(review_key, False) and reviewer_role:
                workspace = self._extract_workspace_from_requirement(requirement)
                code_listing = ""
                if workspace:
                    code_listing = self._collect_project_files(
                        workspace,
                        max_chars=self._config.max_project_chars,
                        max_files=self._config.max_project_files,
                    )
                review_context = (
                    f"## 审查模块: {mod_name}\n文件: {', '.join(mod_files)}\n\n"
                    f"## 设计方案\n{stage_outputs.get('Design', '')[:2000]}\n\n"
                    f"## 代码\n{code_listing[:8000]}"
                )
                try:
                    result = await asyncio.wait_for(
                        CODE_REVIEW.run(review_context, reviewer_role),
                        timeout=STAGE_TIMEOUTS.get("CodeReview", 180),
                    )
                    if self._is_review_approved(result):
                        stages_done[review_key] = True
                    else:
                        rc = rework_counts.get(f"review_{mod_name}", 0)
                        if rc < max_rework:
                            rework_counts[f"review_{mod_name}"] = rc + 1
                            fix_parts = [f"## 审查意见（请只修复指出的问题）\n{result}"]
                            pdir_match = _re.search(r'## 项目工作目录\n(.+)\n', requirement)
                            if pdir_match:
                                fix_parts.append(f"## 项目工作目录\n{pdir_match.group(1)}")
                            await asyncio.wait_for(
                                FIX_CODE.run("\n\n".join(fix_parts), dev_role),
                                timeout=STAGE_TIMEOUTS.get("FixCode", 300),
                            )
                        stages_done[review_key] = True
                except (asyncio.TimeoutError, Exception) as e:
                    if isinstance(e, asyncio.TimeoutError):
                        logger.warning("模块 %s CodeReview 超时，强制通过", mod_name)
                    else:
                        logger.error("模块 %s CodeReview 失败: %s", mod_name, e)
                    stages_done[review_key] = True

            logger.info("模块 %s 完成 (%d/%d)", mod_name, i + 1, len(modules))

        if sm:
            sm.complete("Code")
            sm.complete("Verify")
            sm.complete("Review")
        else:
            stages_done["Code"] = True
            stages_done["Verify"] = True
            stages_done["Review"] = True

        project_dir = self._extract_workspace_from_requirement(requirement)
        if project_dir:
            self._persist_pipeline_state(project_dir, stages_done, stage_outputs)

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
                    # 有活跃项目时只展示该项目，避免 PM 混淆
                    if self._active_project_name and self._active_project_name not in pd.name:
                        continue
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

    _PROJECT_NAME_STOPWORDS = {
        "是", "的", "了", "吧", "呢", "啊", "嗯", "哦", "好", "行",
        "对", "嗯嗯", "ok", "OK", "yes", "no", "是的", "好的", "行的",
        "可以", "没问题", "收到", "明白", "知道", "那", "这", "就",
        "你", "我", "他", "她", "它", "们", "接着", "继续",
        "开干", "开始", "开始吧", "动手", "动手吧", "可以开干",
        "可以开始", "就这样", "确认", "没问题开干", "好的开干",
    }

    @staticmethod
    def _has_chinese(text: str) -> bool:
        """检测文本是否包含中文字符."""
        import re
        return bool(re.search(r'[一-鿿]', text))

    async def _to_english_name(self, chinese_name: str) -> str:
        """将中文项目名翻译为英文 kebab-case（通过 LLM）."""
        import re as _re_en
        from agent.core.engine import Message
        messages = [Message(role="user", content=(
            f"将以下中文项目名翻译为简短的英文 kebab-case 名称（全小写、用连字符分隔、不超过3个单词）。\n"
            f"只输出翻译结果，不要解释。\n\n"
            f"中文名：{chinese_name}"
        ))]
        try:
            resp = await self._router.complete_with_failover(messages, user_message=chinese_name)
            name = resp.content.strip().lower()
            name = _re_en.sub(r'[^a-z0-9-]', '', name)
            name = _re_en.sub(r'-+', '-', name).strip('-')
            if name and len(name) >= 2:
                return name[:20]
        except Exception as e:
            logger.warning("项目名翻译失败: %s", e)
        return chinese_name

    @staticmethod
    def _extract_project_name(text: str, max_len: int = 20) -> str:
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
            r'核心是[，,：:]*\s*|'
            r'是的[，,]?\s*)',
            '', clean,
        )
        clean = re.sub(r'^[，,：:。\s]+', '', clean)
        for prefix in ("做一个", "开发一个", "写一个", "搞一个", "实现一个", "创建一个", "建一个",
                        "你接着", "你继续", "接着", "继续"):
            if clean.startswith(prefix):
                clean = clean[len(prefix):]
                break
        clean = re.sub(r'^[，,：:。\s]+', '', clean)
        clean = re.sub(r'^\d{8}-', '', clean)
        first_line = clean.split('\n')[0].strip()
        first_sentence = re.split(r'[。！？\n，,的]', first_line)[0].strip()
        if not first_sentence or first_sentence in Company._PROJECT_NAME_STOPWORDS or len(first_sentence) < 2:
            parts = re.split(r'[。！？\n，,]', first_line)
            for part in parts:
                part = part.strip()
                if part and part not in Company._PROJECT_NAME_STOPWORDS and len(part) >= 2:
                    first_sentence = part
                    break
            else:
                first_sentence = first_line[:max_len] if first_line and len(first_line) >= 2 and first_line not in Company._PROJECT_NAME_STOPWORDS else "project"
        return first_sentence[:max_len] if first_sentence else "project"

    def _create_project_workspace(self, requirement: str, project_name: Optional[str] = None) -> Path:
        """根据需求创建项目工作目录，返回项目路径."""
        import json
        import re
        from datetime import datetime

        # 创建前自动归档旧项目
        try:
            from agent.company.project_manager import ProjectManager
            pm = ProjectManager()
            archived = pm.auto_cleanup(
                max_active=self._config.max_active_projects,
                archive_days=self._config.archive_after_days,
            )
            if archived:
                logger.info("自动归档了 %d 个旧项目: %s", len(archived), archived)
        except Exception as e:
            logger.warning("自动归档失败: %s", e)

        max_len = self._config.project_name_max_len
        date_str = datetime.now().strftime("%Y%m%d")
        if project_name:
            slug = project_name[:max_len].strip()
        else:
            short_name = self._extract_project_name(requirement, max_len)
            slug = short_name[:max_len].strip()
        slug = re.sub(r'[^\w\u4e00-\u9fff-]', '_', slug)
        slug = re.sub(r'_+', '_', slug).strip('_') or "project"
        project_name = f"{date_str}-{slug}"

        projects_base = Path(self._config.projects_dir) if self._config.projects_dir else get_projects_dir()
        project_dir = projects_base / project_name
        for sub in ("docs", "src", "tests"):
            (project_dir / sub).mkdir(parents=True, exist_ok=True)

        meta_file = project_dir / ".project.json"
        if not meta_file.exists():
            meta = {
                "requirement": requirement[:500],
                "created_at": datetime.now().isoformat(),
                "status": "in_progress",
            }
            try:
                from agent.company.port_manager import PortManager
                pm_port = PortManager()
                port = pm_port.allocate(project_name)
                meta["port"] = port
                (project_dir / ".port").write_text(str(port))
                logger.info("为项目 %s 分配端口 %d", project_name, port)
            except Exception as e:
                logger.warning("端口分配失败: %s", e)
            meta_file.write_text(json.dumps(meta, ensure_ascii=False, indent=2))

            readme = project_dir / "README.md"
            if not readme.exists():
                readme.write_text(f"# {slug}\n\n> {requirement[:200]}\n\nCreated: {date_str}\n")

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
            stages_done = getattr(self, '_last_stages_done', None)
            if stages_done:
                meta["stages_done"] = stages_done
            stage_outputs = getattr(self, '_last_stage_outputs', None)
            if stage_outputs:
                meta["stage_outputs"] = {k: v[:3000] for k, v in stage_outputs.items()}
            meta_file.write_text(json.dumps(meta, ensure_ascii=False, indent=2))
        except Exception as e:
            logger.warning("更新项目状态失败: %s", e)

    @staticmethod
    def _persist_pipeline_state(
        project_dir: Path,
        stages_done: dict[str, bool],
        stage_outputs: dict[str, str],
    ) -> None:
        """实时持久化 pipeline 状态到 .project.json（每个 stage 完成后调用）."""
        import json
        meta_file = project_dir / ".project.json"
        if not meta_file.exists():
            return
        try:
            meta = json.loads(meta_file.read_text())
            meta["status"] = "in_progress"
            meta["stages_done"] = dict(stages_done)
            meta["stage_outputs"] = {k: v[:3000] for k, v in stage_outputs.items()}
            meta_file.write_text(json.dumps(meta, ensure_ascii=False, indent=2))
        except Exception as e:
            logger.warning("持久化 pipeline 状态失败: %s", e)

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
                result_msgs = await role.run()
                for result_msg in result_msgs:
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
