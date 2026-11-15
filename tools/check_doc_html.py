"""检查 1.02 文档的 HTML 标签是否配对（生成后自检用）。"""

import pathlib
import re
import sys

TAGS = ["section", "figure", "table", "thead", "tbody", "tr", "td", "th",
        "svg", "div", "blockquote", "ul", "li", "p", "h1", "h2", "h3", "h4"]


def check(path: pathlib.Path) -> int:
    s = path.read_text(encoding="utf-8")
    print(f"== {path.name}  {len(s):,} 字符")
    bad = 0
    for tag in TAGS:
        o = len(re.findall(rf"<{tag}[ >]", s))
        c = len(re.findall(rf"</{tag}>", s))
        mark = "" if o == c else "   <<< 不配对"
        if o != c:
            bad += 1
        print(f"   {tag:11}{o:6} / {c:6}{mark}")
    return bad


if __name__ == "__main__":
    root = pathlib.Path(__file__).resolve().parents[2]
    total = 0
    for name in ("ComfyBridge-需求与功能文档.html", "ComfyBridge-需求与功能文档-1.02.html"):
        total += check(root / name)
    print("结论：", "全部配对" if total == 0 else f"{total} 个标签不配对")
    sys.exit(0 if total == 0 else 1)
