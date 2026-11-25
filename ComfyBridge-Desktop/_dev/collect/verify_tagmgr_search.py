"""验收标签管理弹窗里的标签名检索框。"""
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


def main():
    total = len(httpx.get(f"{API}/tags", timeout=30).json()["data"])
    base.api("/api/ui", "POST", {"patch": {"route": "admin", "adminPage": "prompts", "promptDock": False}})
    proc, cdp = base.start_browser()
    try:
        cdp.call("Emulation.setDeviceMetricsOverride",
                 {"width": 900, "height": 1000, "deviceScaleFactor": 1, "mobile": False})
        cdp.call("Page.navigate", {"url": base.BASE + f"/?v={int(time.time())}#admin/prompts"})
        cdp.wait("document.querySelectorAll('.pt-card').length>2", 40)
        time.sleep(1.5)
        cdp.js("document.querySelector('#ptTagMgr').click()")
        cdp.wait("!!document.querySelector('#ptTagMgrQ')", 10)
        print("初始行数", cdp.js("document.querySelectorAll('.pt-tagmgr-row').length"), "/", total)

        cdp.js("(()=>{const q=document.querySelector('#ptTagMgrQ'); q.value='参考'; q.dispatchEvent(new Event('input',{bubbles:true}));})()")
        time.sleep(0.4)
        names = cdp.js("[...document.querySelectorAll('.pt-tagmgr-row .pt-tagname')].map(x=>x.value)")
        print("检索「参考」→", names)
        shot(cdp, "17-tagmgr-search.png")

        cdp.js("(()=>{const q=document.querySelector('#ptTagMgrQ'); q.value='不存在的标签'; q.dispatchEvent(new Event('input',{bubbles:true}));})()")
        time.sleep(0.4)
        empty = cdp.js("document.querySelector('#ptTagList').textContent.trim()")
        print("无匹配提示：", empty)
        shot(cdp, "18-tagmgr-empty.png")

        cdp.js("(()=>{const q=document.querySelector('#ptTagMgrQ'); q.value=''; q.dispatchEvent(new Event('input',{bubbles:true}));})()")
        time.sleep(0.4)
        back = cdp.js("document.querySelectorAll('.pt-tagmgr-row').length")
        print("清空后行数", back)
        cdp.js("document.querySelector('.m-foot [data-close]')?.click()")

        errs = sum(1 for e in cdp.events if e.get("method") == "Runtime.exceptionThrown")
        ok = (names and all("参考" in n for n in names) and "参考" in names
              and "没有匹配" in empty and back == total and errs == 0)
        print("验收", "PASS" if ok else "FAIL", "| JS 异常", errs)
    finally:
        cdp.close()
        proc.terminate()


if __name__ == "__main__":
    main()
