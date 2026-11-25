"""改动后 UI 快速目检：档位表 6 组、结果卡提示词、右栏固定头/缩略图、模板入口。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path("tests").resolve()))
from ui_e2e_test import CDP, api, click, start_browser  # noqa: E402


def js(cdp: CDP, code: str):
    return cdp.js(code)


def main() -> int:
    proc, cdp = start_browser()
    try:
        cdp.call("Emulation.setDeviceMetricsOverride",
                 {"width": 1440, "height": 1000, "deviceScaleFactor": 1, "mobile": False})
        cdp.call("Network.enable")
        cdp.call("Network.setCacheDisabled", {"cacheDisabled": True})
        cdp.call("Page.navigate", {"url": "http://127.0.0.1:8190/#admin"})
        cdp.pause(2800)
        cdp.js("document.documentElement.style.scrollBehavior='auto'; true")

        # 进档位表 → 角色设定图
        click(cdp, '[data-page="grades"]'); cdp.pause(600)
        click(cdp, '[data-grades-cap="sheet.character"]'); cdp.pause(1200)
        r = js(cdp, """(() => ({
          modeBtns: document.querySelectorAll('[data-combo-mode]').length,
          layoutBtns: document.querySelectorAll('[data-combo-layout]').length,
          hasInk: !!document.querySelector('[data-combo-ink]'),
          hasRef: !!document.querySelector('[data-combo-ref]'),
          hasTpl: !!document.querySelector('[data-sheet-template]'),
          dragRows: document.querySelectorAll('[data-gdrag]').length,
          current: (document.querySelector('.sheetbar:last-of-type b')||{}).textContent||'',
          page: UI.adminPage, comboSel: UI.comboSel
        }))()""")
        print("档位表：", r)

        # 结果卡提示词统一 + 看完整提示词
        cdp.call("Page.navigate", {"url": "http://127.0.0.1:8190/#studio"}); cdp.pause(2200)
        r2 = js(cdp, """(() => ({
          jobs: document.querySelectorAll('.job.done').length,
          promptBoxes: document.querySelectorAll('.job .body .msg-u').length,
          fullBtns: document.querySelectorAll('[data-seefull]').length,
          promptLines: (()=>{const e=document.querySelector('.job .body .msg-u'); if(!e)return null;
            const cs=getComputedStyle(e); return {maxH:cs.maxHeight, overflow:cs.overflowY, h:e.clientHeight, sh:e.scrollHeight};})(),
          drawerFixed: !!document.querySelector('.drawer-fixed'),
          drawerScroll: !!document.querySelector('.drawer-scroll'),
          taskThumbs: document.querySelectorAll('.task-thumbs').length,
          zoomExpand: !!document.querySelector('#zoomExpand')
        }))()""")
        print("创作台：", r2)

        # 打开预览弹窗检查按钮
        z = js(cdp, "(()=>{const e=document.querySelector('[data-zoom]'); if(!e)return 'no-zoom'; e.click(); return 'clicked';})()")
        cdp.pause(500)
        r3 = js(cdp, """(() => ({
          hasExpand: !!document.querySelector('#zoomExpand'),
          hasCopyImg: !!document.querySelector('#copyImg'),
          hasOpenDir: !!document.querySelector('#openDir'),
          hasDl: !!document.querySelector('#zoomDl'),
          footBtns: [...document.querySelectorAll('.modal .m-foot button')].map(b=>b.id||b.textContent.trim())
        }))()""")
        print("预览弹窗：", z, r3)
        return 0
    finally:
        cdp.close(); proc.terminate()


if __name__ == "__main__":
    raise SystemExit(main())
