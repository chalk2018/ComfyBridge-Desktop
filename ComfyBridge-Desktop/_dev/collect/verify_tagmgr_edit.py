"""验收标签管理：✓ 保存按钮状态、× 删除确认、批量删除确认（不真正改数据）。"""
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


def heads(cdp):
    return cdp.js("[...document.querySelectorAll('.m-head h3')].map(x=>x.textContent.trim())")


def cancel(cdp):
    cdp.js("(()=>{const b=[...document.querySelectorAll('.m-foot .btn')].find(x=>x.textContent.trim()==='取消'); if(b)b.click();})()")
    time.sleep(0.4)


def main():
    before = len(httpx.get(f"{API}/tags", timeout=30).json()["data"])
    base.api("/api/ui", "POST", {"patch": {"route": "admin", "adminPage": "prompts", "promptDock": False}})
    proc, cdp = base.start_browser()
    try:
        cdp.call("Emulation.setDeviceMetricsOverride",
                 {"width": 900, "height": 1000, "deviceScaleFactor": 1, "mobile": False})
        cdp.call("Page.navigate", {"url": base.BASE + f"/?v={int(time.time())}#admin/prompts"})
        cdp.wait("document.querySelectorAll('.pt-card').length>2", 40)
        time.sleep(1.5)
        cdp.js("document.querySelector('#ptTagMgr').click()")
        cdp.wait("document.querySelectorAll('.pt-tagmgr-row').length>2", 10)

        init = cdp.js("document.querySelector('.pt-tagmgr-save').disabled")
        print("✓ 初始禁用：", init, "| 每行控件：", cdp.js("(()=>{const r=document.querySelector('.pt-tagmgr-row');return [!!r.querySelector('.pt-tagmgr-check'),!!r.querySelector('.pt-tagmgr-save'),!!r.querySelector('[data-tagdel]')];})()"))

        # 改名字 → 亮；恢复 → 灭
        cdp.js("(()=>{const i=document.querySelector('.pt-tagname'); i.value=i.value+'_测试'; i.dispatchEvent(new Event('input',{bubbles:true}));})()")
        time.sleep(0.3)
        on = cdp.js("!document.querySelector('.pt-tagmgr-save').disabled")
        shot(cdp, "19-tagmgr-save-ready.png")
        cdp.js("(()=>{const i=document.querySelector('.pt-tagname'); i.value=i.dataset.oldname; i.dispatchEvent(new Event('input',{bubbles:true}));})()")
        time.sleep(0.3)
        off = cdp.js("document.querySelector('.pt-tagmgr-save').disabled")
        print("改名后 ✓ 亮：", on, "| 复原后 ✓ 灭：", off)

        # 改颜色 → 亮；恢复 → 灭
        cdp.js("(()=>{const i=document.querySelector('.pt-color'); i.value='#123456'; i.dispatchEvent(new Event('input',{bubbles:true}));})()")
        time.sleep(0.3)
        color_on = cdp.js("!document.querySelector('.pt-tagmgr-save').disabled")
        cdp.js("(()=>{const i=document.querySelector('.pt-color'); i.value=i.dataset.oldcolor; i.dispatchEvent(new Event('input',{bubbles:true}));})()")
        time.sleep(0.3)
        color_off = cdp.js("document.querySelector('.pt-tagmgr-save').disabled")
        print("改色后 ✓ 亮：", color_on, "| 复原后 ✓ 灭：", color_off)

        # 单行删除确认
        cdp.js("document.querySelector('[data-tagdel]').click()")
        cdp.wait("document.querySelectorAll('.m-head h3').length>1", 10)
        h = heads(cdp)
        print("点 × 后弹窗：", h)
        time.sleep(0.7)   # 等弹窗淡入动画结束，避免截到半透明状态
        shot(cdp, "20-tagmgr-del-confirm.png")
        cancel(cdp)

        # 批量选择 + 确认
        cdp.js("(()=>{const c=document.querySelectorAll('.pt-tagmgr-check'); c[0].click(); c[1].click();})()")
        time.sleep(0.4)
        bulk = cdp.js("(()=>{const b=document.querySelector('#ptTagBulkDel');return [b.disabled,b.textContent.trim()];})()")
        print("批量按钮：", bulk)
        cdp.js("document.querySelector('#ptTagBulkDel').click()")
        cdp.wait("document.querySelectorAll('.m-head h3').length>1", 10)
        bh = heads(cdp)
        print("批量确认弹窗：", bh)
        time.sleep(0.7)
        shot(cdp, "21-tagmgr-bulk-confirm.png")
        cancel(cdp)
        restored = cdp.js("(()=>{const b=document.querySelector('#ptTagBulkDel');return [b.disabled,b.textContent.trim()];})()")
        print("取消后批量按钮：", restored)
        cdp.js("(()=>{const a=document.querySelector('#ptTagMgrAll'); if(a.checked) a.click();})()")
        time.sleep(0.5)
        shot(cdp, "22-tagmgr-controls.png")
        after = httpx.get(f"{API}/tags", timeout=30).json()["data"]
        print("接口标签数", before, "→", len(after))

        errs = sum(1 for e in cdp.events if e.get("method") == "Runtime.exceptionThrown")
        ok = (init and on and off and color_on and color_off
              and any("是否删除标签" in x for x in h) and any("是否删除这 2 个标签" in x for x in bh)
              and bulk[0] is False and "（2）" in bulk[1] and "（2）" in restored[1]
              and len(after) == before and errs == 0)
        print("验收", "PASS" if ok else "FAIL", "| JS 异常", errs)
    finally:
        cdp.close()
        proc.terminate()


if __name__ == "__main__":
    main()
