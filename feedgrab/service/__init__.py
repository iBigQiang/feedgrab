# -*- coding: utf-8 -*-
"""Structured service API for feedgrab clients.

The first-stage service layer is intentionally thin: it wraps the existing
reader/storage/login/config behavior so CLI and MCP can share one backend
contract without changing platform fetcher semantics.
"""

from feedgrab.service.models import (
    Artifact,
    DiagnosticResult,
    FetchRequest,
    FetchResult,
    ProgressEvent,
    ServiceError,
)

_LAZY_EXPORTS = {
    "FetchService": ("feedgrab.service.fetch", "FetchService"),
    "JobService": ("feedgrab.service.jobs", "JobService"),
    "SettingsService": ("feedgrab.service.settings", "SettingsService"),
    "DoctorService": ("feedgrab.service.doctor", "DoctorService"),
    "LoginService": ("feedgrab.service.login", "LoginService"),
    "OutputService": ("feedgrab.service.output", "OutputService"),
}

__all__ = [
    "Artifact",
    "DiagnosticResult",
    "FetchRequest",
    "FetchResult",
    "FetchService",
    "JobService",
    "SettingsService",
    "DoctorService",
    "LoginService",
    "OutputService",
    "ProgressEvent",
    "ServiceError",
]


def __getattr__(name: str):
    lazy_export = _LAZY_EXPORTS.get(name)
    if lazy_export is None:
        raise AttributeError(f"module 'feedgrab.service' has no attribute {name!r}")
    module_name, attr_name = lazy_export
    from importlib import import_module

    value = getattr(import_module(module_name), attr_name)
    globals()[name] = value
    return value
