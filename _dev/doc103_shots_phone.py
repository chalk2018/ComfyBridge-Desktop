"""只补手机端两张：场景设定图、宫格分镜故事板。"""

from __future__ import annotations

import time

import httpx

from doc103_shots import BASE, PHONE, connect, set_ui

SCENE_TEXT = ("老城区的旧书店内景：靠墙一排深色木书架，书脊颜色杂而不乱，"
              "窗前一张旧木桌，桌上摊着翻开的书和一台收音机，午后斜光从左侧窗户压进来")


def main() -> None:
    cdp, proc = connect()
    try:
        cdp.call("Emulation.setDeviceMetricsOverride", PHONE)
        cdp.wait("typeof UI!=='undefined' && !!S && !!S.capabilities", 30)
        time.sleep(1.2)
        set_ui(cdp, """(() => {
          UI.route='studio'; UI.capId='sheet.scene'; UI._draftCap='sheet.scene';
          UI._navPending=true; UI.mode='t2i'; UI.refs=[]; UI.extOpen=false;
          UI.navOpen=false; UI.sheet=false;
          UI.extForm={kind:'scene', layout:'N', sub:'', ink:'color', nochar:'yes', ref:'none',
            grade:'默认', text:'%s', style:'写实摄影', styleCustom:false, styleText:'',
            lineStyle:'W1', refSlots:{}};
          saveDraft({route:'studio',capId:'sheet.scene',ext:{...UI.extForm},extOpen:false});
          const app=document.querySelector('.app'); if(app){app.classList.remove('nav-open');app.classList.remove('sheet-open');}
          render(); window.scrollTo(0, document.body.scrollHeight); return true;
        })()""" % SCENE_TEXT.replace("'", "\\'"), 2.2)
        cdp.shot("u_phone_scene")

        set_ui(cdp, """(() => {
          UI.capId='board.story'; UI._draftCap='board.story'; UI._navPending=true; UI.boardOpen=false;
          UI.boardForm={layout:'G4', ink:'color', lineStyle:'W1', grade:'默认', sel:1, cells:[
            '清晨的码头，短发女孩背着相机快步走来，远景','女孩蹲在台阶边整理相机包，中景，侧光',
            '女孩抬头看向河面上的乌篷船，近景，浅景深','乌篷船慢慢划过拱桥，女孩站在岸边目送，大远景']};
          saveDraft({route:'studio',capId:'board.story',board:{...UI.boardForm},boardOpen:false});
          render(); window.scrollTo(0, document.body.scrollHeight); return true;
        })()""", 2.2)
        cdp.shot("u_phone_board")
        cdp.shot_el("u_phone_board_grid", ".composer", 6)
    finally:
        cdp.ws.close()
        proc.terminate()
        httpx.post(f"{BASE}/api/ui", json={"patch": {"route": "studio", "capId": "image.generate",
                                                     "adminPage": "caps"}}, timeout=20)


if __name__ == "__main__":
    main()
