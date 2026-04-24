"""渠道文件管理器 — 统一存储渠道收到的媒体文件到 workspace/inbox.

渠道适配器下载的 media_data 落盘到 workspace/inbox/，
路径注入到消息文本，工具直接读取本地路径。
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from agent.core.config import get_inbox_dir

logger = logging.getLogger(__name__)

_INBOX_DIR = get_inbox_dir()

_MAGIC_BYTES = {
    b"\xff\xd8\xff": "jpg",
    b"\x89PNG": "png",
    b"GIF8": "gif",
    b"RIFF": "webp",
    b"%PDF": "pdf",
    b"PK\x03\x04": "zip",
}


def _detect_ext(data: bytes) -> str:
    for magic, ext in _MAGIC_BYTES.items():
        if data[:len(magic)] == magic:
            if ext == "webp" and b"WEBP" not in data[:12]:
                continue
            return ext
    return ""


class ChannelFileManager:
    _instance: Optional[ChannelFileManager] = None

    def __init__(self) -> None:
        _INBOX_DIR.mkdir(parents=True, exist_ok=True)

    @classmethod
    def get(cls) -> ChannelFileManager:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def save_incoming(self, media_data: bytes, message_type: str = "", platform: str = "") -> str:
        ext = _detect_ext(media_data)
        if not ext:
            type_map = {"image": "jpg", "video": "mp4", "voice": "wav", "file": "bin"}
            ext = type_map.get(message_type.lower(), "bin")

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        h = hashlib.md5(media_data[:4096]).hexdigest()[:6]
        filename = f"{ts}_{h}.{ext}"
        path = _INBOX_DIR / filename
        path.write_bytes(media_data)
        logger.info("渠道文件已保存: %s (%d bytes, platform=%s)", path, len(media_data), platform)
        return str(path)

    @property
    def inbox_dir(self) -> Path:
        return _INBOX_DIR


def get_channel_file_manager() -> ChannelFileManager:
    return ChannelFileManager.get()
