"""Home Assistant 插件 — 智能家居控制.

通过 Home Assistant REST API 控制智能设备:
- 查询设备状态
- 控制开关/灯光/空调等
- 场景激活
- 自动化触发

用法:
    plugin = HomeAssistantPlugin(
        ha_url="http://homeassistant.local:8123",
        token="your_long_lived_access_token",
    )
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

class HomeAssistantPlugin:
    """Home Assistant 智能家居插件."""

    name = "homeassistant"
    version = "0.1.0"
    description = "Home Assistant 智能家居控制"

    def __init__(self, ha_url: str = "", token: str = "", **kwargs) -> None:
        self._url = ha_url.rstrip("/")
        self._token = token
        self._enabled = False

    async def on_enable(self) -> None:
        self._enabled = True
        logger.info("Home Assistant 插件已启用: %s", self._url)

    async def on_disable(self) -> None:
        self._enabled = False

    def get_tools(self) -> list[dict]:
        return [
            {
                "name": "ha_get_states",
                "description": "获取 Home Assistant 所有设备状态",
                "parameters": {"type": "object", "properties": {}, "required": []},
                "handler": self._get_states,
            },
            {
                "name": "ha_get_state",
                "description": "获取指定设备状态",
                "parameters": {
                    "type": "object",
                    "properties": {"entity_id": {"type": "string", "description": "设备 ID, 如 light.living_room"}},
                    "required": ["entity_id"],
                },
                "handler": self._get_state,
            },
            {
                "name": "ha_call_service",
                "description": "调用 Home Assistant 服务 (控制设备)",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "domain": {"type": "string", "description": "服务域, 如 light, switch, climate"},
                        "service": {"type": "string", "description": "服务名, 如 turn_on, turn_off, set_temperature"},
                        "entity_id": {"type": "string", "description": "设备 ID"},
                        "data": {"type": "object", "description": "附加参数, 如 {brightness: 128, color_temp: 300}"},
                    },
                    "required": ["domain", "service", "entity_id"],
                },
                "handler": self._call_service,
            },
            {
                "name": "ha_activate_scene",
                "description": "激活 Home Assistant 场景",
                "parameters": {
                    "type": "object",
                    "properties": {"scene_id": {"type": "string", "description": "场景 ID, 如 scene.movie_time"}},
                    "required": ["scene_id"],
                },
                "handler": self._activate_scene,
            },
            {
                "name": "ha_trigger_automation",
                "description": "触发 Home Assistant 自动化",
                "parameters": {
                    "type": "object",
                    "properties": {"automation_id": {"type": "string", "description": "自动化 ID"}},
                    "required": ["automation_id"],
                },
                "handler": self._trigger_automation,
            },
        ]

    async def _request(self, method: str, path: str, json_data: Any = None) -> Any:
        """发送 HTTP 请求到 Home Assistant."""
        try:
            import aiohttp
        except ImportError:
            return {"error": "aiohttp 未安装，请运行: pip install aiohttp"}

        url = f"{self._url}/api{path}"
        headers = {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"}

        async with aiohttp.ClientSession() as session:
            async with session.request(method, url, headers=headers, json=json_data, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    return await resp.json()
                else:
                    text = await resp.text()
                    return {"error": f"HTTP {resp.status}: {text[:200]}"}

    async def _get_states(self, **kwargs) -> str:
        """获取所有设备状态."""
        result = await self._request("GET", "/states")
        if isinstance(result, dict) and "error" in result:
            return result["error"]
        # 简化输出
        devices = []
        for entity in result[:50]:  # 限制数量
            devices.append(f"{entity['entity_id']}: {entity['state']}")
        return f"共 {len(result)} 个设备:\n" + "\n".join(devices)

    async def _get_state(self, entity_id: str = "", **kwargs) -> str:
        """获取指定设备状态."""
        result = await self._request("GET", f"/states/{entity_id}")
        if isinstance(result, dict) and "error" in result:
            return result["error"]
        attrs = result.get("attributes", {})
        return f"{entity_id}: {result['state']}\n属性: {attrs}"

    async def _call_service(self, domain: str = "", service: str = "", entity_id: str = "", data: Optional[dict] = None, **kwargs) -> str:
        """调用服务."""
        payload = {"entity_id": entity_id}
        if data:
            payload.update(data)
        result = await self._request("POST", f"/services/{domain}/{service}", payload)
        if isinstance(result, dict) and "error" in result:
            return result["error"]
        return f"已执行: {domain}.{service} → {entity_id}"

    async def _activate_scene(self, scene_id: str = "", **kwargs) -> str:
        """激活场景."""
        result = await self._request("POST", "/services/scene/turn_on", {"entity_id": scene_id})
        if isinstance(result, dict) and "error" in result:
            return result["error"]
        return f"场景已激活: {scene_id}"

    async def _trigger_automation(self, automation_id: str = "", **kwargs) -> str:
        """触发自动化."""
        result = await self._request("POST", "/services/automation/trigger", {"entity_id": automation_id})
        if isinstance(result, dict) and "error" in result:
            return result["error"]
        return f"自动化已触发: {automation_id}"
