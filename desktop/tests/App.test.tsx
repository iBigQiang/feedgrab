import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { App } from "../renderer/src/App";

describe("App", () => {
  it("renders the desktop workspace and completes a mock worker fetch from pasted URLs", async () => {
    render(<App />);

    expect(screen.getByRole("heading", { name: "抓取工作台" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "开始抓取" })).toBeInTheDocument();
    expect(screen.getByText("诊断")).toBeInTheDocument();
    await screen.findByText("浏览器测试 mock worker 已连接。");

    fireEvent.change(screen.getByLabelText("内容链接"), {
      target: { value: "https://example.com/a\nhttps://example.com/b" }
    });
    fireEvent.click(screen.getByRole("button", { name: "开始抓取" }));

    expect(screen.getAllByText(/example.com/).length).toBeGreaterThan(0);
    await screen.findByText("worker 已接收 2 条链接，输出到 D:\\Notes\\Feeds");
    await screen.findByText("抓取完成");
    fireEvent.click(screen.getByRole("button", { name: "输出" }));
    expect(await screen.findByText("mock.md")).toBeInTheDocument();
  });
});
