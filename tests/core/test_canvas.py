"""测试 — Canvas / A2UI."""

from __future__ import annotations

import pytest

from agent.core.canvas import CanvasArtifact, CanvasManager, CanvasType


class TestCanvasType:
    def test_values(self):
        assert CanvasType.HTML == "html"
        assert CanvasType.MERMAID == "mermaid"
        assert CanvasType.CHART == "chart"
        assert CanvasType.REACT == "react"
        assert CanvasType.MARKDOWN == "markdown"


class TestCanvasManager:
    def test_create(self):
        mgr = CanvasManager()
        a = mgr.create(CanvasType.HTML, "Test", "<h1>Hello</h1>")
        assert a.artifact_id
        assert a.title == "Test"
        assert a.canvas_type == CanvasType.HTML
        assert a.created_at > 0

    def test_get(self):
        mgr = CanvasManager()
        a = mgr.create(CanvasType.HTML, "Test", "content")
        assert mgr.get(a.artifact_id) is a
        assert mgr.get("nonexistent") is None

    def test_list_all(self):
        mgr = CanvasManager()
        mgr.create(CanvasType.HTML, "A", "a")
        mgr.create(CanvasType.MERMAID, "B", "b")
        assert len(mgr.list_all()) == 2

    def test_update(self):
        mgr = CanvasManager()
        a = mgr.create(CanvasType.HTML, "Test", "old")
        updated = mgr.update(a.artifact_id, "new")
        assert updated.content == "new"
        assert updated.updated_at >= a.created_at

    def test_update_nonexistent(self):
        mgr = CanvasManager()
        assert mgr.update("nope", "content") is None

    def test_delete(self):
        mgr = CanvasManager()
        a = mgr.create(CanvasType.HTML, "Test", "content")
        assert mgr.delete(a.artifact_id) is True
        assert mgr.get(a.artifact_id) is None
        assert mgr.delete("nope") is False

    def test_listener(self):
        mgr = CanvasManager()
        events = []
        mgr.on_change(lambda event, artifact: events.append(event))
        mgr.create(CanvasType.HTML, "Test", "content")
        assert "create" in events

    def test_listener_on_update(self):
        mgr = CanvasManager()
        events = []
        mgr.on_change(lambda event, artifact: events.append(event))
        a = mgr.create(CanvasType.HTML, "Test", "old")
        mgr.update(a.artifact_id, "new")
        assert events == ["create", "update"]

    def test_listener_on_delete(self):
        mgr = CanvasManager()
        events = []
        mgr.on_change(lambda event, artifact: events.append(event))
        a = mgr.create(CanvasType.HTML, "Test", "content")
        mgr.delete(a.artifact_id)
        assert "delete" in events


class TestRenderHTML:
    def test_render_html(self):
        mgr = CanvasManager()
        a = mgr.create(CanvasType.HTML, "Page", "<p>Hello</p>")
        html = mgr.render_html(a.artifact_id)
        assert "<p>Hello</p>" in html
        assert "<!DOCTYPE html>" in html

    def test_render_mermaid(self):
        mgr = CanvasManager()
        a = mgr.create(CanvasType.MERMAID, "Flow", "graph TD; A-->B")
        html = mgr.render_html(a.artifact_id)
        assert "mermaid" in html
        assert "graph TD; A-->B" in html

    def test_render_chart(self):
        mgr = CanvasManager()
        a = mgr.create(CanvasType.CHART, "Chart", '{"type":"bar","data":{}}')
        html = mgr.render_html(a.artifact_id)
        assert "chart.js" in html

    def test_render_react(self):
        mgr = CanvasManager()
        a = mgr.create(CanvasType.REACT, "App", "ReactDOM.render(<h1>Hi</h1>,document.getElementById('root'))")
        html = mgr.render_html(a.artifact_id)
        assert "react" in html.lower()
        assert "babel" in html.lower()

    def test_render_markdown(self):
        mgr = CanvasManager()
        a = mgr.create(CanvasType.MARKDOWN, "Doc", "# Hello")
        html = mgr.render_html(a.artifact_id)
        assert "marked" in html

    def test_render_nonexistent(self):
        mgr = CanvasManager()
        assert mgr.render_html("nope") is None
