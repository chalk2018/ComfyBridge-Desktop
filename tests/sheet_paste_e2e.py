"""角色设定图「Ctrl+V 粘贴」页面回归。

全部通过真实浏览器页面的 DOM 事件完成：在页面里派发 paste 事件（带 text/plain
或 image/png 的 DataTransfer），只用 CDP 读取页面自己的 window.UI / DOM 做核对，
不直接调后端生成接口、不执行业务生成代码。

用法：.venv\\Scripts\\python.exe tests\\sheet_paste_e2e.py
结果：tests/_sheet_full_out/paste_regression.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sheet_full_e2e import (APP, OUT, CDP, ensure_sheet, click, click_value, q,  # noqa: E402
                           set_sel, set_text, start_browser)

RESULT = OUT / "paste_regression.json"
ROWS: list[dict[str, Any]] = []

# 1x1 PNG，用来模拟「复制成品图 → Ctrl+V」
PNG_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg=="
TEXT = "粘贴测试：二十四岁的钟表匠，圆框眼镜，短卷发，靛蓝工装外套、米色长裤、棕色皮靴，胸前挂黄铜放大镜，袖口有齿轮纹章"
REAL_TEXT = "ClipboardRealPaste-2026"


def rec(item: str, ok: bool, detail: str = "") -> None:
    ROWS.append({"item": item, "ok": bool(ok), "detail": detail})
    print(("PASS  " if ok else "FAIL  ") + item + (("  | " + detail) if detail else ""), flush=True)


def note(item: str, detail: str) -> None:
    ROWS.append({"item": item, "ok": None, "detail": detail})
    print("NOTE  " + item + "  | " + detail, flush=True)


def state(cdp: CDP) -> dict[str, Any]:
    return json.loads(cdp.js("""JSON.stringify({
        value:(document.querySelector('#sheetChar')||{}).value||'',
        form:UI.sheetForm.character||'',
        focus:(document.activeElement||{}).id||'',
        start:(document.querySelector('#sheetChar')||{}).selectionStart,
        disabled:(document.querySelector('#sendBtn')||{}).disabled,
        refs:UI.refs.length,
        mode:UI.mode,
        styleText:UI.sheetForm.styleText||''
    })""") or "{}")


def paste_text(cdp: CDP, text: str, target: str = "#sheetChar") -> dict[str, Any]:
    sel = "document" if target == "document" else f"document.querySelector({q(target)})"
    return json.loads(cdp.js(f"""(()=>{{
        const el={sel}; if(!el) return '{{}}';
        const dt=new DataTransfer(); dt.setData('text/plain',{q(text)});
        const ev=new ClipboardEvent('paste',{{bubbles:true,cancelable:true,clipboardData:dt}});
        el.dispatchEvent(ev);
        return JSON.stringify({{prevented:ev.defaultPrevented}});
    }})()""") or "{}")


def paste_image(cdp: CDP, target: str = "#sheetChar") -> bool:
    return bool(cdp.js(f"""(()=>{{
        const el=document.querySelector({q(target)}); if(!el) return false;
        const bin=atob({q(PNG_B64)}), bytes=new Uint8Array(bin.length);
        for(let i=0;i<bin.length;i++) bytes[i]=bin.charCodeAt(i);
        const dt=new DataTransfer();
        dt.items.add(new File([bytes],'paste-e2e.png',{{type:'image/png'}}));
        el.dispatchEvent(new ClipboardEvent('paste',{{bubbles:true,cancelable:true,clipboardData:dt}}));
        return true;
    }})()"""))


def focus_char(cdp: CDP, start: int | None = None, end: int | None = None) -> None:
    expr = "const c=document.querySelector('#sheetChar'); c.focus();"
    if start is not None:
        expr += f"c.setSelectionRange({start},{end if end is not None else start});"
    cdp.js("(()=>{" + expr + "return true})()")


def main() -> int:
    proc, cdp = start_browser()
    try:
        if not ensure_sheet(cdp):
            rec("页面进入角色设定图", False, "连不上页面或没找到 #sheetChar")
            return 1
        snap = json.loads(cdp.js("""JSON.stringify({
            capId:UI.capId, mode:UI.mode, refs:UI.refs, sheetForm:UI.sheetForm,
            sheetOpen:UI.sheetOpen, grade:UI.params.grade, prompt:UI.prompt
        })""") or "{}")
        rec("页面进入角色设定图", True, f"capId={snap.get('capId')} mode={snap.get('mode')}")

        # ---------- 1. 文字粘贴 ----------
        set_text(cdp, "#sheetChar", "", "input")
        focus_char(cdp)
        ev = paste_text(cdp, TEXT)
        st = state(cdp)
        rec("Ctrl+V 纯文字粘进角色描述框", st["value"] == TEXT and st["form"] == TEXT,
            f"value={len(st['value'])}字 form={len(st['form'])}字 prevented={ev.get('prevented')}")
        rec("粘贴后发送按钮立刻可用", st["disabled"] is False, f"disabled={st['disabled']}")

        # ---------- 2. 光标位置 ----------
        set_text(cdp, "#sheetChar", "ABCDEF", "input")
        focus_char(cdp, 3, 3)
        paste_text(cdp, "-XYZ-")
        st = state(cdp)
        rec("粘在光标处而不是整框覆盖", st["value"] == "ABC-XYZ-DEF", f"value={st['value']}")

        # ---------- 3. 页面轮询重绘不丢内容/焦点/光标 ----------
        focus_char(cdp, 5, 5)
        cdp.js("(()=>{refreshDynamic();return true})()")
        cdp.pause(250)
        st = state(cdp)
        rec("refreshDynamic 重绘后内容/焦点/光标都在",
            st["value"] == "ABC-XYZ-DEF" and st["focus"] == "sheetChar" and st["start"] == 5,
            f"focus={st['focus']} start={st['start']} value={st['value']}")
        cdp.js("(()=>{refreshComposer();return true})()")
        cdp.pause(200)
        st = state(cdp)
        rec("refreshComposer 重绘后内容/焦点/光标都在",
            st["value"] == "ABC-XYZ-DEF" and st["focus"] == "sheetChar" and st["start"] == 5,
            f"focus={st['focus']} start={st['start']}")

        # ---------- 4. 焦点被抢走时的兜底 ----------
        cdp.js("(()=>{document.activeElement&&document.activeElement.blur();return true})()")
        paste_text(cdp, "FALLBACK", "document")
        st = state(cdp)
        rec("焦点不在输入框时 Ctrl+V 仍落到角色描述", "FALLBACK" in st["value"]
            and "FALLBACK" in st["form"] and st["focus"] == "sheetChar",
            f"value={st['value']} focus={st['focus']}（按原光标位置插入）")

        # ---------- 4.5 重复重绘不会把同一段文字插入多遍 ----------
        set_text(cdp, "#sheetChar", "BASE", "input")
        focus_char(cdp, 4, 4)
        cdp.js("(()=>{bindStudio();bindStudio();bindStudio();return true})()")
        paste_text(cdp, "-ONCE-", "#inputbox")   # 派到输入区空白处，只走 .sheetbox 的委托
        st = state(cdp)
        rec("多次重绘后粘贴只插入一次",
            st["value"].count("-ONCE-") == 1 and st["form"].count("-ONCE-") == 1,
            f"value={st['value']} 出现次数={st['value'].count('-ONCE-')}")

        # ---------- 5. 图片粘贴成参考图 ----------
        if cdp.js("UI.mode") != "i2i":
            click(cdp, '[data-mode="i2i"]')
            cdp.pause(450)
        refs_before = int(cdp.js("UI.refs.length") or 0)
        paste_image(cdp)
        cdp.wait(f"UI.refs.length>{refs_before}", 20)
        refs_after = int(cdp.js("UI.refs.length") or 0)
        rec("复制图片 Ctrl+V 变成参考图", refs_after > refs_before,
            f"refs {refs_before} → {refs_after}")
        thumb = bool(cdp.js("!!document.querySelector('#refsRow [data-rmref]')"))
        rec("参考图缩略图在页面上出现", thumb, f"refsRow={thumb}")

        # ---------- 6. 自定义风格输入框不被抢 ----------
        set_sel(cdp, "#sheetStyle", "")       # 选「自定义…」
        cdp.pause(300)
        before_char = str(cdp.js("UI.sheetForm.character || ''") or "")
        cdp.js("(()=>{const e=document.querySelector('#sheetStyleText'); if(e) e.focus(); return true})()")
        ev = paste_text(cdp, "水彩速写风", "#sheetStyleText")
        st = state(cdp)
        rec("自定义风格输入框粘贴不抢角色描述",
            st["form"] == before_char and ev.get("prevented") is False,
            f"prevented={ev.get('prevented')} 角色描述未变={st['form'] == before_char}")

        # ---------- 6.5 普通能力输入框的粘贴没被新增的兜底干扰 ----------
        other = str(cdp.js("""(()=>{const c=(S.capabilities||[]).find(x=>x.id!=='sheet.character'&&x.enabled);
                              return c?c.id:''})()""") or "")
        if other:
            try:
                click_value(cdp, "data-cap", other)
                cdp.wait("!!document.querySelector('#promptInput')", 15)
                cdp.pause(350)
                cdp.js("(()=>{const e=document.querySelector('#promptInput');if(e)e.focus();return true})()")
                paste_text(cdp, "普通能力粘贴回归", "#promptInput")
                ed = str(cdp.js("(document.querySelector('#promptInput')||{}).innerText||''") or "")
                ui_prompt = str(cdp.js("UI.prompt||''") or "")
                rec("普通能力输入框粘贴回归（没被设定图兜底抢走）",
                    "普通能力粘贴回归" in ed or "普通能力粘贴回归" in ui_prompt,
                    f"capId={other} 编辑器={len(ed)}字 UI.prompt={len(ui_prompt)}字")
            except Exception as e:
                note("普通能力输入框粘贴回归（没被设定图兜底抢走）", f"页面事件回归未跑成：{e}")
            click_value(cdp, "data-cap", "sheet.character")
            cdp.wait("!!document.querySelector('#sheetChar')", 15)
            cdp.pause(350)
        else:
            note("普通能力输入框粘贴回归（没被设定图兜底抢走）", "当前只有一个能力，跳过")

        # ---------- 7. 真实系统剪贴板 Ctrl+V（可选验证） ----------
        focus_char(cdp)
        try:
            cdp.call("Input.dispatchKeyEvent", {"type": "keyDown", "modifiers": 2, "key": "v",
                                                "code": "KeyV", "windowsVirtualKeyCode": 86})
            cdp.call("Input.dispatchKeyEvent", {"type": "keyUp", "modifiers": 2, "key": "v",
                                                "code": "KeyV", "windowsVirtualKeyCode": 86})
            cdp.pause(500)
            st = state(cdp)
            ok_real = REAL_TEXT in st["value"] or REAL_TEXT in st["form"]
            if ok_real:
                rec("真实 Ctrl+V 按键（系统剪贴板）", True, "文本已进入输入框")
            else:
                note("真实 Ctrl+V 按键（系统剪贴板）",
                     "无头 Edge 不读取系统剪贴板；页面 paste 事件路径已由其余各项覆盖")
        except Exception as e:
            note("真实 Ctrl+V 按键（系统剪贴板）", f"无头浏览器不支持该按键注入：{e}")

        # ---------- 8. 恢复现场，不把测试文字/参考图留给用户的草稿 ----------
        cdp.js("""(async()=>{
            const s=""" + q(json.dumps(snap, ensure_ascii=False)) + """;
            const d=JSON.parse(s);
            UI.capId=d.capId; UI.mode=d.mode; UI.refs=d.refs; UI.sheetForm=d.sheetForm;
            UI.sheetOpen=d.sheetOpen; UI.params.grade=d.grade; UI.prompt=d.prompt;
            UI.sheetPreview=null;
            // 普通能力回归项粘进去的测试文字不留痕（也清掉早期运行留下的历史痕迹）
            for(const k of Object.keys(S.ui.drafts||{})){
                const x=S.ui.drafts[k];
                if(x && typeof x.prompt==='string') x.prompt=x.prompt.split('普通能力粘贴回归').join('');
            }
            await saveDraft(); render(); return true;
        })()""")
        cdp.pause(600)
        st = state(cdp)
        rec("测试后恢复用户原来的草稿", st["value"] == str(snap.get("sheetForm", {}).get("character", "") or "")
            and st["refs"] == len(snap.get("refs") or []),
            f"value={len(st['value'])}字 refs={st['refs']} mode={st['mode']}")
        return 0
    finally:
        RESULT.write_text(json.dumps(ROWS, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n结果写入 {RESULT}", flush=True)
        cdp.close()
        proc.terminate()


if __name__ == "__main__":
    raise SystemExit(main())
