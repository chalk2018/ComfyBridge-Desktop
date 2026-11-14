"""把资产目录中的两个单文件 HTML 转换为便于阅读的纯文本 / 独立 JS。

仅使用标准库，输出全部写入项目目录内，作为开发阶段的阅读辅助。

Usage:
    python tools/extract_docs.py
"""

from __future__ import annotations

import html as html_lib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REF = ROOT / "_reference"


# 需要转换为换行的块级标签（保留文本之间的结构，便于逐条核对需求）
BLOCK_TAGS = (
    "p|div|li|ul|ol|tr|h1|h2|h3|h4|h5|section|article|blockquote|"
    "table|thead|tbody|figure|figcaption|nav|aside|main|header|footer|"
    "details|summary|pre|hr|br"
)


def strip_html(source: str, *, keep_svg_text: bool = False) -> str:
    """把 HTML 正文近似还原为纯文本。

    Args:
        source: 原始 HTML 文本
        keep_svg_text: 是否保留 SVG 内的文字（图形说明可能有价值）

    Returns:
        去标签、去样式/脚本后的可读文本
    """
    text = re.sub(r"<style\b[^>]*>.*?</style>", "\n", source, flags=re.S | re.I)
    text = re.sub(r"<script\b[^>]*>.*?</script>", "\n", text, flags=re.S | re.I)
    if not keep_svg_text:
        text = re.sub(r"<svg\b[^>]*>.*?</svg>", "\n[SVG 图形]\n", text, flags=re.S | re.I)
    text = re.sub(r"<!--.*?-->", "\n", text, flags=re.S)
    text = re.sub(rf"</?(?:{BLOCK_TAGS})\b[^>]*>", "\n", text, flags=re.I)
    text = re.sub(r"</?(?:td|th)\b[^>]*>", " | ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = html_lib.unescape(text)
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    out: list[str] = []
    blank = False
    for line in lines:
        if not line:
            if not blank and out:
                out.append("")
            blank = True
            continue
        blank = False
        out.append(line)
    return "\n".join(out).strip() + "\n"


def extract_scripts(source: str) -> str:
    """提取 HTML 中所有 <script> 内容，便于阅读原型的交互逻辑。"""
    blocks = re.findall(r"<script\b[^>]*>(.*?)</script>", source, flags=re.S | re.I)
    return "\n\n".join(
        f"// ===== script #{i} =====\n{block.strip()}"
        for i, block in enumerate(blocks, 1)
        if block.strip()
    ) + "\n"


def main() -> None:
    REF.mkdir(parents=True, exist_ok=True)
    for name, stem in (("requirements.html", "requirements"), ("prototype.html", "prototype")):
        source = (REF / name).read_text(encoding="utf-8", errors="replace")
        (REF / f"{stem}.txt").write_text(strip_html(source), encoding="utf-8")
        scripts = extract_scripts(source)
        if scripts.strip():
            (REF / f"{stem}.scripts.txt").write_text(scripts, encoding="utf-8")
        print(f"{name}: text={len(strip_html(source))} chars, scripts={len(scripts)} chars")


if __name__ == "__main__":
    main()
