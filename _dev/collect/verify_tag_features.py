"""验收：标签改名「参考」、下拉检索、包含/排除筛选（管理页 + 速取小窗）。"""
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


def main():
    d = httpx.get(API, timeout=180).json()["data"]
    tag = next(t for t in d["tags"] if t["name"] == "参考")
    want = sum(1 for t in d["templates"] if t.get("media"))
    total = len(d["templates"])
    print("接口：总", total, "带参考素材", want, "标签", tag)

    base.api("/api/ui", "POST", {"patch": {"route": "admin", "adminPage": "prompts", "promptDock": False}})
    proc, cdp = base.start_browser()
    try:
        cdp.call("Emulation.setDeviceMetricsOverride",
                 {"width": 1440, "height": 1000, "deviceScaleFactor": 1, "mobile": False})
        cdp.call("Page.navigate", {"url": base.BASE + f"/?v={int(time.time())}#admin/prompts"})
        cdp.wait("document.querySelectorAll('.pt-card').length>2", 40)
        time.sleep(2)

        # A. 下拉检索 + 包含
        cdp.js("document.querySelector('#ptTagFilter').click()")
        cdp.wait("!!document.querySelector('[data-afq]')", 10)
        cdp.js("(()=>{const q=document.querySelector('[data-afq]'); q.value='参考'; q.dispatchEvent(new Event('input',{bubbles:true}));})()")
        time.sleep(0.5)
        rows = cdp.js("[...document.querySelectorAll('.pt-tagfilter-item span')].map(x=>x.textContent)")
        print("A 下拉输入「参考」→", rows)
        cdp.js(f"document.querySelector('[data-afid=\"{tag['id']}\"]').click()")
        time.sleep(0.5)
        shot(cdp, "10-tag-search-include.png")
        cdp.js("document.querySelector('[data-afdone]').click()")
        time.sleep(2)
        got_inc = cnt(cdp)
        print("A 包含后：", got_inc, "| 筛选框：", cdp.js("document.querySelector('#ptTagCur').textContent"))

        # B. 排除（不含「参考」）
        cdp.js("document.querySelector('#ptTagFilter').click()")
        cdp.wait("!!document.querySelector('[data-afno]')", 10)
        cdp.js("(()=>{const q=document.querySelector('[data-afq]'); q.value='参考'; q.dispatchEvent(new Event('input',{bubbles:true}));})()")
        time.sleep(0.4)
        cdp.js(f"document.querySelector('[data-afno=\"{tag['id']}\"]').click()")
        time.sleep(0.5)
        shot(cdp, "11-tag-exclude.png")
        cdp.js("document.querySelector('[data-afdone]').click()")
        time.sleep(2)
        got_exc = cnt(cdp)
        print("B 排除后：", got_exc, "| 筛选框：", cdp.js("document.querySelector('#ptTagCur').textContent"))
        shot(cdp, "11b-excluded-list.png")
        first_chips = cdp.js("[...document.querySelectorAll('.pt-card .pt-chip')].slice(0,6).map(x=>x.textContent.trim())")
        print("B 首卡标签：", first_chips)

        # C. 清空 + 搜索框直接搜标签名
        cdp.js("document.querySelector('#ptTagFilter').click()")
        cdp.wait("!!document.querySelector('[data-afclear]')", 10)
        cdp.js("document.querySelector('[data-afclear]').click()")
        cdp.js("document.querySelector('[data-afdone]').click()")
        time.sleep(1)
        cdp.js("(()=>{const q=document.querySelector('#ptQ'); q.value='参考'; q.dispatchEvent(new Event('input',{bubbles:true}));})()")
        time.sleep(2)
        got_q = cnt(cdp)
        print("C 搜索「参考」：", got_q)

        # D. 速取小窗：检索 + 排除
        base.api("/api/ui", "POST", {"patch": {"promptDock": True}})
        cdp.call("Page.navigate", {"url": base.BASE + f"/?d={int(time.time())}#studio"})
        cdp.wait("!!document.querySelector('.pt-dock') && !document.querySelector('.pt-dock').hidden", 40)
        cdp.wait("document.querySelectorAll('.pt-dcard').length>0", 30)
        time.sleep(1.5)
        cdp.js("document.querySelector('[data-dtagfilter]').click()")
        cdp.wait("!!document.querySelector('[data-tfq]')", 10)
        cdp.js("(()=>{const q=document.querySelector('[data-tfq]'); q.value='参考'; q.dispatchEvent(new Event('input',{bubbles:true}));})()")
        time.sleep(0.5)
        drows = cdp.js("[...document.querySelectorAll('.pt-tagfilter-item span')].map(x=>x.textContent)")
        print("D 小窗输入「参考」→", drows)
        cdp.js(f"document.querySelector('[data-tfno=\"{tag['id']}\"]').click()")
        time.sleep(1)
        print("D 小窗排除后：", cdp.js("document.querySelector('#ptDockCount').textContent"),
              "|", cdp.js("document.querySelector('.pt-tagfilter-btn').textContent.trim()"))
        shot(cdp, "12-dock-exclude.png")

        errs = sum(1 for e in cdp.events if e.get("method") == "Runtime.exceptionThrown")
        qn = int(got_q.split('/')[1].split('条')[0].strip())
        ok = ("参考" in rows and "参考" in drows
              and str(want) in got_inc and str(total - want) in got_exc and qn >= want
              and all(s.replace('×', '').strip() != '参考' for s in first_chips) and errs == 0)
        print("验收", "PASS" if ok else "FAIL", "| JS 异常", errs)
    finally:
        base.api("/api/ui", "POST", {"patch": {"promptDock": False}})
        cdp.close()
        proc.terminate()


if __name__ == "__main__":
    main()
