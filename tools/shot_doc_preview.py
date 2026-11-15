"""把生成的 1.02 文档渲染成几张预览图，用来目检排版（不是交付物）。

用法：.venv/Scripts/python.exe tools/shot_doc_preview.py
输出：_reference/docs/_docview/*.png
"""
from __future__ import annotations

import base64
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))

from ui_e2e_test import CDP, start_browser  # noqa: E402

DOC = ROOT.parent / "ComfyBridge-需求与功能文档-1.02.html"
OUT = ROOT / "_reference" / "docs" / "_docview"

# 锚点 → 文件名。用锚点而不是固定滚动像素，改了内容也不会拍偏。
SHOTS = [
    ("sec-12", "v1_ch03_studio"),
    ("sec-12", "v1b_ch03_studio2"),
    ("sec-12b", "v2_ch03_sheet"),
    ("sec-12c", "v3_gallery"),
    ("sec-13", "v4_admin"),
    ("ch-08", "v5_mobile"),
    ("sec-46", "v6_mobile_check"),
]


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    proc, cdp = start_browser()
    try:
        cdp.call("Emulation.setDeviceMetricsOverride", {
            "width": 1440, "height": 1180, "deviceScaleFactor": 2, "mobile": False})
        # 从 http 页面跳 file:// 会被浏览器拦，必须用 Page.navigate 直接导航
        cdp.call("Page.navigate", {"url": DOC.as_uri()})
        cdp.pause(2000)
        # 19 张 base64 大图是异步解码的，图没加载完就滚，滚完图撑开布局会把位置顶跑。
        # 等所有 img.complete 再滚，滚完再校一次。
        cdp.wait("[...document.images].every(i => i.complete && i.naturalWidth > 0)", 20)
        cdp.pause(500)
        # 文档 CSS 里有 scroll-behavior:smooth，滚动是动画；截图会拍在动画中途。
        # 这里直接关掉平滑滚动，让 scrollIntoView 一次到位。
        cdp.js("document.documentElement.style.scrollBehavior='auto'; true")
        print("页面标题：", cdp.js("document.title"), "| 有 sec-12：", cdp.js("!!document.getElementById('sec-12')"))

        for anchor, name in SHOTS:
            # IIFE 包一层：Runtime.evaluate 的顶层 const 会跨调用复用，直接写 const 会重复声明报错
            cdp.js(f"(() => {{ const el = document.getElementById({anchor!r});"
                   f" if (el) el.scrollIntoView({{block:'start'}}); return true; }})()")
            cdp.pause(400)
            cdp.js(f"(() => {{ const el = document.getElementById({anchor!r});"
                   f" if (el) el.scrollIntoView({{block:'start'}}); return true; }})()")
            cdp.pause(300)
            shot = cdp.call("Page.captureScreenshot", {"format": "png"})
            p = OUT / f"{name}.png"
            p.write_bytes(base64.b64decode(shot["data"]))
            print(f"{p.name:26s} {p.stat().st_size // 1024} KB")

        # 顺手量一下有没有横向溢出（图片超过版心会把整页撑宽）
        wide = cdp.js("""(() => ({
            sw: document.documentElement.scrollWidth,
            cw: document.documentElement.clientWidth,
            imgs: [...document.images].map(i => Math.round(i.getBoundingClientRect().width)).slice(0, 30),
        }))()""")
        print("宽度检查：", wide)
    finally:
        cdp.close()
        proc.terminate()
    return 0


if __name__ == "__main__":
    sys.exit(main())
