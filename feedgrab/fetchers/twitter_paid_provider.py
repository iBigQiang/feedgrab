"""Select and adapt paid X/Twitter read providers."""

from dataclasses import dataclass
from types import ModuleType

from feedgrab.config import (
    twitterapi_io_key,
    x_api_provider,
    xquik_api_key,
)


@dataclass(frozen=True)
class PaidXProvider:
    """Provider metadata plus a uniform batch-fetch interface."""

    provider_id: str
    label: str
    cache_prefix: str
    module: ModuleType

    def search_tweets(
        self,
        screen_name: str,
        query_type: str = "Latest",
        cursor: str = "",
        max_id: int | None = None,
        since_date: str = "",
        until_date: str = "",
    ) -> dict | None:
        """Search one account using provider-native pagination and filters."""
        query = f"from:{screen_name}"
        if self.provider_id == "xquik":
            return self.module.search_tweets(
                query,
                query_type=query_type,
                cursor=cursor,
                since_date=since_date,
                until_date=until_date,
            )
        if max_id is not None:
            query += f" max_id:{max_id}"
        return self.module.search_tweets(query, query_type=query_type, cursor=cursor)

    def get_user_last_tweets(
        self,
        user_name: str,
        cursor: str = "",
        include_replies: bool = False,
        since_date: str = "",
    ) -> dict | None:
        """Fetch one account timeline using the provider's public client."""
        if self.provider_id == "xquik":
            return self.module.get_user_last_tweets(
                user_name,
                cursor=cursor,
                include_replies=include_replies,
                since_date=since_date,
            )
        return self.module.get_user_last_tweets(
            user_name,
            cursor=cursor,
            include_replies=include_replies,
        )

    def parse_api_tweet(self, raw: dict) -> dict:
        """Map a provider response to feedgrab's internal tweet shape."""
        return self.module.parse_api_tweet(raw)


def configured_paid_provider_name() -> str:
    """Return the explicit provider or the first configured supplementary one."""
    explicit = x_api_provider()
    if explicit in ("api", "xquik"):
        return explicit
    if twitterapi_io_key():
        return "api"
    if xquik_api_key():
        return "xquik"
    return ""


def provider_key_name(provider_id: str) -> str:
    """Return the environment variable used by a provider."""
    return "XQUIK_API_KEY" if provider_id == "xquik" else "TWITTERAPI_IO_KEY"


def provider_has_credentials(provider_id: str) -> bool:
    """Check whether the selected provider has a configured key."""
    return bool(xquik_api_key() if provider_id == "xquik" else twitterapi_io_key())


def load_paid_provider(provider_id: str = "") -> PaidXProvider:
    """Load the selected provider lazily to preserve optional configuration."""
    selected = provider_id or configured_paid_provider_name()
    if selected == "xquik":
        from feedgrab.fetchers import xquik_api

        return PaidXProvider("xquik", "Xquik", "xquik", xquik_api)
    if selected == "api":
        from feedgrab.fetchers import twitter_api

        return PaidXProvider("api", "TwitterAPI.io", "api", twitter_api)
    raise RuntimeError("未配置付费 X/Twitter API provider")
