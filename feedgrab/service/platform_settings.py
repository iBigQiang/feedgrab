# -*- coding: utf-8 -*-
"""Desktop-facing platform capability and settings metadata."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class PlatformLoginCapability:
    platform: str
    display_name: str
    login: str = "required"
    session_prefixes: tuple[str, ...] = ()
    description: str = ""

    @property
    def login_required(self) -> bool:
        return self.login == "required"

    def to_dict(self) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "display_name": self.display_name,
            "login": self.login,
            "login_required": self.login_required,
            "session_prefixes": list(self.session_prefixes or (self.platform,)),
            "description": self.description,
        }


@dataclass(frozen=True)
class PlatformSettingField:
    name: str
    value_type: str
    label: str
    platform: str
    default: Any = ""
    secret: bool = False
    description: str = ""
    placeholder: str = ""
    options: tuple[Any, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "name": self.name,
            "type": self.value_type,
            "label": self.label,
            "platform": self.platform,
            "default": self.default,
            "secret": self.secret,
            "description": self.description,
        }
        if self.placeholder:
            payload["placeholder"] = self.placeholder
        if self.options:
            payload["options"] = list(self.options)
        return payload


@dataclass(frozen=True)
class PlatformSettingsGroup:
    id: str
    name: str
    fields: tuple[PlatformSettingField, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "fields": [field.to_dict() for field in self.fields],
        }


@dataclass(frozen=True)
class PlatformSettingsSchema:
    platforms: tuple[PlatformSettingsGroup, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {"platforms": [platform.to_dict() for platform in self.platforms]}

    def fields(self) -> list[PlatformSettingField]:
        return [field for platform in self.platforms for field in platform.fields]

    def field_map(self) -> dict[str, PlatformSettingField]:
        return {field.name: field for field in self.fields()}


LOGIN_CAPABILITIES: dict[str, PlatformLoginCapability] = {
    "web": PlatformLoginCapability("web", "网页", "not_required"),
    "github": PlatformLoginCapability("github", "GitHub", "not_required"),
    "youtube": PlatformLoginCapability("youtube", "YouTube", "not_required"),
    "bilibili": PlatformLoginCapability("bilibili", "Bilibili", "not_required"),
    "x": PlatformLoginCapability("x", "X / Twitter", "required", ("x", "twitter")),
    "twitter": PlatformLoginCapability("twitter", "X / Twitter", "required", ("x", "twitter")),
    "xhs": PlatformLoginCapability("xhs", "小红书", "required"),
    "wechat": PlatformLoginCapability("wechat", "微信公众号", "required"),
    "feishu": PlatformLoginCapability("feishu", "飞书", "required"),
    "kdocs": PlatformLoginCapability("kdocs", "金山文档", "required"),
    "flowus": PlatformLoginCapability("flowus", "FlowUs", "required"),
    "zhihu": PlatformLoginCapability("zhihu", "知乎", "required"),
    "linuxdo": PlatformLoginCapability("linuxdo", "LinuxDo", "required"),
    "idcflare": PlatformLoginCapability("idcflare", "IDCFlare", "required"),
    "zsxq": PlatformLoginCapability("zsxq", "知识星球", "required"),
    "reddit": PlatformLoginCapability("reddit", "Reddit", "required"),
}


PLATFORM_SETTINGS_SCHEMA = PlatformSettingsSchema(
    platforms=(
        PlatformSettingsGroup(
            id="core",
            name="基础设置",
            fields=(
                PlatformSettingField("OUTPUT_DIR", "path", "输出目录", "core", "./output"),
                PlatformSettingField("OBSIDIAN_VAULT", "path", "Obsidian Vault", "core", description="高优先级"),
                PlatformSettingField("FEEDGRAB_DATA_DIR", "path", "登录态和数据目录", "core", "sessions"),
                PlatformSettingField("BROWSER_USER_AGENT", "string", "浏览器 User-Agent", "core"),
                PlatformSettingField("CHROME_CDP_LOGIN", "boolean", "登录时优先从 Chrome CDP 提取登录态", "core", False),
                PlatformSettingField("CHROME_CDP_PORT", "integer", "Chrome CDP 端口", "core", 9222),
                PlatformSettingField("FORCE_REFETCH", "boolean", "强制重新抓取", "core", False),
                PlatformSettingField("FEEDGRAB_PROXY_ENABLED", "boolean", "启用代理", "core", False),
                PlatformSettingField(
                    "FEEDGRAB_PROXY_URL",
                    "string",
                    "代理地址",
                    "core",
                    "",
                    description="支持 HTTP / SOCKS5 代理；日志和界面会隐藏密码。",
                    placeholder="http://127.0.0.1:7890 或 socks5://127.0.0.1:7890",
                ),
                PlatformSettingField(
                    "FEEDGRAB_NO_PROXY",
                    "string",
                    "不走代理地址",
                    "core",
                    "127.0.0.1,localhost",
                    description="用英文逗号分隔，避免本地 worker、CDP 和内部服务被代理干扰。",
                ),
            ),
        ),
        PlatformSettingsGroup(
            id="x",
            name="X / Twitter",
            fields=(
                PlatformSettingField("X_GRAPHQL_ENABLED", "boolean", "启用 GraphQL 深度抓取", "x", True),
                PlatformSettingField("X_THREAD_MAX_PAGES", "integer", "线程最大分页数", "x", 20),
                PlatformSettingField("X_REQUEST_DELAY", "number", "GraphQL 请求间隔秒数", "x", 1.5),
                PlatformSettingField("X_FETCH_AUTHOR_REPLIES", "boolean", "抓取作者回帖", "x", False),
                PlatformSettingField("X_FETCH_ALL_COMMENTS", "boolean", "抓取全部评论", "x", False),
                PlatformSettingField("X_BOOKMARKS_ENABLED", "boolean", "启用书签批量抓取", "x", False),
                PlatformSettingField("X_BOOKMARK_MAX_PAGES", "integer", "书签最大分页数", "x", 50),
                PlatformSettingField("X_USER_TWEETS_ENABLED", "boolean", "启用账号推文批量抓取", "x", False),
                PlatformSettingField("X_USER_TWEET_MAX_PAGES", "integer", "账号推文最大分页数", "x", 200),
                PlatformSettingField("X_USER_TWEET_DELAY", "number", "账号推文处理间隔秒数", "x", 2.0),
                PlatformSettingField("X_USER_TWEETS_SINCE", "string", "账号推文起始日期", "x", "", description="YYYY-MM-DD，留空表示全部"),
                PlatformSettingField("X_LIST_TWEETS_ENABLED", "boolean", "启用列表推文批量抓取", "x", False),
                PlatformSettingField("X_LIST_TWEETS_DAYS", "integer", "列表抓取最近天数", "x", 1),
                PlatformSettingField("X_LIST_TWEET_DELAY", "number", "列表推文处理间隔秒数", "x", 2.0),
                PlatformSettingField("X_LIST_TWEETS_SUMMARY", "boolean", "列表抓取后生成汇总表", "x", False),
                PlatformSettingField("X_SEARCH_ENABLED", "boolean", "启用关键词搜索", "x", True),
                PlatformSettingField(
                    "X_SEARCH_LANG",
                    "enum",
                    "搜索语言",
                    "x",
                    "zh",
                    options=(
                        {"label": "不限", "value": ""},
                        {"label": "全部 zh+zxx", "value": "zh+zxx"},
                        {"label": "中文 zh", "value": "zh"},
                        {"label": "英文 en", "value": "en"},
                        {"label": "日文 ja", "value": "ja"},
                    ),
                ),
                PlatformSettingField("X_SEARCH_DAYS", "integer", "搜索最近天数", "x", 1),
                PlatformSettingField("X_SEARCH_MIN_FAVES", "integer", "最低点赞数", "x", 0),
                PlatformSettingField("X_SEARCH_MIN_RETWEETS", "integer", "最低转发数", "x", 0),
                PlatformSettingField(
                    "X_SEARCH_SORT",
                    "enum",
                    "搜索排序",
                    "x",
                    "live",
                    options=(
                        {"label": "最新 live", "value": "live"},
                        {"label": "热门 top", "value": "top"},
                        {"label": "全量 Live+Top", "value": "all"},
                    ),
                ),
                PlatformSettingField("X_SEARCH_EXCLUDE_RETWEETS", "boolean", "排除转推", "x", True),
                PlatformSettingField("X_SEARCH_DELAY", "number", "搜索滚动间隔秒数", "x", 2.0),
                PlatformSettingField("X_SEARCH_MAX_RESULTS", "integer", "每次搜索最大结果数", "x", 100),
                PlatformSettingField("X_SEARCH_SAVE_TWEETS", "boolean", "保存单条推文 Markdown", "x", False),
                PlatformSettingField("X_SEARCH_MERGE_KEYWORDS", "boolean", "多关键词合并汇总表", "x", False),
                PlatformSettingField(
                    "X_API_PROVIDER",
                    "enum",
                    "API 提供方",
                    "x",
                    "graphql",
                    options=(
                        {"label": "GraphQL 默认流程", "value": "graphql"},
                        {"label": "TwitterAPI.io 付费 API", "value": "api"},
                    ),
                ),
                PlatformSettingField("X_API_SAVE_DIRECTLY", "boolean", "付费 API 结果直接保存", "x", False),
                PlatformSettingField("TWITTERAPI_IO_KEY", "secret", "TwitterAPI.io Key", "x", secret=True),
            ),
        ),
        PlatformSettingsGroup(
            id="xhs",
            name="小红书",
            fields=(
                PlatformSettingField("XHS_API_ENABLED", "boolean", "启用 API 优先模式", "xhs", True),
                PlatformSettingField("XHS_PINIA_ENABLED", "boolean", "启用 Pinia Store 兜底", "xhs", True),
                PlatformSettingField("XHS_API_DELAY", "number", "API 请求间隔秒数", "xhs", 1.0),
                PlatformSettingField("XHS_FETCH_COMMENTS", "boolean", "抓取单篇评论", "xhs", False),
                PlatformSettingField("XHS_MAX_COMMENTS", "integer", "评论最大页数", "xhs", 5),
                PlatformSettingField(
                    "XHS_SEARCH_SORT",
                    "enum",
                    "搜索排序",
                    "xhs",
                    "general",
                    options=(
                        {"label": "综合 general", "value": "general"},
                        {"label": "热门 popular", "value": "popular"},
                        {"label": "最新 latest", "value": "latest"},
                    ),
                ),
                PlatformSettingField(
                    "XHS_SEARCH_NOTE_TYPE",
                    "enum",
                    "搜索内容类型",
                    "xhs",
                    "all",
                    options=(
                        {"label": "全部 all", "value": "all"},
                        {"label": "视频 video", "value": "video"},
                        {"label": "图文 image", "value": "image"},
                    ),
                ),
                PlatformSettingField("XHS_SEARCH_MAX_PAGES", "integer", "搜索最大页数", "xhs", 10),
                PlatformSettingField("XHS_SEARCH_SAVE_NOTES", "boolean", "保存单条笔记 Markdown", "xhs", False),
                PlatformSettingField("XHS_SEARCH_MERGE_KEYWORDS", "boolean", "多关键词合并汇总表", "xhs", False),
                PlatformSettingField("XHS_USER_NOTES_ENABLED", "boolean", "启用作者笔记批量抓取", "xhs", False),
                PlatformSettingField("XHS_USER_NOTE_MAX_SCROLLS", "integer", "作者主页最大滚动次数", "xhs", 50),
                PlatformSettingField("XHS_USER_NOTE_DELAY", "number", "作者笔记处理间隔秒数", "xhs", 3.0),
                PlatformSettingField("XHS_USER_NOTES_SINCE", "string", "作者笔记起始日期", "xhs", "", description="YYYY-MM-DD，留空表示全部"),
                PlatformSettingField("XHS_SEARCH_ENABLED", "boolean", "启用搜索结果批量抓取", "xhs", False),
                PlatformSettingField("XHS_SEARCH_MAX_SCROLLS", "integer", "搜索页最大滚动次数", "xhs", 30),
                PlatformSettingField("XHS_SEARCH_DELAY", "number", "搜索笔记处理间隔秒数", "xhs", 3.0),
            ),
        ),
        PlatformSettingsGroup(
            id="wechat",
            name="微信公众号",
            fields=(
                PlatformSettingField("MPWEIXIN_SOGOU_ENABLED", "boolean", "启用搜狗微信搜索", "wechat", False),
                PlatformSettingField("MPWEIXIN_SOGOU_MAX_RESULTS", "integer", "搜狗搜索最大文章数", "wechat", 10),
                PlatformSettingField("MPWEIXIN_SOGOU_DELAY", "number", "搜狗文章处理间隔秒数", "wechat", 3.0),
                PlatformSettingField("MPWEIXIN_ID_SINCE", "string", "账号文章起始日期", "wechat", "", description="YYYY-MM-DD，留空表示全部"),
                PlatformSettingField("MPWEIXIN_ID_DELAY", "number", "账号文章处理间隔秒数", "wechat", 3.0),
                PlatformSettingField("MPWEIXIN_ZHUANJI_SINCE", "string", "专辑文章起始日期", "wechat", "", description="YYYY-MM-DD，留空表示全部"),
                PlatformSettingField("MPWEIXIN_ZHUANJI_DELAY", "number", "专辑文章处理间隔秒数", "wechat", 3.0),
                PlatformSettingField("MPWEIXIN_FETCH_COMMENTS", "boolean", "抓取精选评论", "wechat", False),
                PlatformSettingField("MPWEIXIN_MAX_COMMENTS", "integer", "最大评论数", "wechat", 100),
            ),
        ),
        PlatformSettingsGroup(
            id="discourse",
            name="Discourse论坛",
            fields=(
                PlatformSettingField(
                    "LINUXDO_REPLY_MODE",
                    "enum",
                    "LinuxDo 回复模式",
                    "discourse",
                    "author",
                    options=(
                        {"label": "主贴 + 楼主自回 author", "value": "author"},
                        {"label": "完整楼层 all", "value": "all"},
                        {"label": "仅主贴 none", "value": "none"},
                    ),
                ),
                PlatformSettingField("LINUXDO_PAGE_LOAD_TIMEOUT", "integer", "LinuxDo 页面等待毫秒", "discourse", 15000),
                PlatformSettingField("LINUXDO_CDP_ENABLED", "boolean", "LinuxDo 复用 Chrome CDP", "discourse", True),
                PlatformSettingField(
                    "IDCFLARE_REPLY_MODE",
                    "enum",
                    "IDCFlare 回复模式",
                    "discourse",
                    "author",
                    options=(
                        {"label": "主贴 + 楼主自回 author", "value": "author"},
                        {"label": "完整楼层 all", "value": "all"},
                        {"label": "仅主贴 none", "value": "none"},
                    ),
                ),
                PlatformSettingField("IDCFLARE_PAGE_LOAD_TIMEOUT", "integer", "IDCFlare 页面等待毫秒", "discourse", 15000),
                PlatformSettingField("IDCFLARE_CDP_ENABLED", "boolean", "IDCFlare 复用 Chrome CDP", "discourse", True),
            ),
        ),
        PlatformSettingsGroup(
            id="reddit",
            name="Reddit",
            fields=(
                PlatformSettingField("REDDIT_ENABLED", "boolean", "启用 Reddit 抓取", "reddit", True),
                PlatformSettingField("REDDIT_CDP_ENABLED", "boolean", "Reddit 复用 Chrome CDP", "reddit", True),
                PlatformSettingField("REDDIT_PAGE_LOAD_TIMEOUT", "integer", "Reddit 页面等待毫秒", "reddit", 15000),
                PlatformSettingField("REDDIT_MAX_COMMENTS", "integer", "评论最大条数", "reddit", 50),
                PlatformSettingField(
                    "REDDIT_REPLY_MODE",
                    "select",
                    "评论模式",
                    "reddit",
                    "top",
                    options=(
                        {"label": "仅顶层 top", "value": "top"},
                        {"label": "保留嵌套 tree", "value": "tree"},
                        {"label": "完整展开 all", "value": "all"},
                    ),
                    description="默认 top，tree/all 会让大帖 Markdown 明显变长。",
                ),
                PlatformSettingField("REDDIT_RETRY_ATTEMPTS", "integer", "HTTP 重试次数", "reddit", 3),
                PlatformSettingField("REDDIT_MAX_PAGES", "integer", "列表最大分页数", "reddit", 5),
                PlatformSettingField("REDDIT_MORECHILDREN_ROUNDS", "integer", "morechildren 展开轮数", "reddit", 2),
                PlatformSettingField("REDDIT_MORECHILDREN_BATCH_SIZE", "integer", "morechildren 每批数量", "reddit", 100),
                PlatformSettingField("REDDIT_FETCH_ALL_COMMENTS", "boolean", "抓取全部评论", "reddit", False),
                PlatformSettingField("REDDIT_USER_AGENT", "string", "Reddit User-Agent", "reddit", ""),
                PlatformSettingField("REDDIT_SUB_LIMIT", "integer", "子版块抓取条数", "reddit", 25),
                PlatformSettingField("REDDIT_SUB_DELAY", "number", "子版块帖子间隔秒数", "reddit", 2.0),
                PlatformSettingField("REDDIT_SEARCH_ENABLED", "boolean", "启用 Reddit 帖子搜索", "reddit", True),
                PlatformSettingField(
                    "REDDIT_SEARCH_SORT",
                    "select",
                    "帖子搜索排序",
                    "reddit",
                    "relevance",
                    options=(
                        {"label": "相关性 relevance", "value": "relevance"},
                        {"label": "热门 hot", "value": "hot"},
                        {"label": "最受欢迎 top", "value": "top"},
                        {"label": "新 new", "value": "new"},
                        {"label": "评论计数 comments", "value": "comments"},
                    ),
                ),
                PlatformSettingField(
                    "REDDIT_SEARCH_TIME_RANGE",
                    "select",
                    "帖子搜索时间范围",
                    "reddit",
                    "all",
                    options=(
                        {"label": "所有时间 all", "value": "all"},
                        {"label": "去年 year", "value": "year"},
                        {"label": "上个月 month", "value": "month"},
                        {"label": "上周 week", "value": "week"},
                        {"label": "今天 day", "value": "day"},
                        {"label": "过去 1 小时 hour", "value": "hour"},
                    ),
                ),
                PlatformSettingField("REDDIT_SEARCH_LIMIT", "integer", "帖子搜索结果数", "reddit", 10),
                PlatformSettingField("REDDIT_SEARCH_SAVE_POSTS", "boolean", "搜索后深抓单贴", "reddit", False),
                PlatformSettingField(
                    "REDDIT_SEARCH_SUBREDDIT",
                    "string",
                    "限定子版块",
                    "reddit",
                    "",
                    placeholder="ChatGPT（留空表示全站）",
                ),
            ),
        ),
        PlatformSettingsGroup(
            id="feishu",
            name="文档平台",
            fields=(
                PlatformSettingField("FEISHU_APP_ID", "string", "飞书 App ID", "feishu"),
                PlatformSettingField("FEISHU_APP_SECRET", "secret", "飞书 App Secret", "feishu", secret=True),
                PlatformSettingField("FEISHU_CDP_ENABLED", "boolean", "飞书复用 Chrome CDP", "feishu", False),
                PlatformSettingField("FEISHU_WIKI_DELAY", "number", "飞书知识库批量间隔秒数", "feishu", 2.0),
                PlatformSettingField("FEISHU_DOWNLOAD_IMAGES", "boolean", "飞书图片下载到本地", "feishu", False),
                PlatformSettingField("FEISHU_PAGE_LOAD_TIMEOUT", "integer", "飞书页面等待毫秒", "feishu", 5000),
                PlatformSettingField("FEISHU_CUSTOM_DOMAINS", "string", "飞书私有化域名", "feishu", "", description="多个域名用英文逗号分隔"),
                PlatformSettingField("KDOCS_CDP_ENABLED", "boolean", "金山文档复用 Chrome CDP", "feishu", True),
                PlatformSettingField("KDOCS_PAGE_LOAD_TIMEOUT", "integer", "金山文档页面等待毫秒", "feishu", 10000),
                PlatformSettingField("KDOCS_DOWNLOAD_IMAGES", "boolean", "金山文档图片下载到本地", "feishu", False),
                PlatformSettingField("FLOWUS_CDP_ENABLED", "boolean", "FlowUs 复用 Chrome CDP", "feishu", True),
                PlatformSettingField("FLOWUS_PAGE_LOAD_TIMEOUT", "integer", "FlowUs 页面等待毫秒", "feishu", 10000),
                PlatformSettingField("FLOWUS_DOWNLOAD_IMAGES", "boolean", "FlowUs 图片下载到本地", "feishu", False),
                PlatformSettingField("YOUDAO_DOWNLOAD_IMAGES", "boolean", "有道云图片下载到本地", "feishu", False),
                PlatformSettingField("GITHUB_TOKEN", "secret", "GitHub Token", "feishu", secret=True),
            ),
        ),
        PlatformSettingsGroup(
            id="video_podcast",
            name="视频播客",
            fields=(
                PlatformSettingField("YOUTUBE_API_KEY", "secret", "YouTube Data API Key", "video_podcast", secret=True),
                PlatformSettingField("YOUTUBE_REGION", "string", "YouTube 搜索地区", "video_podcast", "US"),
                PlatformSettingField(
                    "YOUTUBE_LANG",
                    "enum",
                    "YouTube 搜索语言",
                    "video_podcast",
                    "zh-CN",
                    options=(
                        {"label": "中文 zh-CN", "value": "zh-CN"},
                        {"label": "英文 en", "value": "en"},
                        {"label": "日文 ja", "value": "ja"},
                    ),
                ),
                PlatformSettingField("YOUTUBE_MAX_RESULTS", "integer", "YouTube 搜索结果数", "video_podcast", 10),
                PlatformSettingField(
                    "YOUTUBE_DOWNLOAD_QUALITY",
                    "enum",
                    "YouTube 下载清晰度",
                    "video_podcast",
                    "1080p",
                    options=(
                        {"label": "best", "value": "best"},
                        {"label": "1080p", "value": "1080p"},
                        {"label": "720p", "value": "720p"},
                        {"label": "480p", "value": "480p"},
                    ),
                ),
                PlatformSettingField("YOUTUBE_WHISPER_LANG", "string", "YouTube Whisper 语言", "video_podcast", "zh"),
                PlatformSettingField("BILIBILI_SUBTITLE_ENABLED", "boolean", "Bilibili 抓取字幕", "video_podcast", True),
                PlatformSettingField("BILIBILI_SUBTITLE_LANG", "string", "Bilibili 字幕语言", "video_podcast", "zh-CN"),
                PlatformSettingField("BILIBILI_SUBTITLE_WHISPER", "boolean", "Bilibili 无字幕时 Whisper", "video_podcast", False),
                PlatformSettingField("XIAOYUZHOU_ENABLED", "boolean", "小宇宙抓取启用", "video_podcast", True),
                PlatformSettingField("XIAOYUZHOU_WHISPER", "boolean", "小宇宙 Whisper 转录", "video_podcast", True),
                PlatformSettingField("XIMALAYA_ENABLED", "boolean", "喜马拉雅抓取启用", "video_podcast", True),
                PlatformSettingField("XIMALAYA_WHISPER", "boolean", "喜马拉雅 Whisper 转录", "video_podcast", True),
            ),
        ),
        PlatformSettingsGroup(
            id="zhihu",
            name="知乎",
            fields=(
                PlatformSettingField("ZHIHU_CDP_ENABLED", "boolean", "知乎复用 Chrome CDP", "zhihu", True),
                PlatformSettingField("ZHIHU_PAGE_LOAD_TIMEOUT", "integer", "知乎页面等待毫秒", "zhihu", 10000),
                PlatformSettingField("ZHIHU_DOWNLOAD_IMAGES", "boolean", "知乎图片下载到本地", "zhihu", False),
                PlatformSettingField("ZHIHU_SEARCH_DAYS", "integer", "知乎搜索最近天数", "zhihu", 30),
                PlatformSettingField("ZHIHU_SEARCH_LIMIT", "integer", "知乎搜索最大结果数", "zhihu", 50),
                PlatformSettingField("ZHIHU_SEARCH_SAVE_ANSWERS", "boolean", "知乎搜索保存答案", "zhihu", False),
                PlatformSettingField("ZHIHU_SEARCH_DELAY", "number", "知乎搜索请求间隔秒数", "zhihu", 2.0),
            ),
        ),
        PlatformSettingsGroup(
            id="telegram",
            name="Telegram",
            fields=(
                PlatformSettingField("TG_API_ID", "string", "Telegram API ID", "telegram"),
                PlatformSettingField("TG_API_HASH", "secret", "Telegram API Hash", "telegram", secret=True),
            ),
        ),
        PlatformSettingsGroup(id="rss", name="RSS"),
        PlatformSettingsGroup(id="web", name="任意网页"),
        PlatformSettingsGroup(
            id="zsxq",
            name="知识星球",
            fields=(),
        ),
        PlatformSettingsGroup(
            id="media_ai",
            name="媒体 / API",
            fields=(
                PlatformSettingField("GROQ_API_KEY", "secret", "Groq API Key", "media_ai", secret=True),
                PlatformSettingField("GEMINI_API_KEY", "secret", "Gemini API Key", "media_ai", secret=True),
                PlatformSettingField("GROQ_WHISPER_MODEL", "string", "Groq Whisper 模型", "media_ai", "whisper-large-v3"),
                PlatformSettingField("TG_API_ID", "string", "Telegram API ID", "media_ai"),
                PlatformSettingField("TG_API_HASH", "secret", "Telegram API Hash", "media_ai", secret=True),
            ),
        ),
    )
)


def get_login_capability(platform: str) -> PlatformLoginCapability:
    normalized = platform.strip().lower()
    return LOGIN_CAPABILITIES.get(
        normalized,
        PlatformLoginCapability(normalized or platform, platform or "未知平台", "required"),
    )


def get_platform_settings_schema() -> PlatformSettingsSchema:
    return PLATFORM_SETTINGS_SCHEMA
