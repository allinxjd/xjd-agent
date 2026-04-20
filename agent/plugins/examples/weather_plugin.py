"""示例插件: 天气查询.

plugin.yaml:
  name: weather
  version: 1.0.0
  description: 天气查询插件 — 使用 OpenWeatherMap API
  author: XJD Team
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from agent.plugins.manager import BasePlugin

logger = logging.getLogger(__name__)

class WeatherPlugin(BasePlugin):
    """天气查询插件.

    配置:
        api_key: OpenWeatherMap API Key
        default_city: 默认城市 (可选)
    """

    async def on_install(self) -> None:
        logger.info("WeatherPlugin installed")

    async def on_enable(self) -> None:
        logger.info("WeatherPlugin enabled")

    async def on_disable(self) -> None:
        logger.info("WeatherPlugin disabled")

    def get_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "get_weather",
                "description": "查询指定城市的当前天气",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "city": {
                            "type": "string",
                            "description": "城市名称 (中文或英文)",
                        },
                        "units": {
                            "type": "string",
                            "enum": ["metric", "imperial"],
                            "description": "温度单位: metric(摄氏) / imperial(华氏)",
                            "default": "metric",
                        },
                    },
                    "required": ["city"],
                },
                "handler": self._get_weather,
            },
            {
                "name": "get_forecast",
                "description": "查询未来3天天气预报",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "city": {
                            "type": "string",
                            "description": "城市名称",
                        },
                    },
                    "required": ["city"],
                },
                "handler": self._get_forecast,
            },
        ]

    async def _get_weather(self, city: str, units: str = "metric") -> str:
        """查询当前天气."""
        api_key = self.config.get("api_key", "")
        if not api_key:
            return "错误: 未配置 OpenWeatherMap API Key"

        try:
            import httpx

            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    "https://api.openweathermap.org/data/2.5/weather",
                    params={
                        "q": city,
                        "appid": api_key,
                        "units": units,
                        "lang": "zh_cn",
                    },
                )
                resp.raise_for_status()
                data = resp.json()

            temp = data["main"]["temp"]
            feels_like = data["main"]["feels_like"]
            humidity = data["main"]["humidity"]
            desc = data["weather"][0]["description"]
            wind = data["wind"]["speed"]
            unit = "°C" if units == "metric" else "°F"

            return (
                f"🌍 {city} 当前天气:\n"
                f"  天气: {desc}\n"
                f"  温度: {temp}{unit} (体感 {feels_like}{unit})\n"
                f"  湿度: {humidity}%\n"
                f"  风速: {wind} m/s"
            )

        except Exception as e:
            return f"查询天气失败: {e}"

    async def _get_forecast(self, city: str) -> str:
        """查询天气预报."""
        api_key = self.config.get("api_key", "")
        if not api_key:
            return "错误: 未配置 OpenWeatherMap API Key"

        try:
            import httpx

            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    "https://api.openweathermap.org/data/2.5/forecast",
                    params={
                        "q": city,
                        "appid": api_key,
                        "units": "metric",
                        "lang": "zh_cn",
                        "cnt": 24,  # 每3小时一条，24条=3天
                    },
                )
                resp.raise_for_status()
                data = resp.json()

            lines = [f"📅 {city} 未来3天预报:"]
            seen_dates = set()
            for item in data.get("list", []):
                date = item["dt_txt"][:10]
                if date in seen_dates:
                    continue
                seen_dates.add(date)

                temp = item["main"]["temp"]
                desc = item["weather"][0]["description"]
                lines.append(f"  {date}: {desc}, {temp}°C")

            return "\n".join(lines)

        except Exception as e:
            return f"查询天气预报失败: {e}"
