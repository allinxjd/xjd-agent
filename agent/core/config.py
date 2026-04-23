"""配置系统 — YAML 配置 + 环境变量 + 默认值.

配置优先级: CLI 参数 > 环境变量 > config.yaml > 默认值
配置文件位置: ~/.xjd-agent/config.yaml
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

logger = logging.getLogger(__name__)

# 默认目录
XJD_HOME = Path(os.environ.get("XJD_AGENT_HOME", Path.home() / ".xjd-agent"))

def get_home() -> Path:
    """获取 xjd-agent 根目录."""
    XJD_HOME.mkdir(parents=True, exist_ok=True)
    return XJD_HOME

def get_config_path() -> Path:
    return get_home() / "config.yaml"

def get_skills_dir() -> Path:
    d = get_home() / "skills"
    d.mkdir(exist_ok=True)
    return d

def get_memory_dir() -> Path:
    d = get_home() / "memory"
    d.mkdir(exist_ok=True)
    return d

def get_workspace_dir() -> Path:
    d = get_home() / "workspace"
    d.mkdir(exist_ok=True)
    return d

def get_sessions_dir() -> Path:
    d = get_home() / "sessions"
    d.mkdir(exist_ok=True)
    return d

def get_marketplace_dir() -> Path:
    d = get_home() / "marketplace"
    d.mkdir(exist_ok=True)
    return d

def get_logs_dir() -> Path:
    d = get_home() / "logs"
    d.mkdir(exist_ok=True)
    return d

@dataclass
class ProviderConfig:
    """单个 Provider 配置."""

    provider: str = ""  # "openai" | "anthropic" | "deepseek" | ...
    model: str = ""
    api_key: str = ""
    api_keys: list[str] = field(default_factory=list)  # 多 Key 轮换
    base_url: str = ""
    organization: str = ""

@dataclass
class ModelConfig:
    """模型相关配置."""

    primary: ProviderConfig = field(default_factory=ProviderConfig)
    cheap: Optional[ProviderConfig] = None
    failover: list[ProviderConfig] = field(default_factory=list)
    temperature: float = 0.7
    max_tokens: int = 4096

@dataclass
class GatewayConfig:
    """Gateway 网关配置."""

    host: str = "127.0.0.1"
    port: int = 18789
    dm_policy: str = "pairing"  # "pairing" | "open"

@dataclass
class SecurityConfig:
    """安全配置."""

    sandbox_mode: str = "off"  # "off" | "non-main" | "all"
    sandbox_type: str = "none"  # "none" | "subprocess" | "docker"
    sandbox_docker_image: str = "python:3.12-slim"
    sandbox_memory_limit: str = "512m"
    sandbox_timeout: int = 30
    sandbox_network: bool = False
    require_approval: list[str] = field(default_factory=lambda: ["rm", "sudo", "reboot"])
    elevated_bash: bool = False

@dataclass
class TerminalConfig:
    """终端后端配置."""

    default_backend: str = "local"
    ssh_host: str = ""
    ssh_port: int = 22
    ssh_username: str = "root"
    ssh_key_path: str = ""
    docker_container: str = ""
    tmux_session: str = "xjd-agent"

@dataclass
class VoiceConfig:
    """语音管线配置."""

    enabled: bool = False
    # STT
    stt_provider: str = "whisper_local"  # "whisper_local" | "whisper_api"
    stt_model: str = "base"             # whisper 模型: tiny/base/small/medium/large
    stt_language: str = ""              # 空 = 自动检测
    stt_api_key: str = ""               # Whisper API 用
    stt_base_url: str = ""              # 自定义 Whisper API endpoint
    # TTS
    tts_provider: str = "edge_tts"      # "edge_tts" | "elevenlabs" | "openai_tts"
    tts_voice: str = "zh-CN-XiaoxiaoNeural"  # Edge TTS 默认中文女声
    tts_speed: float = 1.0
    tts_api_key: str = ""               # ElevenLabs / OpenAI TTS 用
    tts_base_url: str = ""              # 自定义 TTS endpoint
    tts_output_format: str = "mp3"      # "mp3" | "wav" | "ogg"

@dataclass
class Config:
    """全局配置."""

    model: ModelConfig = field(default_factory=ModelConfig)
    gateway: GatewayConfig = field(default_factory=GatewayConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)
    voice: VoiceConfig = field(default_factory=VoiceConfig)
    language: str = "zh-CN"
    log_level: str = "INFO"
    proxy: str = ""
    hub_url: str = "https://ai.allinxjd.com"

    # Channel 配置 (动态加载)
    channels: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Optional[Path] = None) -> Config:
        """从 YAML 文件加载配置."""
        config_path = path or get_config_path()

        if not config_path.exists():
            logger.info("No config file found at %s, using defaults", config_path)
            return cls()

        try:
            with open(config_path) as f:
                data = yaml.safe_load(f) or {}

            config = cls()

            # 解析 model 配置
            if "model" in data:
                m = data["model"]
                if "primary" in m:
                    p = m["primary"]
                    config.model.primary = ProviderConfig(
                        provider=p.get("provider", ""),
                        model=p.get("model", ""),
                        api_key=p.get("api_key", ""),
                        api_keys=p.get("api_keys", []),
                        base_url=p.get("base_url", ""),
                    )
                if "cheap" in m:
                    c = m["cheap"]
                    config.model.cheap = ProviderConfig(
                        provider=c.get("provider", ""),
                        model=c.get("model", ""),
                        api_key=c.get("api_key", ""),
                        base_url=c.get("base_url", ""),
                    )
                config.model.temperature = m.get("temperature", 0.7)
                config.model.max_tokens = m.get("max_tokens", 4096)
                if "failover" in m:
                    for fc in m["failover"]:
                        config.model.failover.append(ProviderConfig(
                            provider=fc.get("provider", ""),
                            model=fc.get("model", ""),
                            api_key=fc.get("api_key", ""),
                            api_keys=fc.get("api_keys", []),
                            base_url=fc.get("base_url", ""),
                        ))

            # 解析 gateway 配置
            if "gateway" in data:
                g = data["gateway"]
                config.gateway.host = g.get("host", "127.0.0.1")
                config.gateway.port = g.get("port", 18789)
                config.gateway.dm_policy = g.get("dm_policy", "pairing")

            # 解析 security 配置
            if "security" in data:
                s = data["security"]
                config.security.sandbox_mode = s.get("sandbox_mode", "off")
                config.security.sandbox_type = s.get("sandbox_type", "none")
                config.security.sandbox_docker_image = s.get("sandbox_docker_image", "python:3.12-slim")
                config.security.sandbox_memory_limit = s.get("sandbox_memory_limit", "512m")
                config.security.sandbox_timeout = s.get("sandbox_timeout", 30)
                config.security.sandbox_network = s.get("sandbox_network", False)
                config.security.require_approval = s.get("require_approval", ["rm", "sudo", "reboot"])
                config.security.elevated_bash = s.get("elevated_bash", False)

            # 解析 voice 配置
            if "voice" in data:
                v = data["voice"]
                config.voice.enabled = v.get("enabled", False)
                config.voice.stt_provider = v.get("stt_provider", "whisper_local")
                config.voice.stt_model = v.get("stt_model", "base")
                config.voice.stt_language = v.get("stt_language", "")
                config.voice.stt_api_key = v.get("stt_api_key", "")
                config.voice.stt_base_url = v.get("stt_base_url", "")
                config.voice.tts_provider = v.get("tts_provider", "edge_tts")
                config.voice.tts_voice = v.get("tts_voice", "zh-CN-XiaoxiaoNeural")
                config.voice.tts_speed = v.get("tts_speed", 1.0)
                config.voice.tts_api_key = v.get("tts_api_key", "")
                config.voice.tts_base_url = v.get("tts_base_url", "")
                config.voice.tts_output_format = v.get("tts_output_format", "mp3")

            # 其他
            config.language = data.get("language", "zh-CN")
            config.log_level = data.get("log_level", "INFO")
            config.proxy = data.get("proxy", "")
            config.hub_url = data.get("hub_url", "https://ai.allinxjd.com")
            config.channels = data.get("channels", {})

            return config

        except Exception as e:
            logger.error("Failed to load config: %s", e)
            return cls()

    def save(self, path: Optional[Path] = None) -> None:
        """保存配置到 YAML."""
        config_path = path or get_config_path()
        config_path.parent.mkdir(parents=True, exist_ok=True)

        primary_data: dict[str, Any] = {
                    "provider": self.model.primary.provider,
                    "model": self.model.primary.model,
                }
        if self.model.primary.api_key:
            primary_data["api_key"] = self.model.primary.api_key
        if self.model.primary.api_keys:
            primary_data["api_keys"] = self.model.primary.api_keys
        if self.model.primary.base_url:
            primary_data["base_url"] = self.model.primary.base_url

        data: dict[str, Any] = {
            "model": {
                "primary": primary_data,
                "temperature": self.model.temperature,
            },
            "gateway": {
                "host": self.gateway.host,
                "port": self.gateway.port,
                "dm_policy": self.gateway.dm_policy,
            },
            "security": {
                "sandbox_mode": self.security.sandbox_mode,
                "sandbox_type": self.security.sandbox_type,
                "sandbox_docker_image": self.security.sandbox_docker_image,
                "sandbox_memory_limit": self.security.sandbox_memory_limit,
                "sandbox_timeout": self.security.sandbox_timeout,
                "sandbox_network": self.security.sandbox_network,
                "require_approval": self.security.require_approval,
                "elevated_bash": self.security.elevated_bash,
            },
            "voice": {
                "enabled": self.voice.enabled,
                "stt_provider": self.voice.stt_provider,
                "stt_model": self.voice.stt_model,
                "stt_language": self.voice.stt_language,
                "stt_api_key": self.voice.stt_api_key,
                "stt_base_url": self.voice.stt_base_url,
                "tts_provider": self.voice.tts_provider,
                "tts_voice": self.voice.tts_voice,
                "tts_speed": self.voice.tts_speed,
                "tts_api_key": self.voice.tts_api_key,
                "tts_base_url": self.voice.tts_base_url,
                "tts_output_format": self.voice.tts_output_format,
            },
            "language": self.language,
            "log_level": self.log_level,
        }

        if self.model.cheap:
            cheap_data: dict[str, Any] = {
                "provider": self.model.cheap.provider,
                "model": self.model.cheap.model,
            }
            if self.model.cheap.api_key:
                cheap_data["api_key"] = self.model.cheap.api_key
            if self.model.cheap.base_url:
                cheap_data["base_url"] = self.model.cheap.base_url
            data["model"]["cheap"] = cheap_data

        if self.model.failover:
            failover_list = []
            for fc in self.model.failover:
                fd: dict[str, Any] = {"provider": fc.provider, "model": fc.model}
                if fc.api_key:
                    fd["api_key"] = fc.api_key
                if fc.base_url:
                    fd["base_url"] = fc.base_url
                failover_list.append(fd)
            data["model"]["failover"] = failover_list

        if self.model.max_tokens != 4096:
            data["model"]["max_tokens"] = self.model.max_tokens

        if self.channels:
            data["channels"] = self.channels

        # 不序列化敏感 key 到 YAML (api_key 通过环境变量管理)
        # voice 的 api_key 也不写入
        for key in ("stt_api_key", "tts_api_key"):
            data["voice"].pop(key, None)

        with open(config_path, "w") as f:
            yaml.dump(data, f, default_flow_style=False, allow_unicode=True)

        logger.info("Config saved to %s", config_path)

    def apply_env_overrides(self) -> None:
        """从环境变量覆盖配置."""
        env_map = {
            "XJD_PRIMARY_PROVIDER": lambda v: setattr(self.model.primary, "provider", v),
            "XJD_PRIMARY_MODEL": lambda v: setattr(self.model.primary, "model", v),
            "XJD_PRIMARY_API_KEY": lambda v: setattr(self.model.primary, "api_key", v),
            "OPENAI_API_KEY": lambda v: (
                setattr(self.model.primary, "api_key", v)
                if not self.model.primary.api_key
                else None
            ),
            "ANTHROPIC_API_KEY": lambda v: (
                setattr(self.model.primary, "api_key", v)
                if self.model.primary.provider == "anthropic" and not self.model.primary.api_key
                else None
            ),
            "DEEPSEEK_API_KEY": lambda v: (
                setattr(self.model.primary, "api_key", v)
                if self.model.primary.provider == "deepseek" and not self.model.primary.api_key
                else None
            ),
            "XJD_GATEWAY_PORT": lambda v: setattr(self.gateway, "port", int(v)),
            "XJD_LOG_LEVEL": lambda v: setattr(self, "log_level", v),
            "XJD_VOICE_ENABLED": lambda v: setattr(self.voice, "enabled", v.lower() in ("1", "true", "yes")),
            "XJD_VOICE_STT_PROVIDER": lambda v: setattr(self.voice, "stt_provider", v),
            "XJD_VOICE_TTS_PROVIDER": lambda v: setattr(self.voice, "tts_provider", v),
            "XJD_VOICE_TTS_VOICE": lambda v: setattr(self.voice, "tts_voice", v),
        }

        for env_key, setter in env_map.items():
            value = os.environ.get(env_key)
            if value:
                setter(value)
