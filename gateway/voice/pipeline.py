"""语音处理管线 — STT (语音转文字) + TTS (文字转语音).

支持:
- STT: faster-whisper (本地), OpenAI Whisper API, Azure Speech
- TTS: edge-tts (免费), elevenlabs (高质量), OpenAI TTS
- 实时对话模式 (VAD → STT → Agent → TTS → 播放)
- 多语言支持

架构:
    VoicePipeline
      ├── STTEngine (语音识别)
      ├── TTSEngine (语音合成)
      └── VAD (语音活动检测, 可选)
"""

from __future__ import annotations

import asyncio
import io
import logging
import tempfile
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, AsyncIterator, Optional

logger = logging.getLogger(__name__)

class STTProvider(str, Enum):
    """STT 引擎."""

    WHISPER_LOCAL = "whisper_local"      # faster-whisper (本地)
    WHISPER_API = "whisper_api"          # OpenAI Whisper API
    AZURE = "azure"                      # Azure Speech
    GOOGLE = "google"                    # Google Speech-to-Text

class TTSProvider(str, Enum):
    """TTS 引擎."""

    EDGE_TTS = "edge_tts"              # 微软 Edge TTS (免费)
    ELEVENLABS = "elevenlabs"          # ElevenLabs (高质量)
    OPENAI_TTS = "openai_tts"          # OpenAI TTS
    AZURE_TTS = "azure_tts"            # Azure TTS

@dataclass
class STTResult:
    """语音识别结果."""

    text: str = ""
    language: str = ""
    confidence: float = 0.0
    duration_sec: float = 0.0
    segments: list[dict] = field(default_factory=list)

@dataclass
class TTSResult:
    """语音合成结果."""

    audio_data: bytes = b""
    format: str = "mp3"               # "mp3" | "wav" | "ogg"
    duration_sec: float = 0.0
    sample_rate: int = 24000

@dataclass
class VoiceConfig:
    """语音配置."""

    # STT
    stt_provider: STTProvider = STTProvider.WHISPER_LOCAL
    stt_model: str = "base"            # whisper model size
    stt_language: str = ""             # 自动检测
    stt_api_key: str = ""
    stt_base_url: str = ""             # 自定义端点 (如 Groq)

    # TTS
    tts_provider: TTSProvider = TTSProvider.EDGE_TTS
    tts_voice: str = "zh-CN-XiaoxiaoNeural"  # Edge TTS 默认中文女声
    tts_speed: float = 1.0
    tts_api_key: str = ""
    tts_base_url: str = ""             # 自定义端点

    # VAD
    vad_enabled: bool = False
    vad_threshold: float = 0.5
    vad_min_silence_ms: int = 500

    # Fallback
    tts_fallback: Optional[TTSProvider] = TTSProvider.EDGE_TTS  # 降级到免费 Edge
    stt_fallback: Optional[STTProvider] = None

class BaseSTT(ABC):
    """STT 基类."""

    @abstractmethod
    async def transcribe(
        self,
        audio: bytes,
        language: str = "",
        format: str = "wav",
    ) -> STTResult:
        """转录音频."""
        ...

class BaseTTS(ABC):
    """TTS 基类."""

    @abstractmethod
    async def synthesize(
        self,
        text: str,
        voice: str = "",
        speed: float = 1.0,
        format: str = "mp3",
    ) -> TTSResult:
        """合成语音."""
        ...

    async def stream_synthesize(
        self,
        text: str,
        voice: str = "",
        speed: float = 1.0,
    ) -> AsyncIterator[bytes]:
        """流式合成."""
        result = await self.synthesize(text, voice, speed)
        yield result.audio_data

# ═══════════════════════════════════════════════════════════════════
#  STT 实现
# ═══════════════════════════════════════════════════════════════════

class WhisperLocalSTT(BaseSTT):
    """本地 faster-whisper STT."""

    def __init__(self, model_size: str = "base") -> None:
        self._model_size = model_size
        self._model = None

    def _ensure_model(self):
        if self._model is None:
            try:
                from faster_whisper import WhisperModel
                self._model = WhisperModel(
                    self._model_size,
                    device="auto",
                    compute_type="int8",
                )
            except ImportError:
                raise ImportError("faster-whisper 未安装。请运行: pip install faster-whisper")
        return self._model

    async def transcribe(
        self,
        audio: bytes,
        language: str = "",
        format: str = "wav",
    ) -> STTResult:
        """使用 faster-whisper 转录."""
        model = self._ensure_model()

        # 写入临时文件
        with tempfile.NamedTemporaryFile(suffix=f".{format}", delete=False) as f:
            f.write(audio)
            tmp_path = f.name

        try:
            loop = asyncio.get_event_loop()
            segments_gen, info = await loop.run_in_executor(
                None,
                lambda: model.transcribe(
                    tmp_path,
                    language=language or None,
                    beam_size=5,
                ),
            )

            segments = []
            full_text = []
            for seg in segments_gen:
                segments.append({
                    "start": seg.start,
                    "end": seg.end,
                    "text": seg.text,
                })
                full_text.append(seg.text)

            return STTResult(
                text="".join(full_text).strip(),
                language=info.language,
                confidence=1.0 - info.language_probability if info.language_probability else 0.0,
                duration_sec=info.duration,
                segments=segments,
            )

        finally:
            Path(tmp_path).unlink(missing_ok=True)

class WhisperAPISTT(BaseSTT):
    """OpenAI Whisper API STT."""

    def __init__(self, api_key: str = "", base_url: str = "") -> None:
        self._api_key = api_key
        self._base_url = base_url

    async def transcribe(
        self,
        audio: bytes,
        language: str = "",
        format: str = "wav",
    ) -> STTResult:
        """使用 OpenAI Whisper API."""
        import httpx

        url = (self._base_url or "https://api.openai.com/v1") + "/audio/transcriptions"
        last_err = None

        for attempt in range(3):
            try:
                files = {"file": (f"audio.{format}", io.BytesIO(audio), f"audio/{format}")}
                data: dict[str, Any] = {"model": "whisper-1"}
                if language:
                    data["language"] = language
                data["response_format"] = "verbose_json"

                async with httpx.AsyncClient(timeout=60.0) as client:
                    resp = await client.post(
                        url,
                        headers={"Authorization": f"Bearer {self._api_key}"},
                        files=files,
                        data=data,
                    )
                    resp.raise_for_status()
                    result = resp.json()

                return STTResult(
                    text=result.get("text", ""),
                    language=result.get("language", ""),
                    duration_sec=result.get("duration", 0),
                )

            except Exception as e:
                last_err = e
                if attempt < 2:
                    logger.warning("Whisper API attempt %d failed: %s", attempt + 1, e)
                    await asyncio.sleep(1.0 * (attempt + 1))

        logger.error("Whisper API error after retries: %s", last_err)
        return STTResult(text=f"[STT Error: {last_err}]")

# ═══════════════════════════════════════════════════════════════════
#  TTS 实现
# ═══════════════════════════════════════════════════════════════════

class EdgeTTS(BaseTTS):
    """微软 Edge TTS — 免费、高质量."""

    def __init__(self, voice: str = "zh-CN-XiaoxiaoNeural") -> None:
        self._voice = voice

    async def synthesize(
        self,
        text: str,
        voice: str = "",
        speed: float = 1.0,
        format: str = "mp3",
    ) -> TTSResult:
        """使用 edge-tts 合成 (带 429 重试)."""
        try:
            import edge_tts
        except ImportError:
            raise ImportError("edge-tts 未安装。请运行: pip install edge-tts")

        voice = voice or self._voice
        rate_str = f"+{int((speed - 1) * 100)}%" if speed >= 1 else f"{int((speed - 1) * 100)}%"
        last_err = None

        for attempt in range(4):
            try:
                communicate = edge_tts.Communicate(text, voice, rate=rate_str)
                audio_chunks = []
                async for chunk in communicate.stream():
                    if chunk["type"] == "audio":
                        audio_chunks.append(chunk["data"])

                audio_data = b"".join(audio_chunks)
                return TTSResult(
                    audio_data=audio_data,
                    format="mp3",
                    sample_rate=24000,
                )
            except Exception as e:
                last_err = e
                err_str = str(e)
                if "429" in err_str or "Too many requests" in err_str:
                    delay = 2.0 * (2 ** attempt)
                    logger.warning("Edge TTS 429 rate limited, retry %d/3 after %.0fs", attempt + 1, delay)
                    await asyncio.sleep(delay)
                    continue
                logger.error("Edge TTS error: %s", e)
                return TTSResult()

        logger.error("Edge TTS failed after retries: %s", last_err)
        return TTSResult()

    async def stream_synthesize(
        self,
        text: str,
        voice: str = "",
        speed: float = 1.0,
    ) -> AsyncIterator[bytes]:
        """流式合成 (带 429 重试)."""
        try:
            import edge_tts
        except ImportError:
            raise ImportError("edge-tts 未安装。请运行: pip install edge-tts")

        voice = voice or self._voice
        rate_str = f"+{int((speed - 1) * 100)}%" if speed >= 1 else f"{int((speed - 1) * 100)}%"

        for attempt in range(4):
            try:
                communicate = edge_tts.Communicate(text, voice, rate=rate_str)
                async for chunk in communicate.stream():
                    if chunk["type"] == "audio":
                        yield chunk["data"]
                return
            except Exception as e:
                err_str = str(e)
                if "429" in err_str or "Too many requests" in err_str:
                    delay = 2.0 * (2 ** attempt)
                    logger.warning("Edge TTS stream 429, retry %d/3 after %.0fs", attempt + 1, delay)
                    await asyncio.sleep(delay)
                    continue
                logger.error("Edge TTS stream error: %s", e)
                return

        logger.error("Edge TTS stream failed after retries")

    async def list_voices(self, language: str = "zh") -> list[dict]:
        """列出可用语音."""
        try:
            import edge_tts
            voices = await edge_tts.list_voices()
            if language:
                voices = [v for v in voices if v.get("Locale", "").startswith(language)]
            return voices
        except Exception:
            return []

class ElevenLabsTTS(BaseTTS):
    """ElevenLabs TTS — 最逼真的 AI 语音."""

    def __init__(self, api_key: str = "", voice_id: str = "") -> None:
        self._api_key = api_key
        self._voice_id = voice_id or "21m00Tcm4TlvDq8ikWAM"  # Rachel

    async def synthesize(
        self,
        text: str,
        voice: str = "",
        speed: float = 1.0,
        format: str = "mp3",
    ) -> TTSResult:
        import httpx

        voice_id = voice or self._voice_id
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
        last_err = None

        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    resp = await client.post(
                        url,
                        headers={
                            "xi-api-key": self._api_key,
                            "Content-Type": "application/json",
                        },
                        json={
                            "text": text,
                            "model_id": "eleven_multilingual_v2",
                            "voice_settings": {
                                "stability": 0.5,
                                "similarity_boost": 0.75,
                            },
                        },
                    )
                    resp.raise_for_status()

                    return TTSResult(
                        audio_data=resp.content,
                        format="mp3",
                        sample_rate=44100,
                    )

            except Exception as e:
                last_err = e
                if attempt < 2:
                    logger.warning("ElevenLabs attempt %d failed: %s", attempt + 1, e)
                    await asyncio.sleep(1.0 * (attempt + 1))

        logger.error("ElevenLabs TTS error after retries: %s", last_err)
        return TTSResult()

class OpenAITTS(BaseTTS):
    """OpenAI TTS."""

    def __init__(self, api_key: str = "", base_url: str = "") -> None:
        self._api_key = api_key
        self._base_url = base_url

    async def synthesize(
        self,
        text: str,
        voice: str = "",
        speed: float = 1.0,
        format: str = "mp3",
    ) -> TTSResult:
        import httpx

        url = (self._base_url or "https://api.openai.com/v1") + "/audio/speech"
        last_err = None

        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=60.0) as client:
                    resp = await client.post(
                        url,
                        headers={
                            "Authorization": f"Bearer {self._api_key}",
                            "Content-Type": "application/json",
                        },
                        json={
                            "model": "tts-1",
                            "voice": voice or "alloy",
                            "input": text,
                            "speed": speed,
                            "response_format": format,
                        },
                    )
                    resp.raise_for_status()

                    return TTSResult(
                        audio_data=resp.content,
                        format=format,
                        sample_rate=24000,
                    )

            except Exception as e:
                last_err = e
                if attempt < 2:
                    logger.warning("OpenAI TTS attempt %d failed: %s", attempt + 1, e)
                    await asyncio.sleep(1.0 * (attempt + 1))

        logger.error("OpenAI TTS error after retries: %s", last_err)
        return TTSResult()

# ═══════════════════════════════════════════════════════════════════
#  Voice Pipeline
# ═══════════════════════════════════════════════════════════════════

class VoicePipeline:
    """语音处理管线 — 集成 STT + TTS + Agent.

    用法:
        pipeline = VoicePipeline(VoiceConfig(
            stt_provider=STTProvider.WHISPER_LOCAL,
            tts_provider=TTSProvider.EDGE_TTS,
        ))
        await pipeline.initialize()

        # 语音转文字
        stt_result = await pipeline.speech_to_text(audio_bytes)

        # 文字转语音
        tts_result = await pipeline.text_to_speech("你好，有什么可以帮助你？")

        # 端到端: 音频 → Agent → 音频
        reply_audio = await pipeline.process_voice(audio_bytes, agent_callback)
    """

    def __init__(self, config: Optional[VoiceConfig] = None) -> None:
        self.config = config or VoiceConfig()
        self._stt: Optional[BaseSTT] = None
        self._tts: Optional[BaseTTS] = None
        self._stt_fallback: Optional[BaseSTT] = None
        self._tts_fallback: Optional[BaseTTS] = None

    async def initialize(self) -> None:
        """初始化引擎."""
        # 初始化 STT
        self._stt = self._create_stt(self.config.stt_provider)

        # 初始化 TTS
        self._tts = self._create_tts(self.config.tts_provider)

        # 初始化 fallback
        if self.config.stt_fallback and self.config.stt_fallback != self.config.stt_provider:
            self._stt_fallback = self._create_stt(self.config.stt_fallback)
        if self.config.tts_fallback and self.config.tts_fallback != self.config.tts_provider:
            self._tts_fallback = self._create_tts(self.config.tts_fallback)

        logger.info("Voice pipeline initialized: STT=%s (fallback=%s), TTS=%s (fallback=%s)",
                     self.config.stt_provider.value,
                     self.config.stt_fallback.value if self.config.stt_fallback else "none",
                     self.config.tts_provider.value,
                     self.config.tts_fallback.value if self.config.tts_fallback else "none")

    def _create_stt(self, provider: STTProvider) -> BaseSTT:
        """创建 STT 引擎实例."""
        if provider == STTProvider.WHISPER_LOCAL:
            return WhisperLocalSTT(model_size=self.config.stt_model)
        elif provider == STTProvider.WHISPER_API:
            return WhisperAPISTT(
                api_key=self.config.stt_api_key,
                base_url=self.config.stt_base_url,
            )
        else:
            logger.warning("STT provider %s not yet supported, using Whisper local", provider)
            return WhisperLocalSTT(model_size=self.config.stt_model)

    def _create_tts(self, provider: TTSProvider) -> BaseTTS:
        """创建 TTS 引擎实例."""
        if provider == TTSProvider.EDGE_TTS:
            return EdgeTTS(voice=self.config.tts_voice)
        elif provider == TTSProvider.ELEVENLABS:
            return ElevenLabsTTS(api_key=self.config.tts_api_key)
        elif provider == TTSProvider.OPENAI_TTS:
            return OpenAITTS(
                api_key=self.config.tts_api_key,
                base_url=self.config.tts_base_url,
            )
        else:
            logger.warning("TTS provider %s not yet supported, using Edge TTS", provider)
            return EdgeTTS(voice=self.config.tts_voice)

    async def speech_to_text(
        self,
        audio: bytes,
        language: str = "",
        format: str = "wav",
    ) -> STTResult:
        """语音转文字 (带 fallback)."""
        if not self._stt:
            raise RuntimeError("Voice pipeline not initialized")

        lang = language or self.config.stt_language
        result = await self._stt.transcribe(audio, language=lang, format=format)

        # 主引擎失败且有 fallback
        if (not result.text or result.text.startswith("[STT Error")) and self._stt_fallback:
            logger.warning("STT primary failed, falling back")
            result = await self._stt_fallback.transcribe(audio, language=lang, format=format)

        return result

    async def text_to_speech(
        self,
        text: str,
        voice: str = "",
        speed: float = 0.0,
        format: str = "mp3",
    ) -> TTSResult:
        """文字转语音 (带 fallback)."""
        if not self._tts:
            raise RuntimeError("Voice pipeline not initialized")

        v = voice or self.config.tts_voice
        s = speed if speed > 0 else self.config.tts_speed
        result = await self._tts.synthesize(text, voice=v, speed=s, format=format)

        # 主引擎失败且有 fallback
        if not result.audio_data and self._tts_fallback:
            logger.warning("TTS primary failed, falling back")
            result = await self._tts_fallback.synthesize(text, voice=v, speed=s, format=format)

        return result

    async def stream_tts(
        self,
        text: str,
        voice: str = "",
        speed: float = 0.0,
    ) -> AsyncIterator[bytes]:
        """流式文字转语音."""
        if not self._tts:
            raise RuntimeError("Voice pipeline not initialized")

        v = voice or self.config.tts_voice
        s = speed if speed > 0 else self.config.tts_speed
        async for chunk in self._tts.stream_synthesize(text, voice=v, speed=s):
            yield chunk

    async def process_voice(
        self,
        audio: bytes,
        agent_callback,
        audio_format: str = "wav",
        tts_voice: str = "",
        timeout: float = 60.0,
    ) -> TTSResult:
        """端到端语音处理: 音频 → STT → Agent → TTS → 音频.

        Args:
            audio: 输入音频
            agent_callback: Agent 处理函数 async (text) -> str
            audio_format: 输入格式
            tts_voice: TTS 语音
            timeout: 总超时秒数 (默认 60s)

        Returns:
            合成的回复音频
        """
        try:
            return await asyncio.wait_for(
                self._process_voice_inner(audio, agent_callback, audio_format, tts_voice),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            logger.error("Voice pipeline timed out after %.0fs", timeout)
            return TTSResult()

    async def _process_voice_inner(
        self,
        audio: bytes,
        agent_callback,
        audio_format: str,
        tts_voice: str,
    ) -> TTSResult:
        """端到端语音处理内部实现."""
        # 1. STT
        stt_result = await self.speech_to_text(audio, format=audio_format)
        if not stt_result.text:
            return TTSResult()

        logger.info("STT: %s", stt_result.text[:100])

        # 2. Agent
        reply_text = await agent_callback(stt_result.text)
        if not reply_text:
            return TTSResult()

        logger.info("Agent reply: %s", reply_text[:100])

        # 3. TTS
        tts_result = await self.text_to_speech(reply_text, voice=tts_voice)

        return tts_result
