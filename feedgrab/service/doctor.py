# -*- coding: utf-8 -*-
"""Structured diagnostic service primitives."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

from feedgrab.service.models import DiagnosticResult


@dataclass
class DiagnosticSummary:
    checks: list[DiagnosticResult] = field(default_factory=list)

    @property
    def status(self) -> str:
        if any(check.status == "error" for check in self.checks):
            return "error"
        if any(check.status == "warning" for check in self.checks):
            return "warning"
        return "ok"

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "checks": [check.to_dict() for check in self.checks],
        }


class DoctorService:
    """Collect structured diagnostic results for non-CLI clients."""

    def __init__(self, output_dir: str | Path | None = None):
        self.output_dir = Path(output_dir) if output_dir is not None else None

    def check_import(self, module_name: str, label: str = "") -> DiagnosticResult:
        name = label or module_name
        try:
            __import__(module_name)
            return DiagnosticResult(name=name, status="ok", message="available")
        except ImportError as exc:
            return DiagnosticResult(name=name, status="warning", message=str(exc))

    def summary(self, optional_modules: list[str] | None = None) -> DiagnosticSummary:
        checks = [
            DiagnosticResult(
                name="python",
                status="ok",
                message=sys.version.split()[0],
                details={"executable": sys.executable},
            )
        ]
        for module_name in optional_modules or []:
            checks.append(self.check_import(module_name, label=f"import:{module_name}"))
        if self.output_dir is not None:
            checks.append(self.check_output_dir(self.output_dir))
        return DiagnosticSummary(checks=checks)

    def check_output_dir(self, output_dir: str | Path) -> DiagnosticResult:
        path = Path(output_dir)
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe = path / ".feedgrab_doctor_write_test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            return DiagnosticResult(
                name="output_dir",
                status="ok",
                message="writable",
                details={"path": str(path)},
            )
        except Exception as exc:
            return DiagnosticResult(
                name="output_dir",
                status="error",
                message=str(exc),
                details={"path": str(path)},
            )
