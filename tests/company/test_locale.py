"""Tests for CompanyLocale."""

from __future__ import annotations

import pytest

from agent.company.locale import CompanyLocale


@pytest.fixture(autouse=True)
def clear_locale_cache():
    CompanyLocale.clear_cache()
    yield
    CompanyLocale.clear_cache()


class TestLocaleLoad:
    def test_load_zh_cn(self):
        locale = CompanyLocale.load("zh-CN")
        assert locale.get("address.boss") == "老板"

    def test_load_en_us(self):
        locale = CompanyLocale.load("en-US")
        assert locale.get("address.boss") == "Boss"

    def test_fallback_to_zh_cn(self):
        locale = CompanyLocale.load("en-US")
        zh = CompanyLocale.load("zh-CN")
        zh_val = zh.get("keywords.task_triggers")
        en_val = locale.get("keywords.task_triggers")
        assert en_val is not None
        assert isinstance(en_val, list)

    def test_unknown_locale_falls_back(self):
        locale = CompanyLocale.load("fr-FR")
        assert locale.get("address.boss") == "老板"


class TestLocaleGet:
    def test_dot_notation(self):
        locale = CompanyLocale.load("zh-CN")
        assert locale.get("roles.pm.description") is not None

    def test_missing_key_returns_default(self):
        locale = CompanyLocale.load("zh-CN")
        assert locale.get("nonexistent.key", "fallback") == "fallback"

    def test_getitem(self):
        locale = CompanyLocale.load("zh-CN")
        assert locale["address.boss"] == "老板"

    def test_getitem_missing_raises(self):
        locale = CompanyLocale.load("zh-CN")
        with pytest.raises(KeyError):
            locale["nonexistent.key"]


class TestLocaleFormat:
    def test_format_with_kwargs(self):
        locale = CompanyLocale.load("zh-CN")
        result = locale.format("messages.pipeline_timeout", max_minutes=30)
        assert "30" in result
        assert "分钟" in result

    def test_format_missing_key(self):
        locale = CompanyLocale.load("zh-CN")
        result = locale.format("nonexistent.key")
        assert result == ""


class TestLocaleKeywords:
    def test_task_triggers_is_list(self):
        locale = CompanyLocale.load("zh-CN")
        triggers = locale.get("keywords.task_triggers")
        assert isinstance(triggers, list)
        assert len(triggers) > 10

    def test_strong_task_triggers(self):
        locale = CompanyLocale.load("zh-CN")
        strong = locale.get("keywords.strong_task_triggers")
        assert isinstance(strong, list)
        assert len(strong) > 10
        assert "开发一个" in strong

    def test_weak_task_triggers(self):
        locale = CompanyLocale.load("zh-CN")
        weak = locale.get("keywords.weak_task_triggers")
        assert isinstance(weak, list)
        assert len(weak) > 5
        assert "加入" in weak

    def test_anti_keywords(self):
        locale = CompanyLocale.load("zh-CN")
        anti = locale.get("keywords.task_anti_keywords")
        assert isinstance(anti, list)
        assert "加油" in anti
        assert "开发者大会" in anti

    def test_quick_task_is_dict(self):
        locale = CompanyLocale.load("zh-CN")
        qt = locale.get("keywords.quick_task")
        assert isinstance(qt, dict)
        assert "Developer" in qt
        assert "DevOps" in qt
        assert "QA" in qt

    def test_filenames(self):
        locale = CompanyLocale.load("zh-CN")
        assert locale.get("filenames.WritePRD") is not None
        assert locale.get("filenames.WriteDesign") is not None

    def test_en_us_has_all_keys(self):
        zh = CompanyLocale.load("zh-CN")
        en = CompanyLocale.load("en-US")
        for key in ["address.boss", "roles.pm.description", "keywords.task_triggers",
                     "keywords.strong_task_triggers", "keywords.weak_task_triggers",
                     "keywords.task_anti_keywords",
                     "keywords.quick_task", "filenames.WritePRD", "messages.pipeline_timeout"]:
            assert en.get(key) is not None, f"en-US missing key: {key}"
