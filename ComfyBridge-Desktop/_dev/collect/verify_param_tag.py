"""在真实浏览器里验收「参考」标签：标签筛选与搜索框都能筛出 503 条。"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT / "tests"))
import ui_e2e_test as base  # noqa: E402

SHOT = ROOT / "_dev" / "_shots500"
SHOT.mkdir(exist_ok=True)
API = "http://127.0.0.1:8190/api/prompt-templates"


def count(cdp):
    return cdp.js("document.querySelector('#ptCount')?.textContent || ''")


def main() -> None:
    cdp_http = httpx.Client(timeout=180)
    tag = next(t for t in cdp_http.get(f"{API}/tags").json()["data"] if t["name"] == "参考")
    tpls = cdp_http.get(API).json()["data"]["templates"]
    want = sum(1 for t in tpls if t.get("media"))
    print("接口：带素材", want, "标签 id", tag["id"])

    base.api("/api/ui", "POST", {"patch": {"route": "admin", "adminPage": "prompts", "promptDock": False}})
    proc, cdp = base.start_browser()
    try:
        cdp.call("Emulation.setDeviceMetricsOverride",
                 {"width": 1440, "height": 1060, "deviceScaleFactor": 1, "mobile": False})
        cdp.call("Page.navigate", {"url": base.BASE + f"/?v={int(time.time())}#admin/prompts"})
        cdp.wait("document.querySelectorAll('.pt-card').length>2", 40)
        time.sleep(2)
        print("初始：", count(cdp))

        # 1) 标签筛选
        cdp.js("document.querySelector('#ptTagFilter').click()")
        cdp.wait("!!document.querySelector('.pt-tagfilter-pop')", 10)
        cdp.js(f"(()=>{{const b=document.querySelector('.pt-tagfilter-pop [data-afid=\"{tag['id']}\"]'); if(b)b.click();}})()")
        time.sleep(0.6)
        cdp.js("document.querySelector('[data-afdone]')?.click()")
        cdp.wait("!!document.querySelector('#ptTagCur')", 5)
        time.sleep(2.5)
        got1 = count(cdp)
        print("标签筛选：", got1, "| 选择框：", cdp.js("document.querySelector('#ptTagCur').textContent"))
        (SHOT / "08-param-filter.png").write_bytes(
            base64.b64decode(cdp.call("Page.captureScreenshot", {"format": "png"})["data"]))

        # 2) 清空后直接搜索「参数」
        cdp.js("(()=>{const b=document.querySelector('#ptTagFilter'); if(b)b.click();})()")
        cdp.wait("!!document.querySelector('.pt-tagfilter-pop')", 10)
        cdp.js("document.querySelector('[data-afclear]')?.click()")
        cdp.js("document.querySelector('[data-afdone]')?.click()")
        time.sleep(1)
        cdp.js("(()=>{const q=document.querySelector('#ptQ'); q.value='参考'; q.dispatchEvent(new Event('input',{bubbles:true}));})()")
        time.sleep(2.5)
        got2 = count(cdp)
        print("搜索参考：", got2)
        (SHOT / "09-param-search.png").write_bytes(
            base64.b64decode(cdp.call("Page.captureScreenshot", {"format": "png"})["data"]))

        errs = sum(1 for e in cdp.events if e.get("method") == "Runtime.exceptionThrown")
        print("JS 异常", errs)
        ok = all(str(want) in s for s in (got1, got2))
        print("验收", "PASS" if ok and errs == 0 else "FAIL", f"(期望 {want})")
    finally:
        cdp.close()
        proc.terminate()


if __name__ == "__main__":
    import base64  # noqa: E402
    main()
