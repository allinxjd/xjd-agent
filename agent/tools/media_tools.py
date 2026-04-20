"""Media-related tools extracted from extended.py.

Includes: image_generate, vision_analyze, text_to_speech, speech_to_text, screenshot.
"""

import asyncio
import subprocess
import os
import tempfile
import base64
import logging
import platform
from pathlib import Path

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
#  Handler functions
# ═══════════════════════════════════════════════════════════════════

async def _image_generate(prompt: str, size: str = "1024x1024", **kwargs) -> str:
    """AI 生成图片."""
    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI()
        resp = await client.images.generate(prompt=prompt, size=size, n=1)
        url = resp.data[0].url
        return f"图片已生成: {url}"
    except Exception as e:
        return f"图片生成失败: {e}"


async def _vision_analyze(image_path: str, prompt: str = "描述这张图片", **kwargs) -> str:
    """分析图片内容 (Vision)."""
    import base64
    p = Path(image_path).expanduser().resolve()
    if not p.exists():
        return f"图片不存在: {image_path}"
    try:
        with open(p, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        suffix = p.suffix.lower().lstrip(".")
        mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
                "gif": "image/gif", "webp": "image/webp"}.get(suffix, "image/png")

        # 尝试 OpenAI vision API
        try:
            from openai import AsyncOpenAI
            client = AsyncOpenAI()
            resp = await client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                ]}],
                max_tokens=1000,
            )
            return resp.choices[0].message.content or "(无描述)"
        except Exception as e:
            return f"Vision 分析失败 (需要支持 vision 的模型): {e}"
    except Exception as e:
        return f"读取图片失败: {e}"


async def _text_to_speech(text: str, voice: str = "zh-CN-XiaoxiaoNeural", output: str = "", **kwargs) -> str:
    """文本转语音."""
    try:
        import edge_tts
        if not output:
            output = tempfile.mktemp(suffix=".mp3", prefix="tts_")
        communicate = edge_tts.Communicate(text, voice)
        await communicate.save(output)
        return f"语音已生成: {output}"
    except ImportError:
        return "错误: edge-tts 未安装。请运行: pip install edge-tts"
    except Exception as e:
        return f"TTS 失败: {e}"


async def _speech_to_text(audio_path: str, language: str = "", **kwargs) -> str:
    """语音转文字."""
    try:
        from faster_whisper import WhisperModel
        model = WhisperModel("base", compute_type="int8")
        segments, info = model.transcribe(audio_path, language=language or None)
        text = " ".join(s.text for s in segments)
        return text or "(未识别到语音)"
    except ImportError:
        return "错误: faster-whisper 未安装。请运行: pip install faster-whisper"
    except Exception as e:
        return f"STT 失败: {e}"


async def _screenshot(region: str = "", **kwargs) -> str:
    """截取屏幕截图."""
    path = tempfile.mktemp(suffix=".png", prefix="screenshot_")
    try:
        if platform.system() == "Darwin":
            cmd = ["screencapture", "-x"]
            if region:
                parts = region.split(",")
                if len(parts) == 4:
                    cmd.extend(["-R", ",".join(parts)])
            cmd.append(path)
            subprocess.run(cmd, timeout=10)
        else:
            subprocess.run(["import", "-window", "root", path], timeout=10)
        return f"截图已保存: {path}"
    except Exception as e:
        return f"截图失败: {e}"


# ═══════════════════════════════════════════════════════════════════
#  Registration
# ═══════════════════════════════════════════════════════════════════

def register_media_tools(registry):
    """Register all media-related tools."""

    registry.register(
        name="image_generate",
        description="通过 AI 生成图片 (需要 OPENAI_API_KEY)。",
        parameters={
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "图片描述"},
                "size": {"type": "string", "description": "尺寸", "default": "1024x1024"},
            },
            "required": ["prompt"],
        },
        handler=_image_generate,
        category="web",
        optional_deps=["openai"],
    )

    registry.register(
        name="vision_analyze",
        description="分析图片内容 (需要支持 vision 的模型，如 GPT-4o)。",
        parameters={
            "type": "object",
            "properties": {
                "image_path": {"type": "string", "description": "图片文件路径"},
                "prompt": {"type": "string", "description": "分析提示 (如: 描述这张图片)", "default": "描述这张图片"},
            },
            "required": ["image_path"],
        },
        handler=_vision_analyze,
        category="media",
    )

    registry.register(
        name="text_to_speech",
        description="文本转语音，生成 MP3 文件 (使用 edge-tts)。",
        parameters={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "要转换的文本"},
                "voice": {"type": "string", "description": "语音 (默认 zh-CN-XiaoxiaoNeural)", "default": "zh-CN-XiaoxiaoNeural"},
                "output": {"type": "string", "description": "输出文件路径"},
            },
            "required": ["text"],
        },
        handler=_text_to_speech,
        category="media",
        optional_deps=["edge_tts"],
    )

    registry.register(
        name="speech_to_text",
        description="语音转文字 (需要 faster-whisper)。",
        parameters={
            "type": "object",
            "properties": {
                "audio_path": {"type": "string", "description": "音频文件路径"},
                "language": {"type": "string", "description": "语言代码 (如 zh, en)", "default": ""},
            },
            "required": ["audio_path"],
        },
        handler=_speech_to_text,
        category="media",
        optional_deps=["faster_whisper"],
    )

    registry.register(
        name="screenshot",
        description="截取屏幕截图。",
        parameters={
            "type": "object",
            "properties": {
                "region": {"type": "string", "description": "截图区域 (x,y,w,h) 或留空截全屏"},
            },
            "required": [],
        },
        handler=_screenshot,
        category="system",
    )
