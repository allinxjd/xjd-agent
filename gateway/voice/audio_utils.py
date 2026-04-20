"""音频格式转换工具 — 基于 ffmpeg subprocess.

飞书语音用 opus/ogg，Whisper 要 wav，Edge TTS 输出 mp3。
此模块统一处理格式转换。

依赖: 系统安装 ffmpeg
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Magic bytes 检测
_MAGIC_MAP = {
    b"RIFF": "wav",
    b"\xff\xfb": "mp3",
    b"\xff\xf3": "mp3",
    b"\xff\xf2": "mp3",
    b"ID3": "mp3",
    b"OggS": "ogg",
    b"fLaC": "flac",
}


def detect_audio_format(data: bytes) -> str:
    """通过 magic bytes 检测音频格式."""
    if not data:
        return "unknown"
    for magic, fmt in _MAGIC_MAP.items():
        if data[:len(magic)] == magic:
            return fmt
    # opus 通常封装在 ogg 里
    if b"OpusHead" in data[:64]:
        return "opus"
    return "unknown"


async def convert_audio(
    data: bytes,
    from_format: str = "auto",
    to_format: str = "wav",
    sample_rate: int = 16000,
    channels: int = 1,
) -> bytes:
    """转换音频格式 (通过 ffmpeg).

    Args:
        data: 原始音频 bytes
        from_format: 输入格式 ("auto" 自动检测, "opus", "ogg", "mp3", "wav")
        to_format: 输出格式 ("wav", "mp3", "ogg", "opus")
        sample_rate: 采样率 (STT 通常用 16000)
        channels: 声道数 (STT 通常用 1)
    """
    if from_format == "auto":
        from_format = detect_audio_format(data)
        if from_format == "unknown":
            from_format = "ogg"  # 飞书默认

    with tempfile.NamedTemporaryFile(suffix=f".{from_format}", delete=False) as inf:
        inf.write(data)
        in_path = inf.name

    out_path = in_path.rsplit(".", 1)[0] + f".{to_format}"

    try:
        cmd = [
            "ffmpeg", "-y", "-i", in_path,
            "-ar", str(sample_rate),
            "-ac", str(channels),
        ]
        if to_format == "wav":
            cmd += ["-f", "wav", "-acodec", "pcm_s16le"]
        elif to_format == "mp3":
            cmd += ["-f", "mp3", "-acodec", "libmp3lame", "-q:a", "2"]
        elif to_format in ("ogg", "opus"):
            cmd += ["-f", "ogg", "-acodec", "libopus"]

        cmd.append(out_path)

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)

        if proc.returncode != 0:
            logger.error("ffmpeg 转换失败: %s", stderr.decode(errors="replace"))
            raise RuntimeError(f"ffmpeg failed: {stderr.decode(errors='replace')[:200]}")

        return Path(out_path).read_bytes()

    finally:
        Path(in_path).unlink(missing_ok=True)
        Path(out_path).unlink(missing_ok=True)


async def check_ffmpeg() -> bool:
    """检查 ffmpeg 是否可用."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        return proc.returncode == 0
    except FileNotFoundError:
        return False
