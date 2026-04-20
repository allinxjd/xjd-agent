"""唤醒词检测 — Wake Word Detection.

支持:
- 关键词匹配 (简单模式)
- 音频流持续监听 (VAD + 关键词)
- 自定义唤醒词
- 检测回调

"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Coroutine, Optional

logger = logging.getLogger(__name__)

class WakeWordEngine(str, Enum):
    """唤醒词引擎."""

    KEYWORD = "keyword"        # 简单关键词匹配 (文本)
    PORCUPINE = "porcupine"    # Picovoice Porcupine (音频)
    OPENWAKEWORD = "openwakeword"  # OpenWakeWord (开源)

@dataclass
class WakeWordEvent:
    """唤醒事件."""

    keyword: str = ""
    confidence: float = 0.0
    timestamp: float = 0.0
    engine: str = ""

# 回调类型
WakeWordCallback = Callable[[WakeWordEvent], Coroutine[Any, Any, None]]

class WakeWordDetector:
    """唤醒词检测器."""

    def __init__(
        self,
        keywords: Optional[list[str]] = None,
        engine: WakeWordEngine = WakeWordEngine.KEYWORD,
        sensitivity: float = 0.5,
        on_wake: Optional[WakeWordCallback] = None,
    ) -> None:
        self._keywords = keywords or ["小巨蛋", "hey egg", "xjd"]
        self._engine = engine
        self._sensitivity = sensitivity
        self._on_wake = on_wake
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._detection_count = 0

    @property
    def keywords(self) -> list[str]:
        return self._keywords

    def add_keyword(self, keyword: str) -> None:
        """添加唤醒词."""
        if keyword not in self._keywords:
            self._keywords.append(keyword)

    def remove_keyword(self, keyword: str) -> bool:
        """移除唤醒词."""
        if keyword in self._keywords:
            self._keywords.remove(keyword)
            return True
        return False

    def detect_in_text(self, text: str) -> Optional[WakeWordEvent]:
        """在文本中检测唤醒词 (简单模式)."""
        text_lower = text.lower().strip()
        for kw in self._keywords:
            if kw.lower() in text_lower:
                event = WakeWordEvent(
                    keyword=kw, confidence=1.0,
                    timestamp=time.time(), engine="keyword",
                )
                self._detection_count += 1
                return event
        return None

    async def detect_in_audio(self, audio_data: bytes, sample_rate: int = 16000) -> Optional[WakeWordEvent]:
        """在音频数据中检测唤醒词."""
        if self._engine == WakeWordEngine.PORCUPINE:
            return await self._detect_porcupine(audio_data, sample_rate)
        elif self._engine == WakeWordEngine.OPENWAKEWORD:
            return await self._detect_openwakeword(audio_data, sample_rate)
        else:
            # 关键词模式不支持音频
            return None

    async def _detect_porcupine(self, audio_data: bytes, sample_rate: int) -> Optional[WakeWordEvent]:
        """使用 Porcupine 检测."""
        try:
            import pvporcupine
            import struct

            porcupine = pvporcupine.create(keywords=self._keywords, sensitivities=[self._sensitivity] * len(self._keywords))
            pcm = struct.unpack_from(f"{len(audio_data) // 2}h", audio_data)

            frame_length = porcupine.frame_length
            for i in range(0, len(pcm) - frame_length, frame_length):
                frame = pcm[i:i + frame_length]
                result = porcupine.process(frame)
                if result >= 0:
                    event = WakeWordEvent(
                        keyword=self._keywords[result],
                        confidence=self._sensitivity,
                        timestamp=time.time(),
                        engine="porcupine",
                    )
                    self._detection_count += 1
                    porcupine.delete()
                    return event

            porcupine.delete()
        except ImportError:
            logger.warning("pvporcupine 未安装，请运行: pip install pvporcupine")
        except Exception as e:
            logger.error("Porcupine 检测失败: %s", e)
        return None

    async def _detect_openwakeword(self, audio_data: bytes, sample_rate: int) -> Optional[WakeWordEvent]:
        """使用 OpenWakeWord 检测."""
        try:
            from openwakeword.model import Model
            import numpy as np

            model = Model()
            audio_array = np.frombuffer(audio_data, dtype=np.int16).astype(np.float32) / 32768.0
            prediction = model.predict(audio_array)

            for model_name, scores in prediction.items():
                max_score = max(scores) if scores else 0
                if max_score > self._sensitivity:
                    event = WakeWordEvent(
                        keyword=model_name, confidence=float(max_score),
                        timestamp=time.time(), engine="openwakeword",
                    )
                    self._detection_count += 1
                    return event
        except ImportError:
            logger.warning("openwakeword 未安装，请运行: pip install openwakeword")
        except Exception as e:
            logger.error("OpenWakeWord 检测失败: %s", e)
        return None

    async def start_listening(self) -> None:
        """启动持续监听 (需要麦克风)."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._listen_loop())
        logger.info("唤醒词监听已启动: %s", self._keywords)

    async def stop_listening(self) -> None:
        """停止监听."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _listen_loop(self) -> None:
        """持续监听循环."""
        try:
            import pyaudio
        except ImportError:
            logger.error("pyaudio 未安装，无法启动麦克风监听")
            return

        pa = pyaudio.PyAudio()
        stream = pa.open(format=pyaudio.paInt16, channels=1, rate=16000, input=True, frames_per_buffer=512)

        try:
            while self._running:
                data = stream.read(512, exception_on_overflow=False)
                event = await self.detect_in_audio(data, 16000)
                if event and self._on_wake:
                    await self._on_wake(event)
                await asyncio.sleep(0)  # yield
        except asyncio.CancelledError:
            pass
        finally:
            stream.stop_stream()
            stream.close()
            pa.terminate()

    @property
    def detection_count(self) -> int:
        return self._detection_count

    @property
    def is_listening(self) -> bool:
        return self._running
