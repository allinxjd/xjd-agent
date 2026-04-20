"""conftest — pytest 共享 fixtures."""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture(scope="session")
def event_loop():
    """全局事件循环."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def tmp_dir():
    """临时目录."""
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


@pytest.fixture
def sample_messages():
    """示例消息列表."""
    from agent.providers.base import Message

    return [
        Message(role="system", content="你是一个AI助手。"),
        Message(role="user", content="你好，请介绍一下自己。"),
    ]


@pytest.fixture
def sample_tools():
    """示例工具定义."""
    from agent.providers.base import ToolDefinition

    return [
        ToolDefinition(
            name="test_tool",
            description="A test tool",
            parameters={
                "type": "object",
                "properties": {
                    "input": {"type": "string", "description": "Input text"},
                },
                "required": ["input"],
            },
        ),
    ]


@pytest.fixture
def mock_config(tmp_dir):
    """模拟配置."""
    os.environ["XJD_HOME"] = str(tmp_dir)

    config_content = """
model:
  primary:
    provider: "openai"
    model: "gpt-4o"
    api_key: "sk-test-key"
"""
    config_file = tmp_dir / "config.yaml"
    config_file.write_text(config_content)

    yield tmp_dir

    os.environ.pop("XJD_HOME", None)
