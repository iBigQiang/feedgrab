# -*- coding: utf-8 -*-
"""
Tweet API driver — selects backend (TwitterAPI.io or GetXAPI) at call time.

Both backends expose the same three callables (search_tweets,
get_user_last_tweets, parse_api_tweet) with identical signatures and
return shapes, so downstream code in twitter_api_user_tweets.py can stay
backend-agnostic.

Selection is governed by X_API_PROVIDER:
    'api'     → feedgrab.fetchers.twitter_api  (TwitterAPI.io, default paid)
    'getxapi' → feedgrab.fetchers.getxapi_api  (GetXAPI)
    other     → defaults to twitter_api (back-compat with prior callers)

Resolved per-call so a config change does not require process restart.
"""

from typing import Optional

from feedgrab.config import x_api_provider, twitterapi_io_key, getxapi_key


def _backend():
    """Return the currently selected backend module.

    Resolution order:
        1. X_API_PROVIDER='getxapi' → GetXAPI
        2. X_API_PROVIDER='api'     → TwitterAPI.io
        3. Otherwise (e.g. 'graphql' supplementary path): pick whichever
           key is configured; prefer TwitterAPI.io if both are set
           (preserves prior single-backend behavior).
    """
    provider = x_api_provider()
    if provider == "getxapi":
        from feedgrab.fetchers import getxapi_api
        return getxapi_api
    if provider == "api":
        from feedgrab.fetchers import twitter_api
        return twitter_api
    if twitterapi_io_key():
        from feedgrab.fetchers import twitter_api
        return twitter_api
    if getxapi_key():
        from feedgrab.fetchers import getxapi_api
        return getxapi_api
    from feedgrab.fetchers import twitter_api
    return twitter_api


def search_tweets(
    query: str,
    query_type: str = "Latest",
    cursor: str = "",
) -> Optional[dict]:
    """Dispatch Advanced Search to the configured backend."""
    return _backend().search_tweets(query, query_type=query_type, cursor=cursor)


def get_user_last_tweets(
    user_name: str,
    cursor: str = "",
    include_replies: bool = False,
) -> Optional[dict]:
    """Dispatch User Last Tweets to the configured backend."""
    return _backend().get_user_last_tweets(
        user_name, cursor=cursor, include_replies=include_replies
    )


def parse_api_tweet(raw: dict) -> dict:
    """Dispatch tweet parsing to the configured backend."""
    return _backend().parse_api_tweet(raw)
