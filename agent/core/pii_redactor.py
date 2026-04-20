"""PII 脱敏 — 检测并遮蔽个人敏感信息.

用法:
    redactor = PIIRedactor()
    clean = redactor.redact("我的手机号是 13812345678")
    # → "我的手机号是 [PHONE]"
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

@dataclass
class PIIMatch:
    """检测到的 PII."""

    pii_type: str
    original: str
    start: int
    end: int

# PII 检测规则
_PII_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # 中国手机号
    ("PHONE", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    # 中国身份证号
    ("ID_CARD", re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")),
    # 邮箱
    ("EMAIL", re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")),
    # 银行卡号 (16-19 位)
    ("BANK_CARD", re.compile(r"(?<!\d)\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4,7}(?!\d)")),
    # IPv4 地址
    ("IP_ADDRESS", re.compile(r"(?<!\d)\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(?!\d)")),
    # API Key 模式 (sk-xxx, ghp_xxx, xoxb-xxx 等)
    ("API_KEY", re.compile(r"(?:sk-|ghp_|gsk_|xoxb-|xapp-|Bearer\s+)[a-zA-Z0-9_-]{20,}")),
    # 密码模式 (password=xxx, pwd:xxx)
    ("PASSWORD", re.compile(r"(?:password|passwd|pwd|secret|token)\s*[=:]\s*\S+", re.IGNORECASE)),
]

class PIIRedactor:
    """PII 脱敏器."""

    def __init__(
        self,
        enabled: bool = True,
        extra_patterns: list[tuple[str, str]] | None = None,
    ) -> None:
        self.enabled = enabled
        self._patterns = list(_PII_PATTERNS)
        if extra_patterns:
            for name, pattern in extra_patterns:
                self._patterns.append((name, re.compile(pattern)))

    def detect(self, text: str) -> list[PIIMatch]:
        """检测文本中的 PII."""
        if not self.enabled:
            return []

        matches: list[PIIMatch] = []
        for pii_type, pattern in self._patterns:
            for m in pattern.finditer(text):
                matches.append(PIIMatch(
                    pii_type=pii_type,
                    original=m.group(),
                    start=m.start(),
                    end=m.end(),
                ))
        return matches

    def redact(self, text: str, replacement_format: str = "[{type}]") -> str:
        """脱敏文本中的 PII."""
        if not self.enabled:
            return text

        result = text
        # 从后往前替换，避免偏移
        matches = sorted(self.detect(text), key=lambda m: m.start, reverse=True)
        for match in matches:
            placeholder = replacement_format.format(type=match.pii_type)
            result = result[:match.start] + placeholder + result[match.end:]
        return result

    def has_pii(self, text: str) -> bool:
        """检查文本是否包含 PII."""
        return len(self.detect(text)) > 0

    def get_stats(self, text: str) -> dict[str, int]:
        """统计各类 PII 数量."""
        matches = self.detect(text)
        stats: dict[str, int] = {}
        for m in matches:
            stats[m.pii_type] = stats.get(m.pii_type, 0) + 1
        return stats
