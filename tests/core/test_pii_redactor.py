"""测试 — PII 脱敏."""

from __future__ import annotations


class TestPIIRedactor:
    def test_detect_phone(self):
        from agent.core.pii_redactor import PIIRedactor

        r = PIIRedactor()
        matches = r.detect("我的手机号是 13812345678")
        assert len(matches) == 1
        assert matches[0].pii_type == "PHONE"

    def test_detect_email(self):
        from agent.core.pii_redactor import PIIRedactor

        r = PIIRedactor()
        matches = r.detect("邮箱: test@example.com")
        assert len(matches) == 1
        assert matches[0].pii_type == "EMAIL"

    def test_detect_id_card(self):
        from agent.core.pii_redactor import PIIRedactor

        r = PIIRedactor()
        matches = r.detect("身份证 110101199001011234")
        types = [m.pii_type for m in matches]
        assert "ID_CARD" in types

    def test_detect_api_key(self):
        from agent.core.pii_redactor import PIIRedactor

        r = PIIRedactor()
        matches = r.detect("key: sk-abcdefghijklmnopqrstuvwxyz1234")
        assert len(matches) == 1
        assert matches[0].pii_type == "API_KEY"

    def test_detect_password(self):
        from agent.core.pii_redactor import PIIRedactor

        r = PIIRedactor()
        matches = r.detect("password=MySecret123!")
        assert len(matches) == 1
        assert matches[0].pii_type == "PASSWORD"

    def test_redact(self):
        from agent.core.pii_redactor import PIIRedactor

        r = PIIRedactor()
        result = r.redact("手机 13812345678 邮箱 test@example.com")
        assert "[PHONE]" in result
        assert "[EMAIL]" in result
        assert "13812345678" not in result

    def test_redact_disabled(self):
        from agent.core.pii_redactor import PIIRedactor

        r = PIIRedactor(enabled=False)
        text = "手机 13812345678"
        assert r.redact(text) == text

    def test_has_pii(self):
        from agent.core.pii_redactor import PIIRedactor

        r = PIIRedactor()
        assert r.has_pii("手机 13812345678") is True
        assert r.has_pii("普通文本") is False

    def test_get_stats(self):
        from agent.core.pii_redactor import PIIRedactor

        r = PIIRedactor()
        stats = r.get_stats("手机 13812345678 邮箱 a@b.com 另一个 13900001111")
        assert stats["PHONE"] == 2
        assert stats["EMAIL"] == 1

    def test_custom_pattern(self):
        from agent.core.pii_redactor import PIIRedactor

        r = PIIRedactor(extra_patterns=[("CUSTOM", r"XJD-\d{6}")])
        matches = r.detect("编号 XJD-123456")
        types = [m.pii_type for m in matches]
        assert "CUSTOM" in types

    def test_no_false_positive(self):
        from agent.core.pii_redactor import PIIRedactor

        r = PIIRedactor()
        assert r.has_pii("这是一段普通的中文文本，没有任何敏感信息。") is False
