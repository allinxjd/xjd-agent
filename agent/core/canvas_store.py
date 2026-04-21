"""Canvas 持久化存储 — 文件系统 + 版本历史.

存储结构:
    ~/.xjd-agent/canvas/{artifact_id}/
        manifest.json   # 元数据
        v1.html         # 版本内容
        v2.html
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Optional

from .canvas import CanvasArtifact, CanvasType

logger = logging.getLogger(__name__)

_DEFAULT_BASE = Path.home() / ".xjd-agent" / "canvas"


class CanvasStore:
    """文件系统 Canvas 持久化."""

    def __init__(self, base_dir: Optional[Path] = None) -> None:
        self._base = base_dir or _DEFAULT_BASE
        self._base.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _safe_dir(self, artifact_id: str) -> Path:
        import re
        if not re.match(r'^[a-zA-Z0-9_-]+$', artifact_id):
            raise ValueError(f"Invalid artifact_id: {artifact_id}")
        resolved = (self._base / artifact_id).resolve()
        if not str(resolved).startswith(str(self._base.resolve())):
            raise ValueError(f"Path traversal detected: {artifact_id}")
        return resolved

    def save(self, artifact: CanvasArtifact) -> str:
        with self._lock:
            return self._save_impl(artifact)

    def _save_impl(self, artifact: CanvasArtifact) -> str:
        art_dir = self._safe_dir(artifact.artifact_id)
        art_dir.mkdir(parents=True, exist_ok=True)

        manifest_path = art_dir / "manifest.json"
        manifest = self._load_manifest(manifest_path)

        version = manifest.get("version", 0) + 1
        ext = self._ext_for_type(artifact.canvas_type)
        version_file = art_dir / f"v{version}{ext}"
        version_file.write_text(artifact.content, encoding="utf-8")

        manifest.update({
            "artifact_id": artifact.artifact_id,
            "type": artifact.canvas_type.value,
            "title": artifact.title,
            "version": version,
            "created_at": manifest.get("created_at", artifact.created_at or time.time()),
            "updated_at": time.time(),
            "metadata": artifact.metadata,
            "versions": manifest.get("versions", []) + [{
                "version": version,
                "timestamp": time.time(),
                "file": version_file.name,
            }],
        })
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

        current = art_dir / f"current{ext}"
        if current.is_symlink() or current.exists():
            current.unlink()
        current.symlink_to(version_file.name)

        return artifact.artifact_id

    def load(self, artifact_id: str, version: Optional[int] = None) -> Optional[CanvasArtifact]:
        try:
            art_dir = self._safe_dir(artifact_id)
        except ValueError:
            return None
        manifest_path = art_dir / "manifest.json"
        if not manifest_path.exists():
            return None

        manifest = self._load_manifest(manifest_path)
        canvas_type = CanvasType(manifest.get("type", "html"))
        ext = self._ext_for_type(canvas_type)

        if version:
            content_file = art_dir / f"v{version}{ext}"
        else:
            content_file = art_dir / f"current{ext}"

        if not content_file.exists():
            return None

        return CanvasArtifact(
            artifact_id=artifact_id,
            canvas_type=canvas_type,
            title=manifest.get("title", ""),
            content=content_file.read_text(encoding="utf-8"),
            metadata=manifest.get("metadata", {}),
            created_at=manifest.get("created_at", 0),
            updated_at=manifest.get("updated_at", 0),
        )

    def list_artifacts(self) -> list[dict[str, Any]]:
        results = []
        if not self._base.exists():
            return results
        for d in sorted(self._base.iterdir()):
            if not d.is_dir():
                continue
            m = d / "manifest.json"
            if m.exists():
                try:
                    data = json.loads(m.read_text(encoding="utf-8"))
                    results.append({
                        "artifact_id": data.get("artifact_id", d.name),
                        "type": data.get("type"),
                        "title": data.get("title"),
                        "version": data.get("version", 1),
                        "updated_at": data.get("updated_at"),
                    })
                except Exception:
                    logger.debug("Failed to read manifest for %s", d.name, exc_info=True)
        return results

    def get_versions(self, artifact_id: str) -> list[dict]:
        try:
            art_dir = self._safe_dir(artifact_id)
        except ValueError:
            return []
        manifest_path = art_dir / "manifest.json"
        if not manifest_path.exists():
            return []
        manifest = self._load_manifest(manifest_path)
        return manifest.get("versions", [])

    def delete(self, artifact_id: str) -> bool:
        import shutil
        try:
            art_dir = self._safe_dir(artifact_id)
        except ValueError:
            return False
        if art_dir.exists():
            shutil.rmtree(art_dir)
            return True
        return False

    @staticmethod
    def _load_manifest(path: Path) -> dict:
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {}

    @staticmethod
    def _ext_for_type(canvas_type: CanvasType) -> str:
        return {
            CanvasType.HTML: ".html",
            CanvasType.MARKDOWN: ".md",
            CanvasType.MERMAID: ".mmd",
            CanvasType.CHART: ".json",
            CanvasType.REACT: ".jsx",
        }.get(canvas_type, ".html")