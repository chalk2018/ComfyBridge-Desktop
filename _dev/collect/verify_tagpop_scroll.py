"""验收：选择标签浮层滚动不关闭 + 浮层内检索清除 + 标签管理清除（管理页 / 速取小窗）。"""
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
TAGSEL = "图生图 · 牛仔人像转水彩"  # 库里已存在的卡片，避免每次造/删数据


def shot(cdp, name):
    (SHOT / name).write_bytes(base64.b64decode(cdp.call("Page.captureScreenshot", {"format": "png"})["data"]))
    print("  shot", name)


def open_card_add(cdp, dock=False):
    """打开某卡片的「添加标签」浮层。管理页用搜索框定位；速取直接按 id 找卡。"""
    if dock:
        # 速取的「添加标签」在卡片编辑器里：先点第一张卡的编辑（✎），再点编辑器的 ＋
        cdp.js("document.querySelector('.pt-dcard [data-dedit]')?.click()")
        time.sleep(0.5)
        ok = cdp.js("(()=>{const b=document.querySelector('.pt-dock-editor [data-addtag]');"
                    "if(!b)return false;b.click();return true;})()")
    else:
        cdp.js("(()=>{const q=document.querySelector('#ptQ'); q.value=" + repr(TAGSEL).replace("'", '"') +
               "; q.dispatchEvent(new Event('input',{bubbles:true}));})()")
        time.sleep(1.0)
        ok = cdp.js("(()=>{const c=[...document.querySelectorAll('.pt-card')]"
                    ".find(x=>{const t=x.querySelector('.pt-title');return t&&t.value.trim()==='" + TAGSEL + "';});"
                    "if(!c)return false;const b=c.querySelector('[data-addtag]');if(!b)return false;"
                    "b.click();return true;})()")
    time.sleep(0.5)
    return bool(ok)


def main():
    total_tags = len(httpx.get(f"{API}/tags", timeout=30).json()["data"])
    results = {}
    base.api("/api/ui", "POST", {"patch": {"route": "admin", "adminPage": "prompts", "promptDock": False}})
    proc, cdp = base.start_browser()
    try:
        cdp.call("Emulation.setDeviceMetricsOverride",
                 {"width": 1320, "height": 900, "deviceScaleFactor": 1, "mobile": False})
        cdp.call("Page.navigate", {"url": base.BASE + f"/?v={int(time.time())}#admin/prompts"})
        cdp.wait("document.querySelectorAll('.pt-card').length>2", 40)
        time.sleep(1.5)

        # ---------- 管理页：选择标签浮层 ----------
        opened = open_card_add(cdp)
        cdp.wait("!!document.querySelector('.pt-tagpop')", 10)
        n_all = cdp.js("document.querySelectorAll('.pt-tagpop .pt-tagpop-item').length")
        has_q = cdp.js("!!document.querySelector('.pt-tagpop [data-tagpopq]')")
        has_x = cdp.js("!!document.querySelector('.pt-tagpop [data-tagpopclear]')")
        x_hidden = cdp.js("getComputedStyle(document.querySelector('.pt-tagpop [data-tagpopclear]')).display==='none'")
        print("A 管理页浮层：打开", opened, "标签", n_all, "检索框", has_q, "×按钮", has_x, "初始隐藏", x_hidden)
        shot(cdp, "23-tagpop-q.png")

        # 浮层内部滚动：用 JS 直接改 scrollTop 并派发冒泡 scroll（等价滚轮/拖滚动条）
        cdp.js("(()=>{const l=document.querySelector('.pt-tagpop .pt-tagpop-list');"
               "l.scrollTop=60;l.dispatchEvent(new Event('scroll',{bubbles:true}));})()")
        time.sleep(0.4)
        alive_after_scroll = cdp.js("!!document.querySelector('.pt-tagpop')")
        print("A 浮层内滚动后仍在：", alive_after_scroll)

        # 输入过滤
        cdp.js("(()=>{const q=document.querySelector('.pt-tagpop [data-tagpopq]');"
               "q.value='参考';q.dispatchEvent(new Event('input',{bubbles:true}));})()")
        time.sleep(0.3)
        names = cdp.js("[...document.querySelectorAll('.pt-tagpop .pt-tagpop-item span')].map(x=>x.textContent.trim())")
        x_shown = cdp.js("getComputedStyle(document.querySelector('.pt-tagpop [data-tagpopclear]')).display!=='none'")
        print("A 过滤「参考」→", names, "| ×显示", x_shown)
        shot(cdp, "24-tagpop-filtered.png")

        # 点 × 清空
        cdp.js("document.querySelector('.pt-tagpop [data-tagpopclear]').click()")
        time.sleep(0.3)
        restored = cdp.js("document.querySelectorAll('.pt-tagpop .pt-tagpop-item').length")
        q_empty = cdp.js("document.querySelector('.pt-tagpop [data-tagpopq]').value===''")
        x_hidden2 = cdp.js("getComputedStyle(document.querySelector('.pt-tagpop [data-tagpopclear]')).display==='none'")
        print("A 点×后：标签", restored, "输入框空", q_empty, "×再隐藏", x_hidden2)

        # 勾选一个标签，验证过滤态下仍能正常操作
        cdp.js("document.querySelector('.pt-tagpop .pt-tagpop-item').click()")
        time.sleep(0.3)
        picked = cdp.js("document.querySelector('.pt-tagpop .pt-tagpop-num').textContent")
        print("A 勾选后计数：", picked, "（卡片原有 3 个标签，点第一个是取消 → 2/10）")

        # 点浮层外部（真实 pointerdown）仍应关闭 —— 防止过度修复
        cdp.js("document.dispatchEvent(new PointerEvent('pointerdown',{bubbles:true,composed:true}))")
        time.sleep(0.3)
        closed = cdp.js("!document.querySelector('.pt-tagpop')")
        print("A 点外部后关闭：", closed)

        # ---------- 标签管理：检索 + ×清除 ----------
        cdp.js("document.querySelector('#ptTagMgr').click()")
        cdp.wait("!!document.querySelector('#ptTagMgrQClear')", 10)
        mgr_x_hidden = cdp.js("getComputedStyle(document.querySelector('#ptTagMgrQClear')).display==='none'")
        cdp.js("(()=>{const q=document.querySelector('#ptTagMgrQ');q.value='参考';q.dispatchEvent(new Event('input',{bubbles:true}));})()")
        time.sleep(0.3)
        mgr_rows = cdp.js("[...document.querySelectorAll('.pt-tagmgr-row .pt-tagname')].map(x=>x.value)")
        mgr_x_shown = cdp.js("getComputedStyle(document.querySelector('#ptTagMgrQClear')).display!=='none'")
        shot(cdp, "25-tagmgr-qclear.png")
        cdp.js("document.querySelector('#ptTagMgrQClear').click()")
        time.sleep(0.3)
        mgr_back = cdp.js("document.querySelectorAll('.pt-tagmgr-row').length")
        print("B 标签管理：初始×隐藏", mgr_x_hidden, "过滤", mgr_rows, "×显示", mgr_x_shown, "点×恢复", mgr_back, "/", total_tags)
        cdp.js("document.querySelector('.m-foot [data-close]')?.click()")

        # ---------- 速取小窗：同样两项 ----------
        base.api("/api/ui", "POST", {"patch": {"route": "studio", "promptDock": True}})
        cdp.call("Page.navigate", {"url": base.BASE + f"/?d={int(time.time())}#studio"})
        cdp.wait("!!document.querySelector('.pt-dock') && !document.querySelector('.pt-dock').hidden", 40)
        cdp.wait("document.querySelectorAll('.pt-dcard').length>0", 30)
        time.sleep(1.5)
        d_opened = open_card_add(cdp, dock=True)
        time.sleep(0.5)
        d_has_q = cdp.js("!!document.querySelector('.pt-tagpop [data-tagpopq]')")
        d_alive = True
        if d_has_q:
            cdp.js("(()=>{const l=document.querySelector('.pt-tagpop .pt-tagpop-list');"
                   "l.scrollTop=60;l.dispatchEvent(new Event('scroll',{bubbles:true}));})()")
            time.sleep(0.4)
            d_alive = cdp.js("!!document.querySelector('.pt-tagpop')")
        print("C 速取浮层：打开", d_opened, "检索框", d_has_q, "滚动后仍在", d_alive)
        shot(cdp, "26-dock-tagpop.png")
        cdp.js("document.dispatchEvent(new PointerEvent('pointerdown',{bubbles:true,composed:true}))")
        time.sleep(0.2)

        errs = sum(1 for e in cdp.events if e.get("method") == "Runtime.exceptionThrown")
        results = {
            "管理页浮层打开且有检索框/×": opened and has_q and has_x and x_hidden,
            "浮层内滚动不关闭": alive_after_scroll,
            "浮层检索过滤+×清空": bool(names) and all("参考" in n for n in names) and x_shown
                                 and restored == n_all and q_empty and x_hidden2 and picked.count("/") == 1,
            "点外部仍关闭": closed,
            "标签管理检索×清除": mgr_x_hidden and bool(mgr_rows) and all("参考" in n for n in mgr_rows)
                                 and mgr_x_shown and mgr_back == total_tags,
            "速取浮层同步": d_opened and d_has_q and d_alive,
            "无JS异常": errs == 0,
        }
        for k, v in results.items():
            print(("  OK  " if v else "  BAD ") + k)
        print("验收", "PASS" if all(results.values()) else "FAIL", "| JS 异常", errs)
    finally:
        base.api("/api/ui", "POST", {"patch": {"promptDock": False}})
        cdp.close()
        proc.terminate()


if __name__ == "__main__":
    main()
