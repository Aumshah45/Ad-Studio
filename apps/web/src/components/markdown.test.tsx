import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Markdown } from "./markdown";

describe("Markdown", () => {
  it("strips scripts, event handlers and javascript: links", () => {
    const md = [
      "# Title",
      "<script>alert(1)</script>",
      '<img src="x" onerror="alert(1)">',
      "[bad](javascript:alert(1)) [good](https://example.com)",
    ].join("\n\n");
    const { container } = render(<Markdown>{md}</Markdown>);
    expect(container.querySelector("script")).toBeNull();
    expect(container.innerHTML).not.toContain("onerror");
    expect(container.innerHTML).not.toContain("javascript:");
    const good = container.querySelector('a[href="https://example.com"]');
    expect(good).toHaveAttribute("target", "_blank");
    expect(good).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("renders GFM tables", () => {
    const { container } = render(<Markdown>{"| a | b |\n|---|---|\n| 1 | 2 |"}</Markdown>);
    expect(container.querySelector("table")).not.toBeNull();
  });
});
