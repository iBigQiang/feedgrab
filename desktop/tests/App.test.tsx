import { readFileSync } from "node:fs";
import { join } from "node:path";

import "@testing-library/jest-dom/vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "../renderer/src/App";
import type {
  FeedgrabIpcApi,
  FeedgrabWorkerEvent,
  FetchJobSnapshot,
  OutputArtifact,
  SettingsSchema
} from "../electron/ipc-types";

const INSTALL_OUTPUT_DIR = "D:\\feedgrab Desktop\\output";

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
});

afterEach(() => {
  vi.unstubAllGlobals();
  delete window.feedgrab;
  window.localStorage.clear();
  window.sessionStorage.clear();
});

describe("App", () => {
  it("renders the desktop workspace and completes a mock worker fetch from pasted URLs", async () => {
    render(<App />);

    expect(screen.getByRole("heading", { name: "抓取工作台" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "开始抓取" })).toBeInTheDocument();
    expect(screen.getByText("诊断")).toBeInTheDocument();
    expect(screen.getByText("赞助")).toBeInTheDocument();
    expect(screen.getByText("社群")).toBeInTheDocument();
    expect(screen.queryByText("授权")).not.toBeInTheDocument();
    expect(screen.getByText("版本号：v0.1.14")).toBeInTheDocument();
    expect(screen.getByText("强子手记").closest(".author-row")).toHaveTextContent("作者：强子手记");
    expect(screen.getByRole("link", { name: "@iBigQiang" })).toHaveAttribute("href", "https://x.com/iBigQiang");
    expect(screen.queryByText("商业化 GUI 客户端分支")).not.toBeInTheDocument();
    expect(screen.getByText("现已支持的平台：")).toBeInTheDocument();
    expect(screen.getByText("YouTube")).toBeInTheDocument();
    expect(screen.getByText("Reddit")).toBeInTheDocument();
    expect(screen.getByText("知识星球")).toBeInTheDocument();
    const platformButtons = within(screen.getByLabelText("平台识别结果")).getAllByRole("button");
    const platformLabels = platformButtons.map((button) => button.textContent ?? "");
    expect(platformLabels.indexOf("Reddit")).toBe(platformLabels.indexOf("知识星球") + 1);
    expect(platformLabels.indexOf("小宇宙")).toBe(platformLabels.indexOf("Reddit") + 1);
    expect(
      Boolean(
        screen.getByLabelText("平台识别结果").compareDocumentPosition(screen.getByLabelText("抓取目标（URL / 关键词 / 关键词组 / 账号）")) &
          Node.DOCUMENT_POSITION_FOLLOWING
      )
    ).toBe(true);
    await screen.findByText("浏览器测试后台工作进程已连接。");

    fireEvent.change(screen.getByLabelText("抓取目标（URL / 关键词 / 关键词组 / 账号）"), {
      target: { value: "https://example.com/a\nhttps://example.com/b" }
    });
    fireEvent.click(screen.getByRole("button", { name: "开始抓取" }));

    expect(screen.getAllByText(/example.com/).length).toBeGreaterThan(0);
    await screen.findByText(/后台工作进程已接收 2 条任务，输出到/);
    expect(await screen.findAllByText("抓取完成")).toHaveLength(2);
    const logMessages = screen.getAllByTestId("log-message").map((item) => item.textContent ?? "");
    expect(logMessages[0]).toContain("抓取完成");
    fireEvent.click(screen.getByRole("button", { name: "输出" }));
    expect(screen.queryByText("已创建 https://example.com/a 抓取任务")).not.toBeInTheDocument();
    expect(await screen.findByText("1.md")).toBeInTheDocument();
  });

  it("keeps sidebar author rows visually consistent and links only the social icons", () => {
    const { container } = render(<App />);

    const rows = Array.from(container.querySelectorAll(".author-row"));
    expect(rows.map((row) => row.textContent?.replace(/\s+/g, ""))).toEqual([
      "作者：强子手记",
      "主页：@iBigQiang",
      "推特：X",
      "仓库：GitHub"
    ]);
    expect(container.querySelectorAll(".author-row")).toHaveLength(4);
    expect(container.querySelectorAll(".author-row strong")).toHaveLength(0);

    const twitterRow = screen.getByText("推特：").closest(".author-row") as HTMLElement;
    const githubRow = screen.getByText("仓库：").closest(".author-row") as HTMLElement;
    expect(within(twitterRow).getByRole("link", { name: "推特" })).toHaveAttribute("href", "https://x.com/iBigQiang");
    expect(within(githubRow).getByRole("link", { name: "仓库" })).toHaveAttribute(
      "href",
      "https://github.com/iBigQiang/feedgrab/tree/feedgrab-desktop"
    );
    expect(within(twitterRow).getByText("X").closest("a")).toBeNull();
    expect(within(githubRow).getByText("GitHub").closest("a")).toBeNull();
  });

  it("submits selected X keywords as a structured search task and shows the command preview", async () => {
    const api = createTestApi({
      startFetch: vi.fn().mockResolvedValue([
        {
          id: "job-search",
          url: 'feedgrab x-so "claude code,openclaw"',
          platform: "twitter",
          status: "running",
          outputDirectory: INSTALL_OUTPUT_DIR,
          createdAt: "2026-06-26T09:00:00.000Z"
        }
      ])
    });
    window.feedgrab = api;

    render(<App />);
    await screen.findByText(INSTALL_OUTPUT_DIR);

    fireEvent.click(screen.getByRole("button", { name: "X / Twitter" }));
    fireEvent.change(screen.getByLabelText("抓取目标（URL / 关键词 / 关键词组 / 账号）"), {
      target: { value: "claude code,openclaw" }
    });

    expect(screen.getByText('将执行：feedgrab x-so "claude code,openclaw"')).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "开始抓取" }));

    await waitFor(() =>
      expect(api.startFetch).toHaveBeenCalledWith({
        urls: [],
        targets: ["claude code,openclaw"],
        platform: "twitter",
        mode: "search",
        commandPreview: 'feedgrab x-so "claude code,openclaw"',
        outputDirectory: INSTALL_OUTPUT_DIR
      })
    );
  });

  it("submits selected Reddit keywords as a structured search task with settings options", async () => {
    const api = createTestApi({
      settingsSchema: vi.fn().mockResolvedValue({
        basic: [],
        platforms: [
          {
            id: "reddit",
            label: "Reddit",
            fields: [
              {
                name: "REDDIT_SEARCH_SORT",
                label: "帖子搜索排序",
                type: "select",
                value: "comments",
                options: [
                  { label: "相关性 relevance", value: "relevance" },
                  { label: "评论计数 comments", value: "comments" }
                ]
              },
              {
                name: "REDDIT_SEARCH_TIME_RANGE",
                label: "帖子搜索时间范围",
                type: "select",
                value: "all",
                options: [
                  { label: "所有时间 all", value: "all" },
                  { label: "上周 week", value: "week" }
                ]
              },
              { name: "REDDIT_SEARCH_LIMIT", label: "帖子搜索结果数", type: "number", value: 10 },
              { name: "REDDIT_SEARCH_SAVE_POSTS", label: "搜索后深抓单贴", type: "boolean", value: false },
              { name: "REDDIT_SEARCH_SUBREDDIT", label: "限定子版块", type: "string", value: "" }
            ]
          }
        ]
      }),
      startFetch: vi.fn().mockResolvedValue([
        {
          id: "job-reddit-search",
          url: "feedgrab reddit-so codex --sort comments --time all --limit 10",
          platform: "reddit",
          status: "running",
          outputDirectory: INSTALL_OUTPUT_DIR,
          createdAt: "2026-06-26T09:00:00.000Z"
        }
      ])
    });
    window.feedgrab = api;

    render(<App />);
    await screen.findByText(INSTALL_OUTPUT_DIR);

    fireEvent.click(screen.getByRole("button", { name: "Reddit" }));
    fireEvent.change(screen.getByLabelText("抓取目标（URL / 关键词 / 关键词组 / 账号）"), {
      target: { value: "codex" }
    });

    expect(await screen.findByText("将执行：feedgrab reddit-so codex --sort comments --time all --limit 10")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "开始抓取" }));

    await waitFor(() =>
      expect(api.startFetch).toHaveBeenCalledWith({
        urls: [],
        targets: ["codex"],
        platform: "reddit",
        mode: "search",
        options: {
          sort: "comments",
          time: "all",
          limit: 10
        },
        commandPreview: "feedgrab reddit-so codex --sort comments --time all --limit 10",
        outputDirectory: INSTALL_OUTPUT_DIR
      })
    );
  });

  it("omits Reddit time range from hot and new search previews", async () => {
    const api = createTestApi({
      settingsSchema: vi.fn().mockResolvedValue({
        basic: [],
        platforms: [
          {
            id: "reddit",
            label: "Reddit",
            fields: [
              {
                name: "REDDIT_SEARCH_SORT",
                label: "帖子搜索排序",
                type: "select",
                value: "hot",
                options: [{ label: "热门 hot", value: "hot" }]
              },
              {
                name: "REDDIT_SEARCH_TIME_RANGE",
                label: "帖子搜索时间范围",
                type: "select",
                value: "week",
                options: [{ label: "上周 week", value: "week" }]
              },
              { name: "REDDIT_SEARCH_LIMIT", label: "帖子搜索结果数", type: "number", value: 10 }
            ]
          }
        ]
      })
    });
    window.feedgrab = api;

    render(<App />);
    await screen.findByText(INSTALL_OUTPUT_DIR);

    fireEvent.click(screen.getByRole("button", { name: "Reddit" }));
    fireEvent.change(screen.getByLabelText("抓取目标（URL / 关键词 / 关键词组 / 账号）"), {
      target: { value: "codex" }
    });

    expect(await screen.findByText("将执行：feedgrab reddit-so codex --sort hot --limit 10")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "开始抓取" }));

    await waitFor(() =>
      expect(api.startFetch).toHaveBeenCalledWith(
        expect.objectContaining({
          options: {
            sort: "hot",
            limit: 10
          },
          commandPreview: "feedgrab reddit-so codex --sort hot --limit 10"
        })
      )
    );
  });

  it("uses effective output directory on fetch page and in startFetch payload", async () => {
    const api = createTestApi({
      settingsSnapshot: vi.fn().mockResolvedValue({
        outputDirectory: "D:\\feedgrab Desktop\\output",
        obsidianVault: "D:\\Notes\\Vault",
        effectiveOutputDirectory: "D:\\Notes\\Vault",
        concurrency: 1,
        downloadImages: true,
        localizeMedia: true,
        replyMode: "author"
      }),
      startFetch: vi.fn().mockResolvedValue([
        {
          id: "job-search",
          url: 'feedgrab x-so "openclaw"',
          platform: "twitter",
          status: "running",
          outputDirectory: "D:\\Notes\\Vault",
          createdAt: "2026-06-26T09:00:00.000Z"
        }
      ])
    });
    window.feedgrab = api;

    render(<App />);
    await screen.findByText("D:\\Notes\\Vault");

    fireEvent.click(screen.getByRole("button", { name: "X / Twitter" }));
    fireEvent.change(screen.getByLabelText("抓取目标（URL / 关键词 / 关键词组 / 账号）"), {
      target: { value: "openclaw" }
    });
    expect(screen.getByText("D:\\Notes\\Vault")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "开始抓取" }));

    await waitFor(() =>
      expect(api.startFetch).toHaveBeenCalledWith({
        urls: [],
        targets: ["openclaw"],
        platform: "twitter",
        mode: "search",
        commandPreview: "feedgrab x-so openclaw",
        outputDirectory: "D:\\Notes\\Vault"
      })
    );
  });

  it("blocks fetch until edited settings are saved", async () => {
    const api = createTestApi({
      settingsSchema: vi.fn().mockResolvedValue({
        basic: [{ name: "FEEDGRAB_PROXY_ENABLED", label: "启用代理", type: "boolean", value: false }],
        platforms: []
      }),
      startFetch: vi.fn().mockResolvedValue([
        {
          id: "job-unsaved",
          url: "https://example.com/a",
          platform: "web",
          status: "running",
          outputDirectory: INSTALL_OUTPUT_DIR,
          createdAt: "2026-06-30T09:00:00.000Z"
        }
      ])
    });
    window.feedgrab = api;

    render(<App />);
    fireEvent.click(screen.getByText("设置"));
    const proxyToggle = await screen.findByLabelText("启用代理");
    fireEvent.click(proxyToggle);

    fireEvent.click(screen.getByText("抓取"));
    fireEvent.change(screen.getByLabelText("抓取目标（URL / 关键词 / 关键词组 / 账号）"), {
      target: { value: "https://example.com/a" }
    });
    fireEvent.click(screen.getByRole("button", { name: "开始抓取" }));

    expect(api.startFetch).not.toHaveBeenCalled();
    expect(await screen.findByText("有未保存设置，请先保存设置后再开始抓取。")).toBeInTheDocument();
  });

  it("renders the sponsor page from bundled markdown and keeps author links in the sidebar", async () => {
    render(<App />);

    const xLink = screen.getByRole("link", { name: "推特" });
    const githubLink = screen.getByRole("link", { name: "仓库" });
    expect(xLink).toHaveAttribute("href", "https://x.com/iBigQiang");
    expect(githubLink).toHaveAttribute("href", "https://github.com/iBigQiang/feedgrab/tree/feedgrab-desktop");

    fireEvent.click(screen.getByRole("button", { name: "赞助" }));

    expect(screen.getByRole("heading", { name: "赞助" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /赞助商/ })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /捐赠打赏/ })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "想出现在这里？" })).toHaveAttribute("href", "mailto:ibigqiang@gmail.com");
    expect(screen.getByRole("link", { name: "此链接" })).toHaveAttribute(
      "href",
      "https://hitu.me/zh/notices/order-and-get-20-off-in-points"
    );
    const sponsorTable = screen.getByRole("table", { name: "赞助商列表" });
    expect(within(sponsorTable).getAllByRole("row").length).toBeGreaterThanOrEqual(1);
    expect(within(sponsorTable).getAllByRole("img", { name: /嗨图象/ })[0].getAttribute("src")).toMatch(/^https:\/\//);
    expect(screen.getAllByText(/嗨图象/).length).toBeGreaterThan(0);
  });

  it("loads online sponsor markdown once per client session and caches it", async () => {
    const onlineMarkdown = [
      "# feedgrab Desktop",
      "",
      "## ❤️赞助商",
      "",
      "> [想出现在这里？](mailto:ibigqiang@gmail.com)",
      "",
      "[![在线图](https://hitu.me/og.png)](https://hitu.me/zh)",
      "",
      "<table>",
      "<tr>",
      "<td width=\"180\"><a href=\"https://hitu.me/zh/\"><img src=\"https://hitu.me/og.png\" alt=\"在线赞助商\" width=\"150\"></a></td>",
      "<td>在线赞助商说明，使用<a href=\"https://hitu.me/zh\">此链接</a>体验。</td>",
      "</tr>",
      "</table>"
    ].join("\n");
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      text: vi.fn().mockResolvedValue(onlineMarkdown)
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "赞助" }));

    expect(await screen.findByText(/在线赞助商说明/)).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith(
      "https://edgeone.gh-proxy.com/https://raw.githubusercontent.com/iBigQiang/feedgrab/feedgrab-desktop/docs/sponsor.md",
      { cache: "no-cache" }
    );
    expect(window.localStorage.getItem("feedgrab.sponsorMarkdown.cache.v1")).toContain("在线赞助商说明");

    fireEvent.click(screen.getByRole("button", { name: "抓取" }));
    fireEvent.click(screen.getByRole("button", { name: "赞助" }));
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("loads remote sponsor markdown through the Electron proxy-aware API when available", async () => {
    const onlineMarkdown = [
      "# feedgrab Desktop",
      "",
      "## ❤️赞助商",
      "",
      "<table>",
      "<tr>",
      "<td width=\"180\"><a href=\"https://hitu.me/zh/\"><img src=\"https://hitu.me/og.png\" alt=\"代理赞助商\" width=\"150\"></a></td>",
      "<td>代理赞助商说明。</td>",
      "</tr>",
      "</table>"
    ].join("\n");
    const fetchMock = vi.fn().mockRejectedValue(new Error("renderer fetch should not be used"));
    vi.stubGlobal("fetch", fetchMock);
    const api = createTestApi({
      fetchRemoteMarkdown: vi.fn().mockResolvedValue({ ok: true, markdown: onlineMarkdown })
    });
    window.feedgrab = api;

    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "赞助" }));

    expect(await screen.findByText("代理赞助商说明。")).toBeInTheDocument();
    expect(api.fetchRemoteMarkdown).toHaveBeenCalledWith(
      "https://edgeone.gh-proxy.com/https://raw.githubusercontent.com/iBigQiang/feedgrab/feedgrab-desktop/docs/sponsor.md"
    );
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("keeps online sponsor tables in order and renders raw html payment images", async () => {
    const paymentImageUrl =
      "https://edgeone.gh-proxy.com/https://github.com/iBigQiang/feedgrab/raw/feedgrab-desktop/docs/Payment_QR_code.png";
    const onlineMarkdown = [
      "# feedgrab Desktop",
      "",
      "## ❤️ feedgrab Desktop 赞助商",
      "",
      "> [想出现在这里？](mailto:ibigqiang@gmail.com)",
      "",
      "<table>",
      "<tr>",
      "<td width=\"180\"><a href=\"https://hitu.me/zh/\"><img src=\"https://hitu.me/og.png\" alt=\"嗨图象\" width=\"150\"></a></td>",
      "<td>感谢「嗨图象」赞助了本项目。</td>",
      "</tr>",
      "</table>",
      "",
      "## 👍 捐赠打赏",
      "",
      "如果 feedgrab 对你有帮助，欢迎请作者喝杯咖啡 :)",
      "",
      "<p align=\"center\">",
      "",
      `<img src="${paymentImageUrl}" alt="打赏码" width="600">`,
      "",
      "</p>"
    ].join("\n");
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        text: vi.fn().mockResolvedValue(onlineMarkdown)
      })
    );

    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "赞助" }));

    const sponsorTable = await screen.findByRole("table", { name: "赞助商列表" });
    const donationHeading = screen.getByRole("heading", { name: /捐赠打赏/ });
    expect(
      Boolean(sponsorTable.compareDocumentPosition(donationHeading) & Node.DOCUMENT_POSITION_FOLLOWING)
    ).toBe(true);
    const paymentImage = screen.getByRole("img", { name: "打赏码" });
    expect(paymentImage).toHaveAttribute("src", paymentImageUrl);
    expect(paymentImage).toHaveAttribute("width", "600");
    expect(screen.queryByText(/<img src=/)).not.toBeInTheDocument();
    expect(screen.queryByText("<p align=\"center\">")).not.toBeInTheDocument();
  });

  it("shows cached sponsor markdown while remote refresh is unavailable", async () => {
    const cachedMarkdown = [
      "# feedgrab Desktop",
      "",
      "## ❤️赞助商",
      "",
      "<table>",
      "<tr>",
      "<td width=\"180\"><a href=\"https://hitu.me/zh/\"><img src=\"https://hitu.me/og.png\" alt=\"缓存赞助商\" width=\"150\"></a></td>",
      "<td>缓存赞助商说明。</td>",
      "</tr>",
      "</table>"
    ].join("\n");
    window.localStorage.setItem(
      "feedgrab.sponsorMarkdown.cache.v1",
      JSON.stringify({ markdown: cachedMarkdown, fetchedAt: Date.now() })
    );

    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "赞助" }));

    expect(await screen.findByText("缓存赞助商说明。")).toBeInTheDocument();
  });

  it("renders the community page from bundled markdown while remote refresh is unavailable", async () => {
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "社群" }));

    expect(screen.getByRole("heading", { name: "社群" })).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: /feedgrab 用户交流群/ })).toBeInTheDocument();
    expect(screen.getByText(/添加作者微信/)).toBeInTheDocument();
    expect(screen.getByText(/88667178/)).toBeInTheDocument();
    const communityTable = screen.getByRole("table", { name: "社群信息" });
    expect(communityTable).toBeInTheDocument();
    const textCell = within(communityTable).getByText(/很高兴 feedgrab/).closest("td") as HTMLTableCellElement;
    expect(within(textCell).queryByText("feedgrab 用户交流微信群")).not.toBeInTheDocument();
    expect(textCell.querySelectorAll("p")).toHaveLength(2);
    expect(within(textCell).getByText("88667178").tagName).toBe("STRONG");
    expect(within(textCell).getByText("feedgrab").tagName).toBe("STRONG");
    expect(within(textCell).getByText("抓取").tagName).toBe("STRONG");
  });

  it("loads online community markdown through the EdgeOne proxy and caches it", async () => {
    const onlineMarkdown = [
      "# feedgrab Desktop",
      "",
      "## ❤️ feedgrab 用户交流群",
      "",
      "> 在线社群说明",
      "",
      "<table>",
      "<tr>",
      "<td width=\"200\"><a href=\"#\"><img src=\"https://edgeone.gh-proxy.com/https://raw.githubusercontent.com/iBigQiang/feedgrab/feedgrab-desktop/docs/vx_88667178.jpg\" alt=\"在线社群二维码\"></a></td>",
      "<td>在线社群内容，欢迎交流。<br><br>添加微信 <strong>88667178</strong>，备注：<strong>feedgrab</strong>。</td>",
      "</tr>",
      "</table>"
    ].join("\n");
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      text: vi.fn().mockResolvedValue(onlineMarkdown)
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "社群" }));

    expect(await screen.findByText(/在线社群内容/)).toBeInTheDocument();
    const communityTable = screen.getByRole("table", { name: "社群信息" });
    const textCell = within(communityTable).getByText(/在线社群内容/).closest("td") as HTMLTableCellElement;
    expect(textCell.querySelectorAll("p")).toHaveLength(2);
    expect(within(textCell).getByText("88667178").tagName).toBe("STRONG");
    expect(within(textCell).getByText("feedgrab").tagName).toBe("STRONG");
    expect(fetchMock).toHaveBeenCalledWith(
      "https://edgeone.gh-proxy.com/https://raw.githubusercontent.com/iBigQiang/feedgrab/feedgrab-desktop/docs/group.md",
      { cache: "no-cache" }
    );
    expect(window.localStorage.getItem("feedgrab.communityMarkdown.cache.v1")).toContain("在线社群内容");
  });

  it("honors sponsor logo width from the online table cell", async () => {
    const onlineMarkdown = [
      "# feedgrab Desktop",
      "",
      "## ❤️赞助商",
      "",
      "<table>",
      "<tr>",
      "<td width=\"300\"><a href=\"https://hitu.me/zh/\"><img src=\"https://hitu.me/og.jpeg\" alt=\"宽图赞助商\"></a></td>",
      "<td>宽图赞助商说明。</td>",
      "</tr>",
      "</table>"
    ].join("\n");
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        text: vi.fn().mockResolvedValue(onlineMarkdown)
      })
    );

    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "赞助" }));

    const image = (await screen.findByRole("img", { name: "宽图赞助商" })) as HTMLImageElement;
    const cell = image.closest("td") as HTMLTableCellElement;
    expect(image.style.width).toBe("300px");
    expect(cell.style.width).toBe("300px");
    expect(cell.style.minWidth).toBe("300px");
  });

  it("allows proxied document fetches and remote document images in the renderer CSP", () => {
    const html = readFileSync(join(process.cwd(), "index.html"), "utf8");

    expect(html).toContain(
      "connect-src 'self' http://127.0.0.1:* ws://127.0.0.1:* https://edgeone.gh-proxy.com https://raw.githubusercontent.com"
    );
    expect(html).toContain("img-src 'self' data: file: https:");
  });

  it("shows the job-created notice inside the fetch form only", async () => {
    render(<App />);
    fireEvent.change(screen.getByLabelText("抓取目标（URL / 关键词 / 关键词组 / 账号）"), {
      target: { value: "https://mp.weixin.qq.com/s/demo" }
    });
    fireEvent.click(screen.getByRole("button", { name: "开始抓取" }));

    expect(await screen.findByText("已创建 https://mp.weixin.qq.com/s/demo 抓取任务")).toBeInTheDocument();
    fireEvent.click(screen.getByText("任务"));
    expect(screen.queryByText("已创建 https://mp.weixin.qq.com/s/demo 抓取任务")).not.toBeInTheDocument();
    fireEvent.click(screen.getByText("输出"));
    expect(screen.queryByText("已创建 https://mp.weixin.qq.com/s/demo 抓取任务")).not.toBeInTheDocument();
    fireEvent.click(screen.getByText("设置"));
    expect(screen.queryByText("已创建 https://mp.weixin.qq.com/s/demo 抓取任务")).not.toBeInTheDocument();
  });

  it("keeps the auto-detect job-created notice separated from the textarea", () => {
    const styles = readFileSync(join(process.cwd(), "renderer", "src", "styles.css"), "utf8");

    expect(styles).toContain("textarea + .inline-notice");
  });

  it("shows structured fetch failure details from worker done events", async () => {
    let workerEvent: ((event: FeedgrabWorkerEvent) => void) | undefined;
    const api = createTestApi({
      startFetch: vi.fn().mockResolvedValue([
        {
          id: "job-search-fail",
          url: 'feedgrab x-so "openclaw"',
          target: "openclaw",
          targets: ["openclaw"],
          platform: "twitter",
          mode: "search",
          commandPreview: 'feedgrab x-so "openclaw"',
          status: "running",
          outputDirectory: "D:\\Notes\\Feeds",
          createdAt: "2026-06-26T09:00:00.000Z"
        }
      ]),
      onWorkerEvent: vi.fn((callback) => {
        workerEvent = callback;
        return () => undefined;
      })
    });
    window.feedgrab = api;

    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "X / Twitter" }));
    fireEvent.change(screen.getByLabelText("抓取目标（URL / 关键词 / 关键词组 / 账号）"), {
      target: { value: "openclaw" }
    });
    fireEvent.click(screen.getByRole("button", { name: "开始抓取" }));
    await waitFor(() => expect(api.startFetch).toHaveBeenCalled());

    act(() => {
      workerEvent?.({
        id: "job-search-fail",
        event: "done",
        method: "fetch",
        result: {
          fetched: 0,
          errors: 1,
          command: 'feedgrab x-so "openclaw"',
          error: "[openclaw] missing Twitter login"
        }
      });
    });

    expect(await screen.findByText("抓取失败：[openclaw] missing Twitter login")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "任务" }));
    expect(await screen.findByText('feedgrab x-so "openclaw"')).toBeInTheDocument();
    expect(screen.queryByText("[openclaw] missing Twitter login")).not.toBeInTheDocument();
  });

  it("keeps the realtime log panel pinned to the newest worker status", async () => {
    let workerEvent: ((event: FeedgrabWorkerEvent) => void) | undefined;
    const api = createTestApi({
      startFetch: vi.fn().mockResolvedValue([
        {
          id: "job-tweet",
          url: "https://x.com/GeekCatX/status/2070561078963216666",
          platform: "twitter",
          status: "running",
          outputDirectory: "D:\\Notes\\Feeds",
          createdAt: "2026-06-26T09:00:00.000Z"
        }
      ]),
      onWorkerEvent: vi.fn((callback) => {
        workerEvent = callback;
        return () => undefined;
      })
    });
    window.feedgrab = api;

    render(<App />);
    fireEvent.change(screen.getByLabelText("抓取目标（URL / 关键词 / 关键词组 / 账号）"), {
      target: { value: "https://x.com/GeekCatX/status/2070561078963216666" }
    });
    fireEvent.click(screen.getByRole("button", { name: "开始抓取" }));
    await waitFor(() => expect(api.startFetch).toHaveBeenCalled());

    const panel = screen.getByLabelText("实时日志");
    panel.scrollTop = 200;
    act(() => {
      workerEvent?.({
        id: "job-tweet",
        event: "done",
        method: "fetch",
        result: { fetched: 1, errors: 0 }
      });
    });

    expect(await screen.findByText("抓取完成")).toBeInTheDocument();
    await waitFor(() => expect(panel.scrollTop).toBe(0));
    const logMessages = screen.getAllByTestId("log-message").map((item) => item.textContent ?? "");
    expect(logMessages[0]).toBe("抓取完成");
  });

  it("shows running account fetch progress and streamed artifacts before completion", async () => {
    let workerEvent: ((event: FeedgrabWorkerEvent) => void) | undefined;
    const api = createTestApi({
      startFetch: vi.fn().mockResolvedValue([
        {
          id: "job-wechat-account",
          url: "feedgrab mpweixin-id 老码小张",
          target: "老码小张",
          targets: ["老码小张"],
          platform: "wechat",
          mode: "account",
          commandPreview: "feedgrab mpweixin-id 老码小张",
          status: "running",
          outputDirectory: "D:\\Notes\\Feeds",
          createdAt: "2026-06-26T09:00:00.000Z"
        }
      ]),
      onWorkerEvent: vi.fn((callback) => {
        workerEvent = callback;
        return () => undefined;
      })
    });
    window.feedgrab = api;

    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "微信公众号" }));
    fireEvent.change(screen.getByLabelText("抓取目标（URL / 关键词 / 关键词组 / 账号）"), {
      target: { value: "老码小张" }
    });
    fireEvent.click(screen.getByRole("button", { name: "开始抓取" }));
    await waitFor(() => expect(api.startFetch).toHaveBeenCalled());

    act(() => {
      workerEvent?.({
        id: "job-wechat-account",
        event: "progress",
        method: "fetch",
        stage: "fetch",
        message: "正在处理：第一篇文章",
        result: { index: 1, total: 860 }
      });
      workerEvent?.({
        id: "job-wechat-account",
        event: "artifact",
        method: "fetch",
        artifact: {
          kind: "markdown",
          path: "D:\\Notes\\Feeds\\mpweixin\\account\\老码小张\\first.md"
        }
      });
    });

    fireEvent.click(screen.getByText("任务"));
    expect(await screen.findByText("已保存 1 个 Markdown")).toBeInTheDocument();
    expect(screen.getByText("正在处理：第一篇文章")).toBeInTheDocument();
    expect(screen.getByText("D:\\Notes\\Feeds\\mpweixin\\account\\老码小张\\first.md")).toBeInTheDocument();

    fireEvent.click(screen.getByText("输出"));
    expect(await screen.findByText("first.md")).toBeInTheDocument();
    expect(screen.getByText("mpweixin")).toBeInTheDocument();
    const metrics = screen.getByLabelText("任务状态").textContent ?? "";
    expect(metrics).toContain("1运行");
    expect(metrics).toContain("0完成");
  });

  it("renders compact job queue rows without repeating the output root", async () => {
    let workerEvent: ((event: FeedgrabWorkerEvent) => void) | undefined;
    const api = createTestApi({
      startFetch: vi.fn().mockResolvedValue([
        {
          id: "job-compact",
          url: "https://mp.weixin.qq.com/s/compact",
          platform: "wechat",
          status: "running",
          outputDirectory: "D:\\Notes\\Feeds",
          createdAt: "2026-06-26T09:00:00.000Z"
        }
      ]),
      onWorkerEvent: vi.fn((callback) => {
        workerEvent = callback;
        return () => undefined;
      })
    });
    window.feedgrab = api;

    render(<App />);
    fireEvent.change(screen.getByLabelText("抓取目标（URL / 关键词 / 关键词组 / 账号）"), {
      target: { value: "https://mp.weixin.qq.com/s/compact" }
    });
    fireEvent.click(screen.getByRole("button", { name: "开始抓取" }));
    await waitFor(() => expect(api.startFetch).toHaveBeenCalled());

    act(() => {
      workerEvent?.({
        id: "job-compact",
        event: "artifact",
        method: "fetch",
        artifact: { kind: "markdown", path: "D:\\Notes\\Feeds\\mpweixin\\compact.md" }
      });
      workerEvent?.({
        id: "job-compact",
        event: "done",
        method: "fetch",
        result: { fetched: 1, errors: 0 }
      });
    });

    fireEvent.click(screen.getByText("任务"));
    const row = (await screen.findByText("https://mp.weixin.qq.com/s/compact")).closest(".job-row") as HTMLElement;
    expect(within(row).queryByText("D:\\Notes\\Feeds")).not.toBeInTheDocument();
    const summary = row.querySelector(".job-summary") as HTMLElement;
    expect(summary).not.toBeNull();
    expect(summary).toHaveTextContent("抓取完成");
    expect(summary).toHaveTextContent("已保存 1 个 Markdown");
    const actions = row.querySelector(".job-actions") as HTMLElement;
    expect(actions).not.toBeNull();
    expect(within(actions).getByText("完成")).toBeInTheDocument();
    expect(within(actions).getByRole("button", { name: "取消" })).toBeDisabled();
  });

  it("keeps completed worker status when done arrives before the job snapshot", async () => {
    let workerEvent: ((event: FeedgrabWorkerEvent) => void) | undefined;
    let resolveStartFetch: ((jobs: FetchJobSnapshot[]) => void) | undefined;
    const api = createTestApi({
      startFetch: vi.fn(
        () =>
          new Promise<FetchJobSnapshot[]>((resolve) => {
            resolveStartFetch = resolve;
          })
      ),
      onWorkerEvent: vi.fn((callback) => {
        workerEvent = callback;
        return () => undefined;
      })
    });
    window.feedgrab = api;

    const { container } = render(<App />);
    fireEvent.change(screen.getByLabelText("抓取目标（URL / 关键词 / 关键词组 / 账号）"), {
      target: { value: "https://x.com/GeekCatX/status/2070561078963216666" }
    });
    fireEvent.click(screen.getByRole("button", { name: "开始抓取" }));
    await waitFor(() => expect(api.startFetch).toHaveBeenCalled());

    act(() => {
      workerEvent?.({
        id: "job-race",
        event: "done",
        method: "fetch",
        result: { fetched: 1, errors: 0 }
      });
    });
    await screen.findByText("抓取完成");

    await act(async () => {
      resolveStartFetch?.([
        {
          id: "job-race",
          url: "https://x.com/GeekCatX/status/2070561078963216666",
          platform: "twitter",
          status: "running",
          outputDirectory: "D:\\Notes\\Feeds",
          createdAt: "2026-06-26T09:00:00.000Z"
        }
      ]);
    });

    const metricTexts = Array.from(container.querySelectorAll(".metric")).map((item) => item.textContent);
    expect(metricTexts).toContain("0运行");
    expect(metricTexts).toContain("1完成");
  });

  it("keeps output rows scoped to current-session artifacts and clears only the UI record", async () => {
    let workerEvent: ((event: FeedgrabWorkerEvent) => void) | undefined;
    const outputList = vi.fn().mockResolvedValue([
      {
        id: "old",
        title: "old.md",
        platform: "GitHub",
        markdownPath: "D:\\Notes\\GitHub\\old.md",
        attachments: [],
        createdAt: "2026-06-26T08:00:00.000Z"
      }
    ] satisfies OutputArtifact[]);
    const api = createTestApi({
      outputList,
      startFetch: vi.fn().mockResolvedValue([
        {
          id: "job-1",
          url: "https://github.com/iBigQiang/feedgrab",
          platform: "github",
          status: "running",
          outputDirectory: "D:\\Notes\\Feeds",
          createdAt: "2026-06-26T09:00:00.000Z"
        }
      ]),
      onWorkerEvent: vi.fn((callback) => {
        workerEvent = callback;
        return () => undefined;
      })
    });
    window.feedgrab = api;

    render(<App />);
    fireEvent.click(screen.getByText("输出"));

    expect(await screen.findByText("暂无输出")).toBeInTheDocument();
    expect(outputList).not.toHaveBeenCalled();

    fireEvent.click(screen.getByText("抓取"));
    fireEvent.change(screen.getByLabelText("抓取目标（URL / 关键词 / 关键词组 / 账号）"), {
      target: { value: "https://github.com/iBigQiang/feedgrab" }
    });
    fireEvent.click(screen.getByRole("button", { name: "开始抓取" }));
    await waitFor(() => expect(api.startFetch).toHaveBeenCalled());

    act(() => {
      workerEvent?.({
        id: "job-1",
        event: "artifact",
        method: "fetch",
        url: "https://github.com/iBigQiang/feedgrab",
        artifact: { kind: "markdown", path: "D:\\Notes\\Feeds\\GitHub\\feedgrab.md" }
      });
    });
    fireEvent.click(screen.getByText("输出"));

    const rows = await screen.findAllByRole("article");
    expect(within(rows[0]).getByText("#1")).toBeInTheDocument();
    expect(within(rows[0]).getByText("feedgrab.md")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "清空记录" }));
    expect(await screen.findByText("等待首个输出")).toBeInTheDocument();
    expect(api.openPath).not.toHaveBeenCalled();
  });

  it("exposes login refresh, global import, and per-platform detect/login/import actions", async () => {
    const now = new Date("2026-06-25T09:00:00.000Z").toISOString();
    const api = createTestApi({
      loginStatus: vi.fn().mockResolvedValue([
        {
          platform: "twitter",
          label: "X / Twitter",
          status: "connected",
          lastChecked: now,
          accountCount: 5,
          validCount: 4,
          expiredCount: 1,
          cookieCount: 28
        },
        { platform: "xhs", label: "小红书", status: "missing", lastChecked: now },
        { platform: "wechat", label: "微信公众号", status: "connected", lastChecked: now },
        { platform: "feishu", label: "飞书", status: "missing", lastChecked: now },
        { platform: "kdocs", label: "金山文档", status: "missing", lastChecked: now },
        { platform: "flowus", label: "FlowUs", status: "missing", lastChecked: now },
        { platform: "reddit", label: "Reddit", status: "missing", lastChecked: now },
        { platform: "zhihu", label: "知乎", status: "missing", lastChecked: now },
        { platform: "linuxdo", label: "LinuxDo", status: "connected", lastChecked: now },
        { platform: "idcflare", label: "IDCFlare", status: "missing", lastChecked: now },
        { platform: "zsxq", label: "知识星球", status: "missing", lastChecked: now },
        { platform: "github", label: "GitHub", status: "notRequired", lastChecked: now },
        { platform: "youtube", label: "YouTube", status: "notRequired", lastChecked: now },
        { platform: "bilibili", label: "Bilibili", status: "notRequired", lastChecked: now }
      ]),
      importLoginSessions: vi.fn().mockResolvedValue({
        ok: true,
        sourceDirectory: "D:\\AiCode\\feedgrab\\desktop\\sessions",
        imported: [{ source: "D:\\AiCode\\feedgrab\\desktop\\sessions\\x.json" }],
        skipped: [],
        disabled: [{ source: "D:\\Notes\\Feeds\\sessions\\x_6.json", reason: "missing_from_source" }],
        ignored: []
      })
    });
    window.feedgrab = api;

    render(<App />);
    fireEvent.click(screen.getByText("登录"));

    expect(await screen.findByRole("button", { name: "重新检测" })).toBeInTheDocument();
    expect(screen.getByText("飞书")).toBeInTheDocument();
    expect(screen.getByText("金山文档")).toBeInTheDocument();
    expect(screen.getByText("FlowUs")).toBeInTheDocument();
    expect(screen.getByText("Reddit")).toBeInTheDocument();
    expect(screen.getByText("知乎")).toBeInTheDocument();
    expect(screen.getByText("IDCFlare")).toBeInTheDocument();
    expect(screen.getByText("知识星球")).toBeInTheDocument();
    expect(screen.getByText("YouTube")).toBeInTheDocument();
    expect(screen.getByText("Bilibili")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "重新检测" }));
    await waitFor(() => expect(api.loginStatus).toHaveBeenCalledWith({ refresh: true }));
    expect(await screen.findByTestId("toast")).toHaveTextContent("已刷新全部平台登录态");

    fireEvent.click(screen.getByRole("button", { name: "导入本机登录态/安装目录 sessions" }));
    await waitFor(() => expect(api.importLoginSessions).toHaveBeenCalledWith());
    expect(await screen.findByTestId("toast")).toHaveTextContent("已完成登录态导入");
    expect(await screen.findByText("导入来源：D:\\AiCode\\feedgrab\\desktop\\sessions")).toBeInTheDocument();
    expect(screen.getByText("导入 1 / 跳过 0 / 停用 1 / 忽略 0")).toBeInTheDocument();

    const twitterRow = screen.getByText("X / Twitter").closest("article");
    expect(twitterRow).not.toBeNull();
    const row = within(twitterRow as HTMLElement);
    expect(row.getByText("5 个账号，本地有效 4 个，过期/异常 1 个")).toBeInTheDocument();
    fireEvent.click(row.getByRole("button", { name: "检测" }));
    await waitFor(() => expect(api.loginStatus).toHaveBeenCalledWith({ refresh: true, platforms: ["twitter"] }));
    await waitFor(() => expect(screen.getByTestId("toast")).toHaveTextContent("X / Twitter 登录态已刷新"));

    fireEvent.click(row.getByRole("button", { name: "登录" }));
    fireEvent.click(row.getByRole("button", { name: "导入" }));
    expect(api.loginPlatform).toHaveBeenCalledWith("twitter");
    expect(api.importLoginSessions).toHaveBeenCalledWith(undefined, "twitter");
  });

  it("keeps Reddit login actions visible when the worker returns an older platform list", async () => {
    const now = new Date("2026-06-25T09:00:00.000Z").toISOString();
    const api = createTestApi({
      loginStatus: vi
        .fn()
        .mockResolvedValueOnce([
          { platform: "twitter", label: "X / Twitter", status: "connected", lastChecked: now },
          { platform: "xhs", label: "小红书", status: "missing", lastChecked: now },
          { platform: "wechat", label: "微信公众号", status: "connected", lastChecked: now }
        ])
        .mockResolvedValue([
          {
            platform: "reddit",
            label: "Reddit",
            status: "connected",
            lastChecked: now,
            accountCount: 1,
            validCount: 1,
            expiredCount: 0,
            cookieCount: 4,
            sessionPath: "D:\\AiCode\\feedgrab\\desktop\\sessions\\reddit.json"
          }
        ]),
      loginPlatform: vi.fn().mockResolvedValue({ ok: true, platform: "reddit", message: "login started" }),
      importLoginSessions: vi.fn().mockResolvedValue({
        ok: true,
        sourceDirectory: "D:\\AiCode\\feedgrab\\desktop\\sessions",
        imported: [],
        skipped: [{ source: "D:\\AiCode\\feedgrab\\desktop\\sessions\\reddit.json", reason: "exists" }],
        disabled: [],
        ignored: []
      })
    });
    window.feedgrab = api;

    render(<App />);
    fireEvent.click(screen.getByText("登录"));

    const redditRow = (await screen.findByText("Reddit")).closest("article");
    expect(redditRow).not.toBeNull();
    const row = within(redditRow as HTMLElement);
    expect(row.getByText("等待检测")).toBeInTheDocument();

    fireEvent.click(row.getByRole("button", { name: "检测" }));
    await waitFor(() => expect(api.loginStatus).toHaveBeenCalledWith({ refresh: true, platforms: ["reddit"] }));
    expect(await screen.findByText("1 个账号，本地有效 1 个，过期/异常 0 个")).toBeInTheDocument();

    fireEvent.click(row.getByRole("button", { name: "登录" }));
    fireEvent.click(row.getByRole("button", { name: "导入" }));
    expect(api.loginPlatform).toHaveBeenCalledWith("reddit");
    expect(api.importLoginSessions).toHaveBeenCalledWith(undefined, "reddit");
  });

  it("shows login action feedback as a temporary toast instead of stacked page rows", async () => {
    render(<App />);
    fireEvent.click(screen.getByText("登录"));

    fireEvent.click(await screen.findByRole("button", { name: "重新检测" }));
    await waitFor(() => expect(screen.getByTestId("toast")).toHaveTextContent("已刷新全部平台登录态"));
    expect(screen.queryByText("已刷新全部平台登录态")?.closest(".login-action-message")).toBeNull();

    await act(async () => {
      await new Promise((resolve) => window.setTimeout(resolve, 2100));
    });
    await waitFor(() => expect(screen.queryByTestId("toast")).not.toBeInTheDocument());
  }, 8000);

  it("renders settings from schema with basic and platform tabs and saves typed values", async () => {
    const schema: SettingsSchema = {
      basic: [
        { name: "OUTPUT_DIR", label: "输出目录", type: "path", value: "D:\\Notes\\Feeds" },
        {
          name: "OBSIDIAN_VAULT",
          label: "Obsidian Vault",
          type: "path",
          value: "D:\\Notes\\Vault",
          description: "高优先级"
        },
        {
          name: "FEEDGRAB_DATA_DIR",
          label: "登录态和数据目录",
          type: "path",
          value: "D:\\feedgrab Desktop\\sessions"
        },
        { name: "DOWNLOAD_IMAGES", label: "下载图片", type: "boolean", value: true },
        { name: "FEEDGRAB_PROXY_ENABLED", label: "启用代理", type: "boolean", value: false },
        {
          name: "FEEDGRAB_PROXY_URL",
          label: "代理地址",
          type: "string",
          value: "",
          placeholder: "http://127.0.0.1:7890 或 socks5://127.0.0.1:7890"
        },
        { name: "FEEDGRAB_NO_PROXY", label: "不走代理地址", type: "string", value: "127.0.0.1,localhost" },
        { name: "CHROME_CDP_LOGIN", label: "登录时优先从 Chrome CDP 提取登录态", type: "boolean", value: false },
        { name: "CHROME_CDP_PORT", label: "Chrome CDP 端口", type: "number", value: 9222 },
        { name: "FORCE_REFETCH", label: "强制重新抓取", type: "boolean", value: false }
      ],
      platforms: [
        {
          id: "x",
          label: "X / Twitter",
          fields: [
            { name: "X_SEARCH_DAYS", label: "搜索天数", type: "number", value: 7 }
          ]
        },
        {
          id: "feishu",
          label: "文档平台",
          fields: [{ name: "FEISHU_APP_SECRET", label: "飞书 Secret", type: "secret", value: "[redacted]", secret: true }]
        },
        {
          id: "discourse",
          label: "Discourse论坛",
          fields: [
            {
              name: "LINUXDO_REPLY_MODE",
              label: "回复模式",
              type: "select",
              value: "author",
              options: [
                { label: "只看楼主", value: "author" },
                { label: "全部楼层", value: "all" }
              ]
            }
          ]
        },
        {
          id: "zsxq",
          label: "知识星球",
          fields: []
        },
        {
          id: "reddit",
          label: "Reddit",
          fields: [
            { name: "REDDIT_ENABLED", label: "启用 Reddit 抓取", type: "boolean", value: true },
            {
              name: "REDDIT_SEARCH_SORT",
              label: "帖子搜索排序",
              type: "select",
              value: "relevance",
              options: [
                { label: "相关性 relevance", value: "relevance" },
                { label: "热门 hot", value: "hot" }
              ]
            },
            {
              name: "REDDIT_SEARCH_TIME_RANGE",
              label: "帖子搜索时间范围",
              type: "select",
              value: "all",
              options: [
                { label: "所有时间 all", value: "all" },
                { label: "上周 week", value: "week" }
              ]
            },
            { name: "REDDIT_SEARCH_LIMIT", label: "帖子搜索结果数", type: "number", value: 10 }
          ]
        }
      ]
    };
    const chooseOutputDirectory = vi
      .fn()
      .mockResolvedValueOnce({ ok: true, path: "D:\\Notes\\Vault2" })
      .mockResolvedValueOnce({ ok: true, path: "D:\\feedgrab Desktop\\sessions2" });
    const api = createTestApi({
      settingsSchema: vi.fn().mockResolvedValue(schema),
      chooseOutputDirectory
    });
    window.feedgrab = api;

    render(<App />);
    fireEvent.click(screen.getByText("设置"));

    expect(await screen.findByRole("tab", { name: "基础设置" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getAllByLabelText("输出目录")).toHaveLength(1);
    const outputRow = screen.getByLabelText("输出目录").closest(".schema-setting-row") as HTMLElement;
    expect(within(outputRow).getByRole("button", { name: "选择" })).toBeInTheDocument();
    expect(screen.getByLabelText("Obsidian Vault")).toHaveDisplayValue("D:\\Notes\\Vault");
    expect(screen.getByText("高优先级")).toBeInTheDocument();
    const vaultRow = screen.getByLabelText("Obsidian Vault").closest(".schema-setting-row") as HTMLElement;
    fireEvent.click(within(vaultRow).getByRole("button", { name: "选择" }));
    await waitFor(() =>
      expect(api.chooseOutputDirectory).toHaveBeenCalledWith({ title: "选择 Obsidian Vault 目录" })
    );
    expect(screen.getByLabelText("Obsidian Vault")).toHaveDisplayValue("D:\\Notes\\Vault2");
    expect(screen.getByLabelText("登录态和数据目录")).toHaveDisplayValue("D:\\feedgrab Desktop\\sessions");
    const dataDirRow = screen.getByLabelText("登录态和数据目录").closest(".schema-setting-row") as HTMLElement;
    fireEvent.click(within(dataDirRow).getByRole("button", { name: "选择" }));
    await waitFor(() =>
      expect(api.chooseOutputDirectory).toHaveBeenCalledWith({ title: "选择登录态和数据目录" })
    );
    expect(screen.getByLabelText("登录态和数据目录")).toHaveDisplayValue("D:\\feedgrab Desktop\\sessions2");
    expect(screen.getByLabelText("下载图片")).toHaveAttribute("type", "checkbox");
    expect(screen.getByLabelText("启用代理")).toHaveAttribute("type", "checkbox");
    expect(screen.getByLabelText("代理地址")).toHaveAttribute(
      "placeholder",
      "http://127.0.0.1:7890 或 socks5://127.0.0.1:7890"
    );
    expect(screen.getByLabelText("不走代理地址")).toHaveDisplayValue("127.0.0.1,localhost");
    expect(screen.getByLabelText("Chrome CDP 与抓取控制")).toBeInTheDocument();
    expect(screen.getByLabelText("Chrome CDP 端口")).toHaveDisplayValue("9222");

    fireEvent.click(screen.getByLabelText("启用代理"));
    fireEvent.change(screen.getByLabelText("代理地址"), {
      target: { value: "http://user:password@127.0.0.1:7890" }
    });
    fireEvent.change(screen.getByLabelText("不走代理地址"), {
      target: { value: "127.0.0.1,localhost,::1" }
    });

    fireEvent.click(screen.getByRole("tab", { name: "平台设置" }));
    expect(screen.getByRole("tab", { name: "X / Twitter" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "文档平台" })).toBeInTheDocument();
    const platformTabs = screen.getByRole("tablist", { name: "平台设置菜单" });
    const platformTabLabels = within(platformTabs).getAllByRole("tab").map((tab) => tab.textContent ?? "");
    expect(platformTabLabels.indexOf("Reddit")).toBe(platformTabLabels.indexOf("知识星球") + 1);
    expect(screen.queryByLabelText("飞书 Secret")).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "关键词搜索" })).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("搜索天数"), { target: { value: "3" } });
    fireEvent.click(screen.getByRole("tab", { name: "文档平台" }));
    expect(screen.getByLabelText("飞书 Secret")).toHaveAttribute("type", "password");
    expect(screen.getByLabelText("飞书 Secret")).toHaveValue("[redacted]");
    fireEvent.click(screen.getByRole("tab", { name: "Discourse论坛" }));
    fireEvent.change(screen.getByLabelText("回复模式"), { target: { value: "all" } });
    fireEvent.click(screen.getByRole("tab", { name: "Reddit" }));
    expect(screen.getByRole("heading", { name: "单贴/评论抓取" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "帖子搜索" })).toBeInTheDocument();
    expect(screen.getByLabelText("帖子搜索排序")).toHaveValue("relevance");
    expect(screen.getByLabelText("帖子搜索时间范围")).toHaveValue("all");
    fireEvent.click(screen.getByRole("button", { name: "保存设置" }));

    await waitFor(() =>
      expect(api.settingsUpdate).toHaveBeenCalledWith({
        FEEDGRAB_PROXY_ENABLED: true,
        FEEDGRAB_PROXY_URL: "http://user:password@127.0.0.1:7890",
        FEEDGRAB_NO_PROXY: "127.0.0.1,localhost,::1",
        OBSIDIAN_VAULT: "D:\\Notes\\Vault2",
        FEEDGRAB_DATA_DIR: "D:\\feedgrab Desktop\\sessions2",
        X_SEARCH_DAYS: 3,
        LINUXDO_REPLY_MODE: "all"
      })
    );
  });

  it("keeps boolean settings false after save when refreshed schema returns string values", async () => {
    const api = createTestApi({
      settingsSchema: vi
        .fn()
        .mockResolvedValueOnce({
          basic: [{ name: "FEEDGRAB_PROXY_ENABLED", label: "启用代理", type: "boolean", value: "true" }],
          platforms: []
        })
        .mockResolvedValueOnce({
          basic: [{ name: "FEEDGRAB_PROXY_ENABLED", label: "启用代理", type: "boolean", value: "false" }],
          platforms: []
        }),
      settingsSnapshot: vi.fn().mockResolvedValue({
        outputDirectory: "D:\\Notes\\Feeds",
        concurrency: 1,
        downloadImages: true,
        localizeMedia: true,
        replyMode: "author"
      })
    });
    window.feedgrab = api;

    render(<App />);
    fireEvent.click(screen.getByText("设置"));
    const proxyToggle = await screen.findByLabelText("启用代理");
    expect(proxyToggle).toBeChecked();

    fireEvent.click(proxyToggle);
    expect(proxyToggle).not.toBeChecked();
    fireEvent.click(screen.getByRole("button", { name: "保存设置" }));

    await waitFor(() => expect(api.settingsUpdate).toHaveBeenCalledWith({ FEEDGRAB_PROXY_ENABLED: false }));
    await waitFor(() => expect(screen.getByLabelText("启用代理")).not.toBeChecked());
  });

  it("keeps fallback Obsidian Vault blank instead of copying the output directory", async () => {
    const api = createTestApi({
      settingsSnapshot: vi.fn().mockResolvedValue({
        outputDirectory: "D:\\feedgrab Desktop\\output",
        obsidianVault: "",
        effectiveOutputDirectory: "D:\\feedgrab Desktop\\output",
        concurrency: 1,
        downloadImages: true,
        localizeMedia: true,
        replyMode: "author"
      }),
      settingsSchema: vi.fn(() => new Promise<SettingsSchema>(() => undefined))
    });
    window.feedgrab = api;

    render(<App />);
    fireEvent.click(screen.getByText("设置"));

    await waitFor(() => expect(screen.getByLabelText("输出目录")).toHaveDisplayValue("D:\\feedgrab Desktop\\output"));
    expect(screen.getByLabelText("输出目录")).toHaveDisplayValue("D:\\feedgrab Desktop\\output");
    expect(screen.getByLabelText("Obsidian Vault")).toHaveDisplayValue("");
  });

  it("keeps OUTPUT_DIR and OBSIDIAN_VAULT raw values separate in settings schema", async () => {
    const api = createTestApi({
      settingsSnapshot: vi.fn().mockResolvedValue({
        outputDirectory: "D:\\feedgrab Desktop\\output",
        obsidianVault: "D:\\Notes\\Vault",
        effectiveOutputDirectory: "D:\\Notes\\Vault",
        concurrency: 1,
        downloadImages: true,
        localizeMedia: true,
        replyMode: "author"
      }),
      settingsSchema: vi.fn(() => new Promise<SettingsSchema>(() => undefined))
    });
    window.feedgrab = api;

    render(<App />);
    fireEvent.click(screen.getByText("设置"));

    expect(await screen.findByLabelText("输出目录")).toHaveDisplayValue("D:\\feedgrab Desktop\\output");
    expect(screen.getByLabelText("Obsidian Vault")).toHaveDisplayValue("D:\\Notes\\Vault");
  });

  it("ignores legacy developer output directory persisted in localStorage", async () => {
    window.localStorage.setItem("feedgrab.outputDirectory", "E:\\Obsidian\\Qiang_Obsidian\\inbox");
    const api = createTestApi({
      settingsSnapshot: vi.fn().mockResolvedValue({
        outputDirectory: "",
        concurrency: 1,
        downloadImages: true,
        localizeMedia: true,
        replyMode: "author"
      }),
      settingsSchema: vi.fn(() => new Promise<SettingsSchema>(() => undefined))
    });
    window.feedgrab = api;

    render(<App />);
    fireEvent.click(screen.getByText("设置"));

    expect(await screen.findByLabelText("输出目录")).toHaveDisplayValue("");
    expect(window.localStorage.getItem("feedgrab.outputDirectory")).toBeNull();
  });

  it("does not toggle platform boolean settings when clicking blank row space", async () => {
    const api = createTestApi({
      settingsSchema: vi.fn().mockResolvedValue({
        basic: [],
        platforms: [
          {
            id: "x",
            label: "X / Twitter",
            fields: [{ name: "X_SEARCH_SAVE_TWEETS", label: "保存单条推文 Markdown", type: "boolean", value: true }]
          }
        ]
      })
    });
    window.feedgrab = api;

    render(<App />);
    fireEvent.click(screen.getByText("设置"));
    fireEvent.click(await screen.findByRole("tab", { name: "平台设置" }));

    const checkbox = screen.getByLabelText("保存单条推文 Markdown");
    expect(checkbox).toBeChecked();
    const row = checkbox.closest(".schema-setting-row") as HTMLElement;
    fireEvent.click(row);
    expect(checkbox).toBeChecked();
    fireEvent.click(checkbox);
    expect(checkbox).not.toBeChecked();
  });

  it("does not start Chrome CDP just by opening settings or enabling the global login CDP setting", async () => {
    const api = createTestApi({
      settingsSchema: vi.fn().mockResolvedValue({
        basic: [
          { name: "CHROME_CDP_LOGIN", label: "登录时优先从 Chrome CDP 提取登录态", type: "boolean", value: false },
          { name: "CHROME_CDP_PORT", label: "Chrome CDP 端口", type: "number", value: 9222 },
          { name: "FORCE_REFETCH", label: "强制重新抓取", type: "boolean", value: false }
        ],
        platforms: []
      }),
      ensureChromeCdp: vi.fn().mockResolvedValue({
        ok: true,
        port: 9223,
        started: true,
        message: "已启动 Chrome CDP 并连接成功"
      })
    });
    window.feedgrab = api;

    render(<App />);
    fireEvent.click(screen.getByText("设置"));
    const checkbox = await screen.findByLabelText("登录时优先从 Chrome CDP 提取登录态");

    expect(api.ensureChromeCdp).not.toHaveBeenCalled();
    fireEvent.click(checkbox);
    expect(api.ensureChromeCdp).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "保存设置" }));
    await waitFor(() => expect(api.settingsUpdate).toHaveBeenCalledWith({ CHROME_CDP_LOGIN: true }));
    expect(api.ensureChromeCdp).not.toHaveBeenCalled();
  });

  it("keeps browser-preview fallback settings aligned with desktop platform groups", async () => {
    render(<App />);
    fireEvent.click(screen.getByText("设置"));

    fireEvent.click(await screen.findByRole("tab", { name: "平台设置" }));
    expect(screen.getByRole("tab", { name: "X / Twitter" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "小红书" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "微信公众号" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Discourse论坛" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Reddit" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "文档平台" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "视频播客" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "知乎" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "媒体 / API" })).toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "FlowUs" })).not.toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "GitHub" })).not.toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "YouTube" })).not.toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "B 站" })).not.toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "小宇宙" })).not.toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "喜马拉雅" })).not.toBeInTheDocument();
    expect(screen.getByLabelText("启用 GraphQL 深度抓取")).toHaveAttribute("type", "checkbox");

    fireEvent.click(screen.getByRole("tab", { name: "Reddit" }));
    expect(screen.getByLabelText("启用 Reddit 抓取")).toHaveAttribute("type", "checkbox");
    expect(screen.getByLabelText("评论最大条数")).toHaveDisplayValue("50");

    fireEvent.click(screen.getByRole("tab", { name: "媒体 / API" }));
    expect(screen.getByRole("heading", { name: "Gemini" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Groq 转录" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Telegram" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "AI / 转录" })).not.toBeInTheDocument();
  });

  it("refreshes settings schema after save so edited paths stay visible", async () => {
    const api = createTestApi({
      settingsSchema: vi
        .fn()
        .mockResolvedValueOnce({
          basic: [{ name: "FEEDGRAB_DATA_DIR", label: "登录态和数据目录", type: "path", value: "sessions" }],
          platforms: []
        })
        .mockResolvedValueOnce({
          basic: [
            {
              name: "FEEDGRAB_DATA_DIR",
              label: "登录态和数据目录",
              type: "path",
              value: "D:\\AiCode\\feedgrab\\sessions"
            }
          ],
          platforms: []
        }),
      settingsSnapshot: vi.fn().mockResolvedValue({
        outputDirectory: "D:\\Notes\\Feeds",
        concurrency: 1,
        downloadImages: true,
        localizeMedia: true,
        replyMode: "author"
      })
    });
    window.feedgrab = api;

    render(<App />);
    fireEvent.click(screen.getByText("设置"));
    const input = await screen.findByLabelText("登录态和数据目录");
    fireEvent.change(input, { target: { value: "D:\\AiCode\\feedgrab\\sessions" } });
    fireEvent.click(screen.getByRole("button", { name: "保存设置" }));

    await waitFor(() => expect(api.settingsUpdate).toHaveBeenCalledWith({ FEEDGRAB_DATA_DIR: "D:\\AiCode\\feedgrab\\sessions" }));
    expect(await screen.findByDisplayValue("D:\\AiCode\\feedgrab\\sessions")).toBeInTheDocument();
  });

  it("renders structured diagnostics without raw JSON notes", async () => {
    window.feedgrab = createTestApi({
      doctor: vi.fn().mockResolvedValue({
        python: "3.12.10",
        browser: "ready",
        network: "unknown",
        writableOutput: true,
        notes: ['{"name":"python","status":"ok"}'],
        checks: [
          { name: "python", label: "Python", status: "ok", message: "3.12.10" },
          { name: "node", label: "Node.js", status: "ok", message: "v22.15.1" },
          { name: "chromium", label: "Chromium", status: "ok", message: "145.0.0.0" },
          { name: "proxy_connectivity", label: "代理连通性", status: "warning", message: "代理未启用" }
        ]
      })
    });

    render(<App />);
    fireEvent.click(screen.getByText("诊断"));

    expect(await screen.findByText("Node.js")).toBeInTheDocument();
    expect(screen.getByText("Chromium")).toBeInTheDocument();
    expect(screen.getByText("代理连通性")).toBeInTheDocument();
    expect(screen.getByText("代理未启用")).toBeInTheDocument();
    expect(screen.queryByText(/"name":"python"/)).not.toBeInTheDocument();
  });

  it("offers install or update actions for repairable diagnostic warnings", async () => {
    const api = createTestApi({
      doctor: vi.fn().mockResolvedValue({
        python: "3.12.10",
        browser: "missing",
        network: "unknown",
        writableOutput: true,
        notes: [],
        checks: [
          { name: "python", label: "Python", status: "ok", message: "3.12.10" },
          {
            name: "import:playwright",
            label: "Playwright",
            status: "warning",
            message: "missing",
            repair: { id: "python-dependencies", label: "安装/更新", available: true }
          }
        ]
      }),
      repairDoctor: vi.fn().mockResolvedValue({ ok: true, action: "python-dependencies", message: "依赖已更新" })
    });
    window.feedgrab = api;

    render(<App />);
    fireEvent.click(screen.getByText("诊断"));

    expect(await screen.findByRole("button", { name: "安装/更新所有依赖" })).toBeInTheDocument();
    const playwrightRow = screen.getByText("Playwright").closest("article");
    expect(playwrightRow).not.toBeNull();
    fireEvent.click(within(playwrightRow as HTMLElement).getByRole("button", { name: "安装/更新" }));

    await waitFor(() => expect(api.repairDoctor).toHaveBeenCalledWith("import:playwright"));
    expect(await screen.findByTestId("toast")).toHaveTextContent("依赖已更新");
  });
});

function createTestApi(overrides: Partial<FeedgrabIpcApi> = {}): FeedgrabIpcApi {
  const now = new Date("2026-06-25T09:00:00.000Z").toISOString();
  return {
    ping: vi.fn().mockResolvedValue({ ok: true, worker: "mock" }),
    detectPlatform: vi.fn().mockResolvedValue("web"),
    startFetch: vi.fn().mockResolvedValue([]),
    cancelJob: vi.fn(),
    doctor: vi.fn().mockResolvedValue({
      python: "mock",
      browser: "mock",
      network: "disabled",
      writableOutput: true,
      notes: []
    }),
    settingsSnapshot: vi.fn().mockResolvedValue({
      outputDirectory: INSTALL_OUTPUT_DIR,
      obsidianVault: "",
      effectiveOutputDirectory: INSTALL_OUTPUT_DIR,
      concurrency: 1,
      downloadImages: true,
      localizeMedia: true,
      replyMode: "author"
    }),
    settingsSchema: vi.fn().mockResolvedValue({ basic: [], platforms: [] }),
    settingsUpdate: vi.fn().mockResolvedValue({ ok: true, updated: [] }),
    ensureChromeCdp: vi.fn().mockResolvedValue({
      ok: true,
      port: 9222,
      started: false,
      message: "Chrome CDP 已连接"
    }),
    loginStatus: vi.fn().mockResolvedValue([
      { platform: "twitter", label: "X / Twitter", status: "missing", lastChecked: now },
      { platform: "reddit", label: "Reddit", status: "missing", lastChecked: now },
      { platform: "github", label: "GitHub", status: "notRequired", lastChecked: now }
    ]),
    importLoginSessions: vi.fn().mockResolvedValue({ ok: true, imported: [], skipped: [], disabled: [], ignored: [] }),
    loginPlatform: vi.fn().mockResolvedValue({ ok: true, platform: "twitter", message: "login started" }),
    repairDoctor: vi.fn().mockResolvedValue({ ok: true, action: "all", message: "依赖已更新" }),
    outputList: vi.fn().mockResolvedValue([]),
    openPath: vi.fn().mockResolvedValue({ ok: true }),
    chooseOutputDirectory: vi.fn().mockResolvedValue({ ok: true, path: INSTALL_OUTPUT_DIR }),
    fetchRemoteMarkdown: vi.fn().mockResolvedValue({ ok: false, error: "offline" }),
    onWorkerEvent: vi.fn(() => () => undefined),
    ...overrides
  };
}
