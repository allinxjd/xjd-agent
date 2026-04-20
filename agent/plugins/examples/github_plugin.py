"""示例插件: GitHub 集成.

支持:
- 查看/创建/关闭 Issue
- 查看 PR 信息
- 搜索仓库
- 查看文件内容
"""

from __future__ import annotations

import logging
from typing import Any

from agent.plugins.manager import BasePlugin

logger = logging.getLogger(__name__)

class GitHubPlugin(BasePlugin):
    """GitHub 集成插件.

    配置:
        token: GitHub Personal Access Token
        default_repo: 默认仓库 (owner/repo)
    """

    async def on_enable(self) -> None:
        token = self.config.get("token", "")
        if not token:
            logger.warning("GitHubPlugin: no token configured")

    def get_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "github_search_repos",
                "description": "搜索 GitHub 仓库",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "搜索关键词"},
                        "language": {"type": "string", "description": "编程语言过滤"},
                        "limit": {"type": "integer", "default": 5},
                    },
                    "required": ["query"],
                },
                "handler": self._search_repos,
            },
            {
                "name": "github_list_issues",
                "description": "列出仓库的 Issues",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "repo": {"type": "string", "description": "仓库 (owner/repo)"},
                        "state": {"type": "string", "enum": ["open", "closed", "all"], "default": "open"},
                        "limit": {"type": "integer", "default": 10},
                    },
                    "required": ["repo"],
                },
                "handler": self._list_issues,
            },
            {
                "name": "github_create_issue",
                "description": "创建 GitHub Issue",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "repo": {"type": "string", "description": "仓库 (owner/repo)"},
                        "title": {"type": "string", "description": "标题"},
                        "body": {"type": "string", "description": "内容"},
                        "labels": {"type": "array", "items": {"type": "string"}, "description": "标签"},
                    },
                    "required": ["repo", "title"],
                },
                "handler": self._create_issue,
                "requires_approval": True,
            },
            {
                "name": "github_get_file",
                "description": "获取仓库中的文件内容",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "repo": {"type": "string", "description": "仓库 (owner/repo)"},
                        "path": {"type": "string", "description": "文件路径"},
                        "branch": {"type": "string", "default": "main"},
                    },
                    "required": ["repo", "path"],
                },
                "handler": self._get_file,
            },
        ]

    def _headers(self) -> dict:
        token = self.config.get("token", "")
        h = {"Accept": "application/vnd.github.v3+json"}
        if token:
            h["Authorization"] = f"token {token}"
        return h

    async def _search_repos(self, query: str, language: str = "", limit: int = 5) -> str:
        try:
            import httpx

            q = query
            if language:
                q += f" language:{language}"

            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    "https://api.github.com/search/repositories",
                    params={"q": q, "per_page": limit, "sort": "stars"},
                    headers=self._headers(),
                )
                resp.raise_for_status()
                data = resp.json()

            items = data.get("items", [])
            if not items:
                return "未找到相关仓库。"

            lines = [f"找到 {data.get('total_count', 0)} 个仓库 (显示前 {len(items)} 个):"]
            for repo in items:
                stars = repo.get("stargazers_count", 0)
                desc = (repo.get("description") or "")[:80]
                lines.append(
                    f"  ⭐ {stars:,}  {repo['full_name']}\n"
                    f"     {desc}"
                )

            return "\n".join(lines)

        except Exception as e:
            return f"搜索失败: {e}"

    async def _list_issues(self, repo: str, state: str = "open", limit: int = 10) -> str:
        try:
            import httpx

            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    f"https://api.github.com/repos/{repo}/issues",
                    params={"state": state, "per_page": limit},
                    headers=self._headers(),
                )
                resp.raise_for_status()
                issues = resp.json()

            if not issues:
                return f"{repo} 没有 {state} 的 Issues。"

            lines = [f"📋 {repo} Issues ({state}):"]
            for issue in issues:
                labels = ", ".join(l["name"] for l in issue.get("labels", []))
                lines.append(
                    f"  #{issue['number']} [{issue['state']}] {issue['title']}"
                    + (f" ({labels})" if labels else "")
                )

            return "\n".join(lines)

        except Exception as e:
            return f"获取 Issues 失败: {e}"

    async def _create_issue(
        self, repo: str, title: str, body: str = "", labels: list[str] | None = None
    ) -> str:
        try:
            import httpx

            payload: dict[str, Any] = {"title": title}
            if body:
                payload["body"] = body
            if labels:
                payload["labels"] = labels

            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    f"https://api.github.com/repos/{repo}/issues",
                    json=payload,
                    headers=self._headers(),
                )
                resp.raise_for_status()
                issue = resp.json()

            return f"✅ Issue 已创建: #{issue['number']} {issue['html_url']}"

        except Exception as e:
            return f"创建 Issue 失败: {e}"

    async def _get_file(self, repo: str, path: str, branch: str = "main") -> str:
        try:
            import httpx
            import base64

            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    f"https://api.github.com/repos/{repo}/contents/{path}",
                    params={"ref": branch},
                    headers=self._headers(),
                )
                resp.raise_for_status()
                data = resp.json()

            if data.get("type") != "file":
                return f"{path} 不是文件 (类型: {data.get('type')})"

            content = base64.b64decode(data.get("content", "")).decode("utf-8", errors="ignore")

            # 截断
            if len(content) > 5000:
                content = content[:5000] + f"\n\n... (截断, 总 {data.get('size', 0)} bytes)"

            return f"📄 {repo}/{path} ({data.get('size', 0)} bytes):\n\n{content}"

        except Exception as e:
            return f"获取文件失败: {e}"
