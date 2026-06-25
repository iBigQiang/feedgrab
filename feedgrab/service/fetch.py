# -*- coding: utf-8 -*-
"""Fetch service wrapping the existing UniversalReader contract."""

from __future__ import annotations

import asyncio
from typing import Iterable

from loguru import logger

from feedgrab.reader import UniversalReader
from feedgrab.schema import UnifiedContent
from feedgrab.service.models import Artifact, FetchRequest, FetchResult, ServiceError, redact_value


class FetchService:
    """Structured fetch API shared by CLI, MCP, and future GUI workers."""

    def __init__(self, reader: UniversalReader | None = None, inbox=None):
        self.reader = reader or UniversalReader()
        self.inbox = inbox

    async def fetch_url(self, request: str | FetchRequest) -> FetchResult:
        req = request if isinstance(request, FetchRequest) else FetchRequest(url=request)
        platform = self.detect_platform(req.url)
        try:
            content = await self.reader.read(req.url)
        except ServiceError:
            raise
        except Exception as exc:
            raise ServiceError(
                str(exc),
                code="fetch_error",
                recoverable=True,
                details={"url": req.url, "platform": platform},
            ) from exc

        self._add_to_inbox(content)
        return FetchResult(
            request=req,
            content=content,
            artifacts=self._artifacts_for(content),
            platform=platform or content.source_type.value,
        )

    async def fetch_urls(self, urls: Iterable[str]) -> list[FetchResult]:
        url_list = list(urls)
        tasks = [self.fetch_url(url) for url in url_list]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        fetched: list[FetchResult] = []
        for url, result in zip(url_list, results):
            if isinstance(result, Exception):
                platform = self.detect_platform(url)
                error = result if isinstance(result, ServiceError) else ServiceError(
                    str(result),
                    code="fetch_error",
                    recoverable=True,
                    details={"url": url, "platform": platform},
                )
                logger.error(f"Batch failed for {redact_value(url)}: {error.message}")
                fetched.append(
                    FetchResult(
                        request=FetchRequest(url=url),
                        content=None,
                        artifacts=[],
                        platform=platform,
                        success=False,
                        error=error.to_dict(),
                    )
                )
                continue
            fetched.append(result)
        return fetched

    def detect_platform(self, url: str) -> str:
        normalized = url
        if not normalized.startswith(("http://", "https://")):
            normalized = f"https://{normalized}"
        return self.reader._detect_platform(normalized)

    def list_inbox(self) -> list[UnifiedContent]:
        if self.inbox is None:
            return []
        return list(getattr(self.inbox, "items", []))

    def _add_to_inbox(self, content: UnifiedContent) -> None:
        if self.inbox is None:
            return
        add = getattr(self.inbox, "add", None)
        if callable(add):
            add(content)

    @staticmethod
    def _artifacts_for(content: UnifiedContent) -> list[Artifact]:
        saved_path = getattr(content, "_feedgrab_saved_path", None)
        if not saved_path:
            return []
        return [Artifact(kind="markdown", path=str(saved_path))]
