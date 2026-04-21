"""电商做图工具 — 调用卡拉贝斯 AI 做图平台生成专业电商图片.

从 extended.py 提取，保持逻辑完全一致。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
#  AI 电商做图 (卡拉贝斯平台)
# ═══════════════════════════════════════════════════════════════════

_calabash_session: dict = {}  # {cookies: dict, expires_at: float}

async def _calabash_login(api_url: str, phone: str, password: str) -> dict:
    """登录做图平台，返回 cookies dict，带 30 分钟缓存."""
    import time
    now = time.time()
    if _calabash_session.get("cookies") and now < _calabash_session.get("expires_at", 0):
        return _calabash_session["cookies"]

    import httpx
    transport = httpx.AsyncHTTPTransport()
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True, transport=transport) as client:
        resp = await client.post(
            f"{api_url}/api/auth/login",
            json={"phone": phone, "password": password},
        )
        resp.raise_for_status()
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(f"登录失败: {data.get('error', 'unknown')}")
        cookies = dict(resp.cookies)
        _calabash_session["cookies"] = cookies
        _calabash_session["expires_at"] = now + 1800  # 30 min
        return cookies


async def generate_ecommerce_image(
    platform: str = "taobao",
    kind: str = "main",
    description: str = "",
    reference_image: str = "",
    style: str = "",
    count: int = 1,
    save_dir: str = "",
) -> str:
    """调用卡拉贝斯 AI 做图平台生成电商图片.

    Args:
        platform: 电商平台 (taobao/jd/xiaohongshu/douyin/pdd/dewu/tiktok/ali1688)
        kind: 图片类型 (main=主图/single=产品单图/detail=详情图)
        description: 产品描述或提示词
        reference_image: 参考图片本地路径 (必填)
        style: 风格描述 (可选)
        count: 生成数量 1-4
        save_dir: 保存目录 (默认 ~/Downloads/xjd-generated/)

    Returns:
        JSON 结果: {success, images: [{path, size_kb}], platform, kind}
    """
    import base64
    import time

    # 加载凭证
    from agent.core.secrets import get_secrets_store
    secrets = get_secrets_store()
    phone = secrets.get("ecommerce-image-pipeline", "CALABASH_PHONE")
    password = secrets.get("ecommerce-image-pipeline", "CALABASH_PASSWORD")
    api_url = secrets.get("ecommerce-image-pipeline", "CALABASH_API_URL", "https://ai.allinxjd.com").rstrip("/")
    if not phone or not password:
        return json.dumps({"success": False, "error": "未配置做图平台凭证，请在设置 → 技能凭证中配置 CALABASH_PHONE 和 CALABASH_PASSWORD"}, ensure_ascii=False)

    # 验证参考图片
    if not reference_image:
        return json.dumps({"success": False, "error": "必须提供 reference_image 参考图片路径"}, ensure_ascii=False)

    ref_path = Path(reference_image).expanduser()
    if not ref_path.exists():
        return json.dumps({"success": False, "error": f"参考图片不存在: {ref_path}"}, ensure_ascii=False)

    # 登录
    try:
        cookies = await _calabash_login(api_url, phone, password)
    except Exception as e:
        return json.dumps({"success": False, "error": f"登录失败: {e}"}, ensure_ascii=False)

    # 构建请求
    import httpx
    count = max(1, min(4, count))
    ref_bytes = ref_path.read_bytes()
    mime = "image/png" if ref_path.suffix.lower() == ".png" else "image/jpeg"

    files = {"reference": (ref_path.name, ref_bytes, mime)}
    form_data = {
        "platform": platform,
        "kind": kind,
        "description": description or "电商产品图",
        "count": str(count),
    }
    if style:
        form_data["style"] = style

    try:
        transport = httpx.AsyncHTTPTransport()
        async with httpx.AsyncClient(timeout=120.0, cookies=cookies, follow_redirects=True, transport=transport) as client:
            resp = await client.post(
                f"{api_url}/api/generate",
                data=form_data,
                files=files,
            )
            if resp.status_code == 401:
                # cookie 过期，重新登录重试
                _calabash_session.clear()
                cookies = await _calabash_login(api_url, phone, password)
                resp = await client.post(
                    f"{api_url}/api/generate",
                    data=form_data,
                    files=files,
                    cookies=cookies,
                )
            if resp.status_code == 402 or resp.status_code == 403:
                try:
                    err_data = resp.json()
                    err_msg = err_data.get("error", "")
                except Exception:
                    err_msg = resp.text
                if "余额" in err_msg or "balance" in err_msg.lower() or "insufficient" in err_msg.lower() or resp.status_code == 402:
                    import webbrowser
                    webbrowser.open("https://ai.calabashai.cn")
                    return json.dumps({"success": False, "error": "账户余额不足，已为您打开充值页面（ai.calabashai.cn），充值完成后重新生成即可"}, ensure_ascii=False)
            resp.raise_for_status()
            result = resp.json()
    except Exception as e:
        return json.dumps({"success": False, "error": f"API 调用失败: {e}"}, ensure_ascii=False)

    # 保存图片
    images_data = result.get("images", [])
    if not images_data:
        return json.dumps({"success": False, "error": "API 返回空图片", "raw": str(result.get("error", ""))}, ensure_ascii=False)

    out_dir = Path(save_dir).expanduser() if save_dir else Path.home() / "Downloads" / "xjd-generated"
    out_dir.mkdir(parents=True, exist_ok=True)

    saved = []
    ts = int(time.time())
    for i, img in enumerate(images_data):
        b64 = img.get("base64", "")
        if not b64:
            continue
        img_bytes = base64.b64decode(b64)
        ext = "png" if img.get("mimeType", "").endswith("png") else "jpg"
        filename = f"{platform}_{kind}_{ts}_{i+1}.{ext}"
        out_path = out_dir / filename
        out_path.write_bytes(img_bytes)
        saved.append({"path": str(out_path), "size_kb": round(len(img_bytes) / 1024, 1)})

    return json.dumps({
        "success": True,
        "images": saved,
        "platform": platform,
        "kind": kind,
        "count": len(saved),
    }, ensure_ascii=False)


# ═══════════════════════════════════════════════════════════════════
#  注册
# ═══════════════════════════════════════════════════════════════════

def register_ecommerce_tools(registry) -> None:
    """注册电商做图工具到 registry."""

    # ── AI 电商做图 (卡拉贝斯平台) ──
    registry.register(
        name="generate_ecommerce_image",
        description="调用 AI 做图平台生成专业电商图片 (淘宝/京东/小红书/抖音/拼多多/得物/TikTok/1688)。必须提供参考图片。",
        parameters={
            "type": "object",
            "properties": {
                "platform": {
                    "type": "string",
                    "description": "电商平台",
                    "enum": ["taobao", "jd", "xiaohongshu", "douyin", "pdd", "dewu", "tiktok", "ali1688"],
                },
                "kind": {
                    "type": "string",
                    "description": "图片类型: main(主图)/single(白底图)/detail(详情图)",
                    "enum": ["main", "single", "detail"],
                    "default": "main",
                },
                "description": {"type": "string", "description": "产品描述/提示词"},
                "reference_image": {"type": "string", "description": "参考图片本地路径 (必填)"},
                "style": {"type": "string", "description": "风格描述 (可选)", "default": ""},
                "count": {"type": "integer", "description": "生成数量 1-4", "default": 1},
                "save_dir": {"type": "string", "description": "保存目录 (默认 ~/Downloads/xjd-generated)", "default": ""},
            },
            "required": ["platform", "description", "reference_image"],
        },
        handler=generate_ecommerce_image,
        category="image",
    )
