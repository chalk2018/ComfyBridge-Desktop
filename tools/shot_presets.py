"""补拍一张桌面版「已实测组合」表格。

这张表在页面很下面，而且要等提示词库拉回来才渲染，所以必须「先进页面 → 等 .presets
出现 → 滚到它 → 按元素裁切」，不能靠整页截图再切。

用法：.venv/Scripts/python.exe tools/shot_presets.py
输出：_reference/docs/d_admin_sheet_presets.png（再用 prep_doc_images.py 压）
"""
from __future__ import annotations

import base64
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))

from ui_e2e_test import CDP, start_browser  # noqa: E402


def main() -> int:
    proc, cdp = start_browser()
    try:
        # 视口直接给到 4200 高：页面每秒会重绘一次，重绘会把滚动位置弹回顶部，
        # 所以「滚到元素再截」永远截到空白。不滚，把整页一次性画进视口里最稳。
        cdp.call("Emulation.setDeviceMetricsOverride", {
            "width": 1440, "height": 5600, "deviceScaleFactor": 2, "mobile": False})
        cdp.call("Network.enable")
        cdp.call("Network.setCacheDisabled", {"cacheDisabled": True})
        cdp.js("location.href='http://127.0.0.1:8190/#admin/sheet'")
        cdp.js("location.reload()")
        cdp.pause(2400)

        ok = cdp.wait("!!document.querySelector('.presets')", 15)
        if not ok:
            raise SystemExit("!! 找不到 .presets（提示词库没渲染出来）")
        cdp.pause(400)
        box = cdp.js("""(() => {
            const t = document.querySelector('.presets');
            const panel = t.closest('.panel') || t;
            const r = panel.getBoundingClientRect();
            const rows = t.querySelectorAll('tbody tr').length;
            return {x: Math.max(0, Math.round(r.left)), y: Math.max(0, Math.round(r.top)),
                    w: Math.round(r.width), h: Math.round(r.height), rows};
        })()""")
        print("量到：", box)
        if not box or box["w"] < 200 or box["y"] < 0:
            raise SystemExit("!! 量到的表格尺寸不合理")
        shot = cdp.call("Page.captureScreenshot", {
            "format": "png",
            "clip": {"x": box["x"], "y": box["y"], "width": box["w"], "height": box["h"], "scale": 1},
        })
        out = ROOT / "_reference" / "docs" / "d_admin_sheet_presets.png"
        out.write_bytes(base64.b64decode(shot["data"]))
        print(f"已保存 {out.relative_to(ROOT)}  {out.stat().st_size // 1024} KB")
    finally:
        cdp.close()
        proc.terminate()
    return 0


if __name__ == "__main__":
    sys.exit(main())
