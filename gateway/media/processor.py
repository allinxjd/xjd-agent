"""媒体处理引擎 — 图片生成 + 图片分析 + OCR + 视频.

支持:
- 图片生成: DALL-E 3, Stable Diffusion (本地/API), Midjourney (API)
- 图片分析: GPT-4V, Claude Vision, Gemini Vision
- OCR: Tesseract (本地), 在线 OCR API
- 视频: 截帧, 缩略图

架构:
    MediaProcessor
      ├── ImageGenerator (图片生成)
      ├── ImageAnalyzer (图片分析/理解)
      ├── OCREngine (文字识别)
      └── VideoProcessor (视频处理)
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import tempfile
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

class ImageGenProvider(str, Enum):
    DALLE = "dalle"
    STABLE_DIFFUSION = "stable_diffusion"
    MIDJOURNEY = "midjourney"

class ImageSize(str, Enum):
    S256 = "256x256"
    S512 = "512x512"
    S1024 = "1024x1024"
    S1024x1792 = "1024x1792"
    S1792x1024 = "1792x1024"

@dataclass
class GeneratedImage:
    """生成的图片."""

    url: str = ""
    data: bytes = b""            # base64 decoded
    revised_prompt: str = ""
    format: str = "png"
    width: int = 0
    height: int = 0

@dataclass
class ImageAnalysis:
    """图片分析结果."""

    description: str = ""
    objects: list[str] = field(default_factory=list)
    text_content: str = ""        # OCR 结果
    tags: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

@dataclass
class OCRResult:
    """OCR 结果."""

    text: str = ""
    blocks: list[dict] = field(default_factory=list)  # {text, bbox, confidence}
    language: str = ""

# ═══════════════════════════════════════════════════════════════════
#  图片生成
# ═══════════════════════════════════════════════════════════════════

class BaseImageGenerator(ABC):
    @abstractmethod
    async def generate(
        self,
        prompt: str,
        size: str = "1024x1024",
        n: int = 1,
        **kwargs,
    ) -> list[GeneratedImage]:
        ...

class DALLEGenerator(BaseImageGenerator):
    """DALL-E 图片生成."""

    def __init__(self, api_key: str = "", base_url: str = "") -> None:
        self._api_key = api_key
        self._base_url = base_url

    async def generate(
        self,
        prompt: str,
        size: str = "1024x1024",
        n: int = 1,
        model: str = "dall-e-3",
        quality: str = "standard",
        style: str = "vivid",
        **kwargs,
    ) -> list[GeneratedImage]:
        try:
            import httpx

            url = (self._base_url or "https://api.openai.com/v1") + "/images/generations"

            body: dict[str, Any] = {
                "model": model,
                "prompt": prompt,
                "size": size,
                "n": n,
                "quality": quality,
                "style": style,
                "response_format": "url",
            }

            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(
                    url,
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                    json=body,
                )
                resp.raise_for_status()
                data = resp.json()

            images = []
            for item in data.get("data", []):
                img = GeneratedImage(
                    url=item.get("url", ""),
                    revised_prompt=item.get("revised_prompt", ""),
                )
                if "b64_json" in item:
                    img.data = base64.b64decode(item["b64_json"])
                images.append(img)

            return images

        except Exception as e:
            logger.error("DALL-E generation error: %s", e)
            return []

class StableDiffusionGenerator(BaseImageGenerator):
    """Stable Diffusion (通过 API 或本地 diffusers)."""

    def __init__(
        self,
        api_url: str = "",
        api_key: str = "",
    ) -> None:
        self._api_url = api_url or "http://127.0.0.1:7860"
        self._api_key = api_key

    async def generate(
        self,
        prompt: str,
        size: str = "512x512",
        n: int = 1,
        negative_prompt: str = "",
        steps: int = 30,
        cfg_scale: float = 7.0,
        **kwargs,
    ) -> list[GeneratedImage]:
        try:
            import httpx

            w, h = (int(x) for x in size.split("x"))

            body = {
                "prompt": prompt,
                "negative_prompt": negative_prompt or "low quality, blurry, nsfw",
                "width": w,
                "height": h,
                "steps": steps,
                "cfg_scale": cfg_scale,
                "batch_size": n,
            }

            headers = {}
            if self._api_key:
                headers["Authorization"] = f"Bearer {self._api_key}"

            url = f"{self._api_url}/sdapi/v1/txt2img"
            async with httpx.AsyncClient(timeout=180.0) as client:
                resp = await client.post(url, headers=headers, json=body)
                resp.raise_for_status()
                data = resp.json()

            images = []
            for b64 in data.get("images", []):
                img_bytes = base64.b64decode(b64)
                images.append(GeneratedImage(
                    data=img_bytes,
                    format="png",
                    width=w,
                    height=h,
                ))

            return images

        except Exception as e:
            logger.error("SD generation error: %s", e)
            return []

# ═══════════════════════════════════════════════════════════════════
#  图片分析
# ═══════════════════════════════════════════════════════════════════

class ImageAnalyzer:
    """图片分析 — 使用多模态 LLM."""

    def __init__(self, model_router=None) -> None:
        self._router = model_router

    async def analyze(
        self,
        image: bytes | str,
        prompt: str = "请描述这张图片的内容。",
        format: str = "png",
    ) -> ImageAnalysis:
        """分析图片.

        Args:
            image: 图片数据 (bytes) 或 URL (str)
            prompt: 分析提示
            format: 图片格式

        Returns:
            分析结果
        """
        if not self._router:
            return ImageAnalysis(description="Error: model router not set")

        try:
            from agent.providers.base import Message

            # 构建多模态消息
            if isinstance(image, bytes):
                b64 = base64.b64encode(image).decode("utf-8")
                image_content = {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/{format};base64,{b64}",
                    },
                }
            else:
                image_content = {
                    "type": "image_url",
                    "image_url": {"url": image},
                }

            content = [
                {"type": "text", "text": prompt},
                image_content,
            ]

            response = await self._router.complete_with_failover(
                messages=[Message(role="user", content=content)],
                user_message=prompt,
                temperature=0.3,
            )

            return ImageAnalysis(
                description=response.content,
                raw={"usage": response.usage.__dict__},
            )

        except Exception as e:
            logger.error("Image analysis error: %s", e)
            return ImageAnalysis(description=f"Error: {e}")

# ═══════════════════════════════════════════════════════════════════
#  OCR
# ═══════════════════════════════════════════════════════════════════

class OCREngine:
    """OCR 引擎 — 文字识别."""

    def __init__(self, use_tesseract: bool = True) -> None:
        self._use_tesseract = use_tesseract

    async def recognize(
        self,
        image: bytes,
        language: str = "chi_sim+eng",
        format: str = "png",
    ) -> OCRResult:
        """识别图片中的文字.

        Args:
            image: 图片数据
            language: Tesseract 语言代码
            format: 图片格式
        """
        if self._use_tesseract:
            return await self._tesseract_ocr(image, language, format)
        else:
            return OCRResult(text="[OCR: no engine configured]")

    async def _tesseract_ocr(
        self,
        image: bytes,
        language: str,
        format: str,
    ) -> OCRResult:
        """使用 Tesseract OCR."""
        try:
            from PIL import Image
            import pytesseract

            img = Image.open(io.BytesIO(image))

            loop = asyncio.get_event_loop()
            text = await loop.run_in_executor(
                None,
                lambda: pytesseract.image_to_string(img, lang=language),
            )

            # 获取详细信息
            data = await loop.run_in_executor(
                None,
                lambda: pytesseract.image_to_data(img, lang=language, output_type=pytesseract.Output.DICT),
            )

            blocks = []
            if data and "text" in data:
                for i, txt in enumerate(data["text"]):
                    if txt.strip():
                        conf = float(data["conf"][i]) if data["conf"][i] != "-1" else 0
                        blocks.append({
                            "text": txt,
                            "confidence": conf / 100.0,
                            "bbox": {
                                "x": data["left"][i],
                                "y": data["top"][i],
                                "w": data["width"][i],
                                "h": data["height"][i],
                            },
                        })

            return OCRResult(
                text=text.strip(),
                blocks=blocks,
                language=language,
            )

        except ImportError:
            return OCRResult(text="[需安装: pip install Pillow pytesseract]")
        except Exception as e:
            logger.error("Tesseract OCR error: %s", e)
            return OCRResult(text=f"[OCR Error: {e}]")

# ═══════════════════════════════════════════════════════════════════
#  视频处理
# ═══════════════════════════════════════════════════════════════════

class VideoProcessor:
    """视频处理 — 截帧、缩略图、时长."""

    async def extract_frames(
        self,
        video_path: str,
        interval_sec: float = 1.0,
        max_frames: int = 10,
        format: str = "jpg",
    ) -> list[bytes]:
        """从视频提取帧.

        Args:
            video_path: 视频路径
            interval_sec: 截帧间隔 (秒)
            max_frames: 最大帧数
            format: 输出格式
        """
        try:
            import cv2

            cap = cv2.VideoCapture(video_path)
            fps = cap.get(cv2.CAP_PROP_FPS) or 30
            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            frame_interval = int(fps * interval_sec)

            frames = []
            frame_idx = 0

            while cap.isOpened() and len(frames) < max_frames:
                ret, frame = cap.read()
                if not ret:
                    break

                if frame_idx % frame_interval == 0:
                    if format == "jpg":
                        _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
                    else:
                        _, buf = cv2.imencode(f".{format}", frame)
                    frames.append(buf.tobytes())

                frame_idx += 1

            cap.release()
            return frames

        except ImportError:
            logger.error("opencv-python required: pip install opencv-python")
            return []
        except Exception as e:
            logger.error("Video frame extraction error: %s", e)
            return []

    async def get_thumbnail(
        self,
        video_path: str,
        time_sec: float = 1.0,
        size: tuple[int, int] = (320, 240),
    ) -> bytes:
        """获取视频缩略图."""
        frames = await self.extract_frames(
            video_path,
            interval_sec=time_sec,
            max_frames=1,
        )
        if not frames:
            return b""

        try:
            from PIL import Image

            img = Image.open(io.BytesIO(frames[0]))
            img.thumbnail(size)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85)
            return buf.getvalue()

        except ImportError:
            return frames[0]

    async def get_info(self, video_path: str) -> dict[str, Any]:
        """获取视频信息."""
        try:
            import cv2

            cap = cv2.VideoCapture(video_path)
            info = {
                "width": int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
                "height": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
                "fps": cap.get(cv2.CAP_PROP_FPS),
                "total_frames": int(cap.get(cv2.CAP_PROP_FRAME_COUNT)),
                "duration_sec": 0.0,
            }
            if info["fps"] > 0:
                info["duration_sec"] = info["total_frames"] / info["fps"]
            cap.release()
            return info

        except ImportError:
            return {"error": "opencv-python required"}
        except Exception as e:
            return {"error": str(e)}

# ═══════════════════════════════════════════════════════════════════
#  统一媒体处理器
# ═══════════════════════════════════════════════════════════════════

class MediaProcessor:
    """统一媒体处理器.

    用法:
        mp = MediaProcessor(config)
        await mp.initialize()

        # 生成图片
        images = await mp.generate_image("一只可爱的小猫", size="1024x1024")

        # 分析图片
        analysis = await mp.analyze_image(image_bytes, "这张图片里有什么？")

        # OCR
        ocr = await mp.ocr(image_bytes)
    """

    def __init__(
        self,
        dalle_api_key: str = "",
        sd_api_url: str = "",
        model_router=None,
    ) -> None:
        self._dalle_key = dalle_api_key
        self._sd_url = sd_api_url
        self._router = model_router

        self._generators: dict[str, BaseImageGenerator] = {}
        self._analyzer: Optional[ImageAnalyzer] = None
        self._ocr: Optional[OCREngine] = None
        self._video: Optional[VideoProcessor] = None

    async def initialize(self) -> None:
        """初始化所有处理器."""
        # 图片生成器
        if self._dalle_key:
            self._generators["dalle"] = DALLEGenerator(api_key=self._dalle_key)
        if self._sd_url:
            self._generators["sd"] = StableDiffusionGenerator(api_url=self._sd_url)

        # 图片分析
        if self._router:
            self._analyzer = ImageAnalyzer(model_router=self._router)

        # OCR
        self._ocr = OCREngine()

        # 视频
        self._video = VideoProcessor()

        logger.info("MediaProcessor initialized: generators=%s", list(self._generators.keys()))

    async def generate_image(
        self,
        prompt: str,
        provider: str = "dalle",
        size: str = "1024x1024",
        n: int = 1,
        **kwargs,
    ) -> list[GeneratedImage]:
        """生成图片."""
        gen = self._generators.get(provider)
        if not gen:
            available = list(self._generators.keys())
            if available:
                gen = self._generators[available[0]]
            else:
                return [GeneratedImage(url="", revised_prompt="Error: no image generator configured")]

        return await gen.generate(prompt, size=size, n=n, **kwargs)

    async def analyze_image(
        self,
        image: bytes | str,
        prompt: str = "请描述这张图片的内容。",
        format: str = "png",
    ) -> ImageAnalysis:
        """分析图片."""
        if not self._analyzer:
            return ImageAnalysis(description="Error: image analyzer not initialized")
        return await self._analyzer.analyze(image, prompt, format)

    async def ocr(
        self,
        image: bytes,
        language: str = "chi_sim+eng",
        format: str = "png",
    ) -> OCRResult:
        """OCR 文字识别."""
        if not self._ocr:
            return OCRResult(text="Error: OCR engine not initialized")
        return await self._ocr.recognize(image, language, format)

    async def video_info(self, path: str) -> dict:
        """获取视频信息."""
        if not self._video:
            return {"error": "video processor not initialized"}
        return await self._video.get_info(path)

    async def video_frames(
        self,
        path: str,
        interval: float = 1.0,
        max_frames: int = 10,
    ) -> list[bytes]:
        """提取视频帧."""
        if not self._video:
            return []
        return await self._video.extract_frames(path, interval, max_frames)
