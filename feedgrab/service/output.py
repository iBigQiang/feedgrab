# -*- coding: utf-8 -*-
"""Output service wrappers for artifact metadata and storage utilities."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from feedgrab.schema import UnifiedContent
from feedgrab.service.models import redact_value
from feedgrab.utils.storage import save_to_markdown


@dataclass
class ArtifactInfo:
    path: str
    exists: bool
    kind: str = "file"
    content_type: str = "text/markdown"
    size: int = 0
    modified_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": redact_value(self.path),
            "exists": self.exists,
            "kind": self.kind,
            "content_type": self.content_type,
            "size": self.size,
            "modified_at": self.modified_at,
            "metadata": redact_value(dict(self.metadata)),
        }


@dataclass
class OpenPathPlan:
    path: str
    exists: bool
    action: str = "open_path"
    executed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "path": redact_value(self.path),
            "exists": self.exists,
            "executed": self.executed,
        }


class OutputService:
    """Persist content and expose safe artifact metadata."""

    def __init__(self, output_dir: str | Path | None = None):
        raw = output_dir if output_dir is not None else os.getenv("OUTPUT_DIR", "")
        self.output_dir = Path(raw) if raw else None

    def save_markdown(self, content: UnifiedContent, filepath: str = None):
        return save_to_markdown(content, filepath=filepath)

    def artifact_info(self, path: str | Path) -> ArtifactInfo:
        artifact_path = Path(path)
        if not artifact_path.exists():
            return ArtifactInfo(path=str(artifact_path), exists=False)
        stat = artifact_path.stat()
        modified = datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat()
        return ArtifactInfo(
            path=str(artifact_path),
            exists=True,
            kind="directory" if artifact_path.is_dir() else "file",
            content_type=self._content_type(artifact_path),
            size=stat.st_size,
            modified_at=modified,
        )

    def list_artifacts(self, platform: str | None = None) -> list[ArtifactInfo]:
        if self.output_dir is None:
            return []
        root = self.output_dir / platform if platform else self.output_dir
        if not root.exists():
            return []
        return [
            self.artifact_info(path)
            for path in sorted(root.rglob("*"))
            if path.is_file()
        ]

    def open_path(self, path: str | Path) -> OpenPathPlan:
        artifact_path = Path(path)
        return OpenPathPlan(path=str(artifact_path), exists=artifact_path.exists())

    @staticmethod
    def _content_type(path: Path) -> str:
        suffix = path.suffix.lower()
        if suffix == ".md":
            return "text/markdown"
        if suffix == ".csv":
            return "text/csv"
        if suffix in {".jpg", ".jpeg"}:
            return "image/jpeg"
        if suffix == ".png":
            return "image/png"
        return "application/octet-stream"
