"""1.03 管理台真实页面检查：场景/道具档位表与各自设定图模板。

不生成图片，只验证菜单、组合切换和模板弹窗能在真实页面里正常工作。
用法：
    .venv\\Scripts\\python.exe tests\\ext_admin_ui_check.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ext_sheet_e2e import CDP, click, q, start_browser  # noqa: E402

APP = Path(__file__).resolve().parents[1]
OUT = APP / "tests" / "_ext_sheet_e2e" / "admin_check.json"


def main() -> None:
    proc, cdp = start_browser()
    results = {}
    try:
        assert cdp.wait("typeof UI!=='undefined' && !!document.querySelector('.topbar')", 25)
        click(cdp, '.topbar [data-nav="admin"]')
        assert cdp.wait("UI.route==='admin' && !!document.querySelector('.admin-nav')", 20)
        results["adminOpened"] = True

        for cap_id, expect_modes, template_word in (
            ("sheet.scene", ["t2i", "i2i", "expand"], "场景设定图"),
            ("sheet.prop", ["t2i", "i2i"], "道具设定图"),
        ):
            click(cdp, f'[data-grades-cap="{cap_id}"]')
            if not cdp.wait(f"UI.capId==={q(cap_id)}", 10):
                click(cdp, f'[data-grades-cap="{cap_id}"]')
                cdp.wait(f"UI.capId==={q(cap_id)}", 10)
            cdp.wait("""(()=>{const a=[...document.querySelectorAll('[data-combo-mode]')].map(e=>e.dataset.comboMode);
              const b=[...new Set(((S.extCombos||{})[(currentCap()||{}).extKind]||[]).map(c=>c.mode))];
              return a.length===b.length && a.every(x=>b.includes(x));})()""", 15)
            modes = cdp.js("[...document.querySelectorAll('[data-combo-mode]')].map(e=>e.dataset.comboMode)")
            results[cap_id] = {
                "cap": cdp.js("UI.capId") == cap_id,
                "gradesPage": cdp.js("UI.adminPage") == "grades",
                "combos": cdp.js("((S.extCombos||{})[(currentCap()||{}).extKind]||[]).length"),
                "stateModes": cdp.js("[...new Set(((S.extCombos||{})[(currentCap()||{}).extKind]||[]).map(c=>c.mode))]"),
                "comboSel": cdp.js("UI.comboSel"),
                "modes": modes,
                "modesOk": all(m in (modes or []) for m in expect_modes),
            }
            if "expand" in expect_modes:
                click(cdp, '[data-combo-mode="expand"]')
                cdp.pause(500)
                results[cap_id]["expandSelected"] = ".expand." in str(cdp.js("UI.comboSel"))
                click(cdp, '[data-combo-mode="t2i"]')
                cdp.pause(400)
            click(cdp, "[data-sheet-template]")
            ok = cdp.wait("!!document.querySelector('.modal') && document.querySelector('.modal').innerText.includes(" + q(template_word) + ")", 15)
            results[cap_id]["templateModal"] = bool(ok)
            click(cdp, ".modal [data-close]")
            cdp.pause(300)

        OUT.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(results, ensure_ascii=False, indent=2))
    finally:
        cdp.close()
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    main()
