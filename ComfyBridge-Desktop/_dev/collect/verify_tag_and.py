"""验收多标签「全部满足 / 任一即可」：复现 音频+参考+故事板 的筛选问题。"""
from __future__ import annotations

import base64
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "tests"))
import ui_e2e_test as base  # noqa: E402

SHOT = ROOT / "_dev" / "_shots500"
API = "http://127.0.0.1:8190/api/prompt-templates"


def shot(cdp, name):
    (SHOT / name).write_bytes(base64.b64decode(cdp.call("Page.captureScreenshot", {"format": "png"})["data"]))
    print("  shot", name)


def cnt(cdp):
    return cdp.js("document.querySelector('#ptCount')?.textContent || ''")


def pick_cat(cdp, old, new):
    cdp.js("document.querySelector('#ptCatFilter').click()")
    cdp.wait("!!document.querySelector('[data-afid]')", 10)
    if old:
        cdp.js(f"document.querySelector('[data-afid=\"{old}\"]').click()")
    if new:
        cdp.js(f"document.querySelector('[data-afid=\"{new}\"]').click()")
    cdp.js("document.querySelector('[data-afdone]').click()")
    time.sleep(1.6)


def pick_tag(cdp, tid, kw):
    cdp.js("document.querySelector('#ptTagFilter').click()")
    cdp.wait("!!document.querySelector('[data-afq]')", 10)
    cdp.js(f"(()=>{{const q=document.querySelector('[data-afq]'); q.value={kw!r}; q.dispatchEvent(new Event('input',{{bubbles:true}}));}})()")
    time.sleep(0.4)
    cdp.js(f"document.querySelector('[data-afid=\"{tid}\"]').click()")
    time.sleep(0.3)


def main():
    d = httpx.get(API, timeout=180).json()["data"]
    ref = next(t["id"] for t in d["tags"] if t["name"] == "参考")
    board = next(t["id"] for t in d["tags"] if t["name"] == "故事板")
    def num(t):
        return sum(1 for x in d["templates"] if x["category"] == t and ref in x.get("tags", []) and board in x.get("tags", []))
    print("接口交集：音频", num("audio"), "视频", num("video"), "图片", num("image"))

    base.api("/api/ui", "POST", {"patch": {"route": "admin", "adminPage": "prompts", "promptDock": False}})
    proc, cdp = base.start_browser()
    try:
        cdp.call("Emulation.setDeviceMetricsOverride",
                 {"width": 1440, "height": 1000, "deviceScaleFactor": 1, "mobile": False})
        cdp.call("Page.navigate", {"url": base.BASE + f"/?v={int(time.time())}#admin/prompts"})
        cdp.wait("document.querySelectorAll('.pt-card').length>2", 40)
        time.sleep(1.8)

        pick_cat(cdp, "", "audio")
        pick_tag(cdp, ref, "参考")
        pick_tag(cdp, board, "故事板")
        print("模式按钮：", cdp.js("document.querySelector('[data-afmode]')?.textContent"))
        shot(cdp, "13-tag-and-pop.png")
        cdp.js("document.querySelector('[data-afdone]').click()")
        time.sleep(1.8)
        got_all = cnt(cdp)
        print("音频+参考+故事板（全部满足）：", got_all, "| 筛选框：", cdp.js("document.querySelector('#ptTagCur').textContent"))
        shot(cdp, "14-tag-and-zero.png")

        cdp.js("document.querySelector('#ptTagFilter').click()")
        cdp.wait("!!document.querySelector('[data-afmode]')", 10)
        cdp.js("document.querySelector('[data-afmode]').click()")
        print("切换后模式：", cdp.js("document.querySelector('[data-afmode]').textContent"))
        cdp.js("document.querySelector('[data-afdone]').click()")
        time.sleep(1.8)
        got_any = cnt(cdp)
        print("音频+参考+故事板（任一即可）：", got_any, "| 筛选框：", cdp.js("document.querySelector('#ptTagCur').textContent"))
        shot(cdp, "15-tag-any.png")

        # 切换回全部满足，改成视频分类
        cdp.js("document.querySelector('#ptTagFilter').click()")
        cdp.wait("!!document.querySelector('[data-afmode]')", 10)
        cdp.js("document.querySelector('[data-afmode]').click()")
        cdp.js("document.querySelector('[data-afdone]').click()")
        time.sleep(1)
        pick_cat(cdp, "audio", "video")
        got_video = cnt(cdp)
        print("视频+参考+故事板（全部满足）：", got_video)

        # 小窗同步
        base.api("/api/ui", "POST", {"patch": {"promptDock": True}})
        cdp.call("Page.navigate", {"url": base.BASE + f"/?d={int(time.time())}#studio"})
        cdp.wait("!!document.querySelector('.pt-dock') && !document.querySelector('.pt-dock').hidden", 40)
        cdp.wait("document.querySelectorAll('.pt-dcard').length>0", 30)
        time.sleep(1.2)
        cdp.js("document.querySelector('[data-dcat=\"audio\"]').click()")
        cdp.js("document.querySelector('[data-dtagfilter]').click()")
        cdp.wait("!!document.querySelector('[data-tfq]')", 10)
        for tid, kw in ((ref, "参考"), (board, "故事板")):
            cdp.js(f"(()=>{{const q=document.querySelector('[data-tfq]'); q.value={kw!r}; q.dispatchEvent(new Event('input',{{bubbles:true}}));}})()")
            time.sleep(0.35)
            cdp.js(f"document.querySelector('[data-tfid=\"{tid}\"]').click()")
            time.sleep(0.25)
        cdp.js("document.querySelector('[data-tfdone]').click()")
        time.sleep(1.2)
        dock_all = cdp.js("document.querySelector('#ptDockCount').textContent")
        print("小窗（全部满足）：", dock_all, "| 模式：", cdp.js("document.querySelector('[data-tfmode]')?.textContent"))
        cdp.js("document.querySelector('[data-dtagfilter]').click()")
        cdp.wait("!!document.querySelector('[data-tfmode]')", 10)
        cdp.js("document.querySelector('[data-tfmode]').click()")
        cdp.js("document.querySelector('[data-tfdone]').click()")
        time.sleep(1.2)
        dock_any = cdp.js("document.querySelector('#ptDockCount').textContent")
        print("小窗（任一即可）：", dock_any)
        shot(cdp, "16-dock-and.png")

        errs = sum(1 for e in cdp.events if e.get("method") == "Runtime.exceptionThrown")
        ok = ("0 条" in got_all and "160 条" in got_any and "45 条" in got_video
              and "0 条" in dock_all and "160 条" in dock_any and errs == 0)
        print("验收", "PASS" if ok else "FAIL", "| JS 异常", errs)
    finally:
        base.api("/api/ui", "POST", {"patch": {"promptDock": False}})
        cdp.close()
        proc.terminate()


if __name__ == "__main__":
    main()
