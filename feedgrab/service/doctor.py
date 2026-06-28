# -*- coding: utf-8 -*-
"""Structured diagnostic service primitives."""

from __future__ import annotations

import sys
import os
from dataclasses import dataclass, field
from pathlib import Path

from feedgrab.config import get_data_dir
from feedgrab.service.models import DiagnosticResult
from feedgrab.service.proxy import get_proxy_url, is_proxy_enabled, redact_proxy_url
from feedgrab.utils import http_client


_PROXY_TEST_TARGETS = (
    "https://raw.githubusercontent.com/github/gitignore/main/Python.gitignore",
    "https://x.com",
    "https://www.youtube.com",
)


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
            return DiagnosticResult(name=name, status="ok", message="可用")
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
        output_dir = self.output_dir or Path(os.getenv("OUTPUT_DIR", "./output"))
        checks.append(self.check_directory("output_dir", output_dir, "输出目录"))
        checks.append(self.check_directory("data_dir", get_data_dir(), "数据/登录态目录"))
        checks.append(self.check_proxy_connectivity())
        return DiagnosticSummary(checks=checks)

    def check_output_dir(self, output_dir: str | Path) -> DiagnosticResult:
        return self.check_directory("output_dir", output_dir, "输出目录")

    def check_directory(self, name: str, directory: str | Path, label: str) -> DiagnosticResult:
        path = Path(directory)
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe = path / ".feedgrab_doctor_write_test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            return DiagnosticResult(
                name=name,
                status="ok",
                message="可写",
                details={"path": str(path), "label": label},
            )
        except Exception as exc:
            return DiagnosticResult(
                name=name,
                status="error",
                message=str(exc),
                details={"path": str(path), "label": label},
            )

    def check_proxy_connectivity(self) -> DiagnosticResult:
        if not is_proxy_enabled():
            return DiagnosticResult(
                name="proxy_connectivity",
                status="warning",
                message="代理未启用",
                details={"enabled": False},
            )

        proxy_url = get_proxy_url()
        if not proxy_url:
            return DiagnosticResult(
                name="proxy_connectivity",
                status="warning",
                message="代理未配置",
                details={"enabled": True},
            )

        last_error = ""
        for target in _PROXY_TEST_TARGETS:
            try:
                response = http_client.get(target, timeout=6)
                status_code = getattr(response, "status_code", 0)
                if 200 <= int(status_code) < 500:
                    return DiagnosticResult(
                        name="proxy_connectivity",
                        status="ok",
                        message="代理连通",
                        details={"target": target, "status_code": status_code, "proxy": redact_proxy_url(proxy_url)},
                    )
                last_error = f"HTTP {status_code}"
            except Exception as exc:
                text = str(exc)
                lowered = text.lower()
                if "timeout" in lowered or "timed out" in lowered:
                    last_error = "网络超时"
                elif "proxy" in lowered or "connect" in lowered or "connection" in lowered:
                    last_error = "代理不可达"
                else:
                    last_error = text

        message = last_error if last_error in {"网络超时", "代理不可达"} else f"代理检测失败：{last_error}"
        return DiagnosticResult(
            name="proxy_connectivity",
            status="error",
            message=message,
            details={"enabled": True, "proxy": redact_proxy_url(proxy_url)},
        )
