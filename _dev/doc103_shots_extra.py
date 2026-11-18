"""补拍：历史页 + 手机端抽屉（1.03 能力数变了，1.02 的老图必须换）。"""

from __future__ import annotations

import time

import httpx

from doc103_shots import BASE, DESK, PHONE, connect, set_ui


def main() -> None:
    cdp, proc = connect()
    try:
        cdp.call("Emulation.setDeviceMetricsOverride", DESK)
        cdp.wait("typeof UI!=='undefined' && !!S && !!S.capabilities", 30)
        time.sleep(1.2)
        set_ui(cdp, """(() => {
          UI.route='gallery'; UI._navPending=true; UI.galleryFilter='全部';
          saveDraft({route:'gallery'});
          render(); window.scrollTo(0,0); return true;
        })()""", 2.2)
        cdp.shot("u_gallery")
        cdp.shot_el("u_gallery_filters", ".gallery-bar, .gbar, .filters", 10)

        # 手机端
        cdp.call("Emulation.setDeviceMetricsOverride", PHONE)
        set_ui(cdp, """(() => {
          UI.route='studio'; UI.capId='board.story'; UI._draftCap='board.story';
          UI._navPending=true; UI.navOpen=false; UI.sheet=false; UI.boardOpen=false;
          const app=document.querySelector('.app'); if(app){app.classList.remove('nav-open');app.classList.remove('sheet-open');}
          render(); window.scrollTo(0, document.body.scrollHeight); return true;
        })()""", 1.8)
        cdp.js("(() => {const b=document.querySelector('#navToggle'); if(b) b.click(); return !!b})()")
        time.sleep(1.0)
        cdp.shot("u_m_nav")
        set_ui(cdp, """(() => {
          const app=document.querySelector('.app');
          if(app) app.classList.remove('nav-open'); UI.navOpen=false; render(); return true;
        })()""", 0.8)
        cdp.js("(() => {const b=document.querySelector('#sheetToggle'); if(b) b.click(); return !!b})()")
        time.sleep(1.0)
        cdp.shot("u_m_right")
        set_ui(cdp, """(() => {
          const app=document.querySelector('.app');
          if(app) app.classList.remove('sheet-open'); UI.sheet=false;
          UI.route='admin'; UI.adminPage='caps'; render(); window.scrollTo(0,0); return true;
        })()""", 1.4)
        cdp.js("(() => {const b=document.querySelector('#navToggle'); if(b) b.click(); return !!b})()")
        time.sleep(1.0)
        cdp.shot("u_m_admin_nav")
    finally:
        cdp.ws.close()
        proc.terminate()
        httpx.post(f"{BASE}/api/ui", json={"patch": {"route": "studio", "capId": "image.generate",
                                                     "adminPage": "caps"}}, timeout=20)


if __name__ == "__main__":
    main()
