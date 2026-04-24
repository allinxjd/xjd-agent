"""桌面控制工具 — Computer Use (支持 4 种工具:
- mouse_control: 鼠标移动/点击/拖拽/滚动
- keyboard_control: 按键/组合键/文字输入
- window_manager: 窗口查找/激活/调整
- screen_ocr: 截屏 + OCR 文字识别

pyautogui + pytesseract 为可选依赖，首次调用时检查。

用法:
    from agent.tools.computer_use import register_computer_use_tools
    register_computer_use_tools(registry)
"""

from __future__ import annotations

import logging
import platform
import subprocess
import tempfile
from agent.core.workspace_files import workspace_tmp
from typing import Any

logger = logging.getLogger(__name__)

def _check_pyautogui():
    """检查 pyautogui 是否可用."""
    try:
        import pyautogui
        return pyautogui
    except ImportError:
        raise ImportError(
            "pyautogui 未安装。请运行:\n"
            "  pip install 'xjd-agent[computer-use]'"
        )

# ── Mouse Control ────────────────────────────────────────────

async def _mouse_control(
    action: str,
    x: int = 0,
    y: int = 0,
    button: str = "left",
    duration: float = 0.5,
    clicks: int = 1,
    scroll_amount: int = 3,
    **kwargs,
) -> str:
    """鼠标控制."""
    pyautogui = _check_pyautogui()
    try:
        if action == "move":
            pyautogui.moveTo(x, y, duration=duration)
            return f"鼠标移动到 ({x}, {y})"
        elif action == "click":
            pyautogui.click(x=x or None, y=y or None, button=button, clicks=clicks)
            return f"鼠标点击 ({x}, {y}) button={button} clicks={clicks}"
        elif action == "double_click":
            pyautogui.doubleClick(x=x or None, y=y or None, button=button)
            return f"鼠标双击 ({x}, {y})"
        elif action == "right_click":
            pyautogui.rightClick(x=x or None, y=y or None)
            return f"鼠标右键 ({x}, {y})"
        elif action == "drag":
            pyautogui.moveTo(x, y, duration=duration)
            # drag to target — kwargs may contain target_x, target_y
            tx = kwargs.get("target_x", x + 100)
            ty = kwargs.get("target_y", y)
            pyautogui.dragTo(tx, ty, duration=duration, button=button)
            return f"拖拽从 ({x},{y}) 到 ({tx},{ty})"
        elif action == "scroll":
            pyautogui.scroll(scroll_amount, x=x or None, y=y or None)
            return f"滚动 {scroll_amount} 格"
        elif action == "position":
            pos = pyautogui.position()
            return f"当前鼠标位置: ({pos.x}, {pos.y})"
        else:
            return f"未知操作: {action}。支持: move, click, double_click, right_click, drag, scroll, position"
    except Exception as e:
        return f"鼠标操作失败: {e}"

# ── Keyboard Control ─────────────────────────────────────────

async def _keyboard_control(
    action: str,
    key: str = "",
    text: str = "",
    hotkey: str = "",
    interval: float = 0.05,
    **kwargs,
) -> str:
    """键盘控制."""
    pyautogui = _check_pyautogui()
    try:
        if action == "press":
            if not key:
                return "错误: press 需要 key 参数"
            pyautogui.press(key)
            return f"按键: {key}"
        elif action == "hotkey":
            if not hotkey:
                return "错误: hotkey 需要 hotkey 参数 (如 'ctrl+c')"
            keys = [k.strip() for k in hotkey.split("+")]
            pyautogui.hotkey(*keys)
            return f"组合键: {hotkey}"
        elif action == "type":
            if not text:
                return "错误: type 需要 text 参数"
            pyautogui.typewrite(text, interval=interval) if text.isascii() else pyautogui.write(text)
            return f"输入文本: {text[:50]}{'...' if len(text) > 50 else ''}"
        elif action == "hold":
            if not key or not text:
                return "错误: hold 需要 key (修饰键) 和 text (按键) 参数"
            with pyautogui.hold(key):
                pyautogui.press(text)
            return f"按住 {key} + {text}"
        else:
            return f"未知操作: {action}。支持: press, hotkey, type, hold"
    except Exception as e:
        return f"键盘操作失败: {e}"

# ── Window Manager ───────────────────────────────────────────

async def _window_manager(
    action: str,
    title: str = "",
    x: int = 0,
    y: int = 0,
    width: int = 0,
    height: int = 0,
    **kwargs,
) -> str:
    """窗口管理."""
    system = platform.system()
    try:
        if action == "list":
            if system == "Darwin":
                script = '''
                    tell application "System Events"
                        set windowList to ""
                        repeat with proc in (every process whose visible is true)
                            set windowList to windowList & name of proc & "\\n"
                        end repeat
                    end tell
                    return windowList
                '''
                r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=10)
                return r.stdout.strip() or "(无可见窗口)"
            else:
                r = subprocess.run(["wmctrl", "-l"], capture_output=True, text=True, timeout=10)
                return r.stdout.strip() or "(无窗口)"

        elif action == "find":
            if not title:
                return "错误: find 需要 title 参数"
            if system == "Darwin":
                script = f'''
                    tell application "System Events"
                        set matched to ""
                        repeat with proc in (every process whose name contains "{title}")
                            set matched to matched & name of proc & "\\n"
                        end repeat
                    end tell
                    return matched
                '''
                r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=10)
                return r.stdout.strip() or f"未找到包含 '{title}' 的窗口"
            else:
                r = subprocess.run(["wmctrl", "-l"], capture_output=True, text=True, timeout=10)
                lines = [l for l in r.stdout.split("\n") if title.lower() in l.lower()]
                return "\n".join(lines) or f"未找到包含 '{title}' 的窗口"

        elif action == "activate":
            if not title:
                return "错误: activate 需要 title 参数"
            if system == "Darwin":
                script = f'tell application "{title}" to activate'
                subprocess.run(["osascript", "-e", script], timeout=10)
            else:
                subprocess.run(["wmctrl", "-a", title], timeout=10)
            return f"已激活窗口: {title}"

        elif action in ("minimize", "maximize"):
            if not title:
                return f"错误: {action} 需要 title 参数"
            if system == "Darwin":
                prop = "miniaturized" if action == "minimize" else "zoomed"
                script = f'''
                    tell application "{title}"
                        set {prop} of front window to true
                    end tell
                '''
                subprocess.run(["osascript", "-e", script], timeout=10)
            else:
                flag = "-b" if action == "maximize" else "-b"
                val = "add,maximized_vert,maximized_horz" if action == "maximize" else "add,hidden"
                subprocess.run(["wmctrl", "-r", title, flag, val], timeout=10)
            return f"已{action}窗口: {title}"

        elif action == "resize":
            if not title or not width or not height:
                return "错误: resize 需要 title, width, height 参数"
            if system == "Darwin":
                script = f'''
                    tell application "{title}"
                        set bounds of front window to {{{x}, {y}, {x + width}, {y + height}}}
                    end tell
                '''
                subprocess.run(["osascript", "-e", script], timeout=10)
            else:
                subprocess.run(["wmctrl", "-r", title, "-e", f"0,{x},{y},{width},{height}"], timeout=10)
            return f"已调整窗口 {title}: {width}x{height} at ({x},{y})"

        elif action == "close":
            if not title:
                return "错误: close 需要 title 参数"
            if system == "Darwin":
                script = f'''
                    tell application "{title}"
                        close front window
                    end tell
                '''
                subprocess.run(["osascript", "-e", script], timeout=10)
            else:
                subprocess.run(["wmctrl", "-c", title], timeout=10)
            return f"已关闭窗口: {title}"

        else:
            return f"未知操作: {action}。支持: list, find, activate, minimize, maximize, resize, close"
    except FileNotFoundError:
        hint = "wmctrl (apt install wmctrl)" if system != "Darwin" else "osascript"
        return f"窗口管理需要 {hint}"
    except Exception as e:
        return f"窗口操作失败: {e}"

# ── Screen OCR ───────────────────────────────────────────────

async def _screen_ocr(
    region: str = "",
    language: str = "eng+chi_sim",
    **kwargs,
) -> str:
    """截屏 + OCR 文字识别."""
    try:
        from PIL import ImageGrab
    except ImportError:
        raise ImportError("Pillow 未安装。请运行: pip install 'xjd-agent[computer-use]'")
    try:
        import pytesseract
    except ImportError:
        raise ImportError("pytesseract 未安装。请运行: pip install 'xjd-agent[computer-use]'\n还需安装 tesseract-ocr 系统包")

    try:
        # 截屏
        if region:
            parts = [int(p.strip()) for p in region.split(",")]
            if len(parts) == 4:
                x, y, w, h = parts
                img = ImageGrab.grab(bbox=(x, y, x + w, y + h))
            else:
                return "错误: region 格式应为 x,y,w,h"
        else:
            img = ImageGrab.grab()

        # 保存截图
        path = str(workspace_tmp(".png", "ocr_"))
        img.save(path)

        # OCR
        text = pytesseract.image_to_string(img, lang=language)
        text = text.strip()

        if not text:
            return f"OCR 未识别到文字。截图已保存: {path}"

        if len(text) > 20000:
            text = text[:20000] + "\n... (截断)"

        return f"识别结果:\n{text}\n\n截图: {path}"
    except Exception as e:
        return f"OCR 失败: {e}"

# ── Registration ─────────────────────────────────────────────

def register_computer_use_tools(registry) -> None:
    """注册桌面控制工具到 ToolRegistry."""

    registry.register(
        name="mouse_control",
        description=(
            "鼠标控制。支持操作:\n"
            "- move: 移动到坐标 (x, y)\n"
            "- click: 点击 (支持 left/right/middle)\n"
            "- double_click: 双击\n"
            "- right_click: 右键点击\n"
            "- drag: 拖拽到目标位置\n"
            "- scroll: 滚动\n"
            "- position: 获取当前鼠标位置"
        ),
        parameters={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "操作类型",
                    "enum": ["move", "click", "double_click", "right_click", "drag", "scroll", "position"],
                },
                "x": {"type": "integer", "description": "X 坐标", "default": 0},
                "y": {"type": "integer", "description": "Y 坐标", "default": 0},
                "button": {"type": "string", "description": "鼠标按钮", "default": "left", "enum": ["left", "right", "middle"]},
                "duration": {"type": "number", "description": "移动持续时间(秒)", "default": 0.5},
                "clicks": {"type": "integer", "description": "点击次数", "default": 1},
                "scroll_amount": {"type": "integer", "description": "滚动格数 (正=上, 负=下)", "default": 3},
            },
            "required": ["action"],
        },
        handler=_mouse_control,
        category="computer-use",
        requires_approval=True,
    )

    registry.register(
        name="keyboard_control",
        description=(
            "键盘控制。支持操作:\n"
            "- press: 按单个键 (如 enter, tab, escape)\n"
            "- hotkey: 组合键 (如 'ctrl+c', 'cmd+shift+s')\n"
            "- type: 输入文本\n"
            "- hold: 按住修饰键 + 按键"
        ),
        parameters={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "操作类型",
                    "enum": ["press", "hotkey", "type", "hold"],
                },
                "key": {"type": "string", "description": "按键名 (press/hold 时使用)"},
                "text": {"type": "string", "description": "输入文本 (type 时使用) 或目标键 (hold 时使用)"},
                "hotkey": {"type": "string", "description": "组合键 (如 'ctrl+c')"},
                "interval": {"type": "number", "description": "按键间隔(秒)", "default": 0.05},
            },
            "required": ["action"],
        },
        handler=_keyboard_control,
        category="computer-use",
        requires_approval=True,
    )

    registry.register(
        name="window_manager",
        description=(
            "窗口管理。支持操作:\n"
            "- list: 列出所有可见窗口\n"
            "- find: 按标题搜索窗口\n"
            "- activate: 激活/聚焦窗口\n"
            "- minimize: 最小化窗口\n"
            "- maximize: 最大化窗口\n"
            "- resize: 调整窗口大小和位置\n"
            "- close: 关闭窗口"
        ),
        parameters={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "操作类型",
                    "enum": ["list", "find", "activate", "minimize", "maximize", "resize", "close"],
                },
                "title": {"type": "string", "description": "窗口标题或应用名"},
                "x": {"type": "integer", "description": "窗口 X 位置 (resize)", "default": 0},
                "y": {"type": "integer", "description": "窗口 Y 位置 (resize)", "default": 0},
                "width": {"type": "integer", "description": "窗口宽度 (resize)"},
                "height": {"type": "integer", "description": "窗口高度 (resize)"},
            },
            "required": ["action"],
        },
        handler=_window_manager,
        category="computer-use",
        requires_approval=True,
    )

    registry.register(
        name="screen_ocr",
        description="截取屏幕并用 OCR 识别文字。可指定区域 (x,y,w,h) 或全屏。支持中英文。",
        parameters={
            "type": "object",
            "properties": {
                "region": {"type": "string", "description": "截图区域 (x,y,w,h) 或留空截全屏"},
                "language": {"type": "string", "description": "OCR 语言 (默认 eng+chi_sim)", "default": "eng+chi_sim"},
            },
            "required": [],
        },
        handler=_screen_ocr,
        category="computer-use",
        requires_approval=True,
    )
