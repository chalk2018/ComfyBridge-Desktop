"""1.03 移动端布局检查：390×844 下打开场景 / 道具设定图，量溢出与控件可点区域。

只看布局，不出图。截图落在 tests/_ext_mobile/，方便人眼复核。
用法：
    .venv\\Scripts\\python.exe tests\\ext_mobile_check.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ext_sheet_e2e import CDP, click, q, start_browser  # noqa: E402

APP = Path(__file__).resolve().parents[1]
OUT = APP / "tests" / "_ext_mobile"
OUT.mkdir(parents=True, exist_ok=True)

MEASURE = """(() => {
  const de = document.documentElement;
  const vw = window.innerWidth;
  const bad = [];
  const SEL = '.ext-bar, .ext-bar .row, .ext-layouts, .ext-slot, .ext-slots, #extText, .ext-grade, .ext-actions, .composer, .topbar';
  document.querySelectorAll(SEL).forEach(el => {
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) return;
    if (r.right > vw + 1 || r.left < -1) {
      bad.push({sel: el.className || el.id, left: Math.round(r.left), right: Math.round(r.right)});
    }
  });
  const btn = document.querySelector('.ext-actions button, .composer button');
  const br = btn ? btn.getBoundingClientRect() : null;
  return {
    vw, scrollW: de.scrollWidth, clientW: de.clientWidth,
    overflowX: de.scrollWidth - de.clientWidth,
    overflow: bad,
    btn: br ? {w: Math.round(br.width), h: Math.round(br.height), right: Math.round(br.right)} : null,
    textAreaW: (() => { const t = document.getElementById('extText'); return t ? Math.round(t.getBoundingClientRect().width) : 0; })(),
  };
})()"""


def shoot(cdp: CDP, name: str) -> None:
    data = cdp.call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False})
    import base64
    (OUT / f"{name}.png").write_bytes(base64.b64decode(data["data"]))


def open_cap(cdp: CDP, cap_id: str, kind: str) -> bool:
    if not cdp.wait("typeof UI!=='undefined' && !!document.querySelector('.app .topbar')", 25):
        return False
    # 手机上侧栏默认收起，先展开再点能力
    cdp.js("(()=>{const b=document.querySelector('[data-side-open]'); if(b) b.click(); return true;})()")
    cdp.pause(200)
    if not cdp.js(f"UI.capId==={q(cap_id)}"):
        if not click(cdp, f'.rail [data-cap="{cap_id}"]'):
            return False
        cdp.pause(400)
    cdp.js("(()=>{const b=document.querySelector('[data-side-close]'); if(b) b.click(); return true;})()")
    return cdp.wait(f"UI.capId==={q(cap_id)}", 10) is not None


def main() -> int:
    proc, cdp = start_browser()
    report = {"view": "390x844"}
    try:
        cdp.call("Emulation.setDeviceMetricsOverride", {
            "width": 390, "height": 844, "deviceScaleFactor": 2,
            "mobile": True, "screenWidth": 390, "screenHeight": 844,
        })
        cdp.call("Page.reload", {"ignoreCache": True})
        if not cdp.wait("typeof UI!=='undefined' && !!document.querySelector('.app .topbar')", 30):
            print("页面没起来")
            return 1
        cdp.pause(600)
        report["home"] = cdp.js(MEASURE)
        shoot(cdp, "01_home")

        for cap, kind, tag in (("sheet.prop", "prop", "02_prop"), ("sheet.scene", "scene", "03_scene")):
            if not open_cap(cdp, cap, kind):
                report[tag] = {"opened": False}
                continue
            cdp.pause(500)
            report[tag] = cdp.js(MEASURE)
            report[tag]["opened"] = True
            shoot(cdp, tag)
            # 切到多视图/九宫格这类版式，确认版式条在窄屏不炸
            cdp.js("(()=>{const b=document.querySelector('[data-ext-layout]'); if(b) b.click(); return true;})()")
            cdp.pause(300)
            report[tag + "_alt"] = cdp.js(MEASURE)
            shoot(cdp, tag + "_alt")

        OUT.joinpath("report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        for k, v in report.items():
            if isinstance(v, dict) and "overflowX" in v:
                print(f"{k}: 横向溢出={v['overflowX']}px 文本域宽={v['textAreaW']} "
                      f"按钮={v['btn']} 越界元素={len(v['overflow'])}")
        print("结果：", OUT / "report.json")
    finally:
        cdp.close()
        proc.terminate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
