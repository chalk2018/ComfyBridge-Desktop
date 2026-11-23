"""提示词模板库「参考素材」全流程 E2E（1.07）。

覆盖：卡片素材缩略图（类型标记 / 同类型序号）、上传、16:9 查看窗（图片复制+下载、
视频播放+下载、音频播放+下载）、素材落盘目录、含参 md 分文件、编辑时素材增删、
速取小窗的缩略图与编辑面板；最后留下 3 条示例模板，不删改任何原有数据。

前置：应用在 8190 运行（已重启加载新代码）。
运行：.venv\\Scripts\\python.exe tests\\prompt_media_e2e_test.py
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / "tests"))

import httpx  # noqa: E402
import ui_e2e_test as base  # noqa: E402

CDP = base.CDP
check = base.check
api = base.api
click = base.click
BASE = base.BASE

SAMPLES = APP / "_dev" / "_samples"
TPL_DIR = APP / "data" / "prompt_templates"
HTTP = httpx.Client(base_url=BASE, timeout=60.0)

TITLE_A = "参考生图 · 山湖晨光构图复位"
TITLE_B = "参考生视频 · 运镜节奏复刻"
TITLE_C = "参考生音乐 · 古筝动机延展"
TITLE_D = "参考生图 · 临时卡片（测试用，稍后删除）"
EXAMPLES = (TITLE_A, TITLE_B, TITLE_C, TITLE_D)


def reset_examples() -> int:
    """清掉上一轮本脚本自己建的示例卡（可重复跑），绝不碰其它数据。"""
    data = api("/api/prompt-templates")
    gone = 0
    for t in (data.get("data") or {}).get("templates") or []:
        if t.get("title") in EXAMPLES:
            api(f"/api/prompt-templates/item/{t['id']}", "DELETE")
            gone += 1
    return gone


def q(path: str) -> str:
    from urllib.parse import quote
    return quote(path, safe="")


def md_count(name: str) -> int:
    p = TPL_DIR / name
    if not p.exists():
        return 0
    return p.read_text(encoding="utf-8").count('<!-- CBTPL id=')


def tpl_by_title(title: str) -> dict:
    data = api("/api/prompt-templates")
    for t in (data.get("data") or {}).get("templates") or []:
        if t.get("title") == title:
            return t
    return {}


def goto_prompts(cdp: CDP) -> None:
    api("/api/ui", "POST", {"patch": {"route": "admin", "adminPage": "prompts"}})
    cdp.call("Page.navigate", {"url": BASE + "/#admin/prompts"})
    cdp.wait("!!document.querySelector('.pt-page')", 30)
    cdp.wait("document.querySelectorAll('.pt-card').length > 0", 30)


def fill_new_card(cdp: CDP, title: str, content: str, cat: str) -> None:
    cdp.js(f"""(() => {{
      const card = document.querySelector('.pt-card.is-new');
      if (!card) return false;
      const set = (sel, val, ev) => {{
        const el = card.querySelector(sel);
        el.value = val;
        el.dispatchEvent(new Event(ev, {{bubbles: true}}));
      }};
      set('[data-f="title"]', {json.dumps(title)}, 'input');
      set('[data-f="content"]', {json.dumps(content)}, 'input');
      set('[data-f="category"]', {json.dumps(cat)}, 'change');
      return true;
    }})()""")


def set_files(cdp: CDP, root_sel: str, files: list[Path]) -> bool:
    """点卡片上的上传按钮，再通过 CDP 把文件塞给隐藏的 file input（等同真人选文件）。"""
    if not click(cdp, root_sel + " [data-madd]"):
        return False
    cdp.pause(200)
    doc = cdp.call("DOM.getDocument", {"depth": -1})
    node = cdp.call("DOM.querySelector", {"nodeId": doc["root"]["nodeId"],
                                          "selector": "#ptMediaInput"})
    if not node.get("nodeId"):
        return False
    cdp.call("DOM.setFileInputFiles", {"files": [str(p) for p in files],
                                       "nodeId": node["nodeId"]})
    return True


def thumb_info(cdp: CDP, root_sel: str) -> list[tuple[str, str]]:
    return [(a, b) for a, b in cdp.js(
        f"[...document.querySelectorAll({json.dumps(root_sel + ' .pt-thumb')})]"
        ".map(e => [e.querySelector('.pt-thumb-kind').textContent.trim(),"
        " e.querySelector('.pt-thumb-num').textContent.trim()])") or []]


def viewer_box(cdp: CDP) -> list:
    return cdp.js("(() => {const b=document.querySelector('.pt-viewer-box');"
                  "if(!b) return null; const r=b.getBoundingClientRect();"
                  "return [Math.round(r.width), Math.round(r.height),"
                  " Number((r.width/r.height).toFixed(3))];})()")


def wait_thumbs(cdp: CDP, root_sel: str, n: int, timeout: float = 40) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        got = cdp.js(f"document.querySelectorAll({json.dumps(root_sel + ' .pt-thumb')}).length")
        if got == n:
            return True
        time.sleep(0.4)
    return False


def run(cdp: CDP) -> None:
    # ---------- A. 页面与数据基线 ----------
    before = {n: md_count(n) for n in ("图片.md", "视频.md", "音频.md")}
    check("A1 模板库页渲染、卡片列表有数据",
          cdp.js("document.querySelectorAll('.pt-card').length >= 10"))
    check("A2 页面标出 6 个数据文件 + 素材目录",
          cdp.js("document.querySelector('.pt-path').textContent.includes('图片-含参.md')"
                 " && document.querySelector('.pt-path').textContent.includes('提示词模板库')"))
    label = cdp.js("document.querySelector('.pt-media-dir').textContent.trim()")
    check("A3 素材目录指向 ComfyBridgeOutput/提示词模板库",
          "ComfyBridgeOutput" in (label or "") and "提示词模板库" in (label or ""), label)
    media_dir = Path(label)
    check("A4 素材目录已自动创建", media_dir.is_dir(), str(media_dir))
    check(f"A5 原有数据条数基线：图片{before['图片.md']} / 视频{before['视频.md']} / 音频{before['音频.md']}",
          before["图片.md"] == 650 and before["视频.md"] == 700 and before["音频.md"] == 650)

    # ---------- B. 新建「参考生图」并上传 2 图 + 1 视频 ----------
    click(cdp, "#ptAdd")
    check("B1 新增卡片出现，带上传按钮", cdp.wait("!!document.querySelector('.pt-card.is-new [data-madd]')", 8))
    title_a = TITLE_A
    fill_new_card(cdp, title_a, "【参考生图】按参考图的构图与光线重建画面：保留山脊线与湖面色温，"
                                "主体位置不变，只替换环境细节与时间感。", "image")
    ok = set_files(cdp, ".pt-card.is-new", [SAMPLES / "ref-山湖晨光.png",
                                            SAMPLES / "ref-人像氛围.png",
                                            SAMPLES / "ref-运镜演示.mp4"])
    check("B2 文件选择器可用（隐藏 input 就位）", ok)
    check("B3 三个缩略图渲染出来", wait_thumbs(cdp, ".pt-card.is-new", 3))
    info = thumb_info(cdp, ".pt-card.is-new")
    check("B4 类型标记 + 同类型序号正确（图1 图2 视1）",
          info == [("图片", "1"), ("图片", "2"), ("视频", "1")], str(info))
    check("B5 缩略图左上角有类型标记、右下角有序号",
          cdp.js("(() => {const t=document.querySelector('.pt-card.is-new .pt-thumb');"
                 "const k=t.querySelector('.pt-thumb-kind'),n=t.querySelector('.pt-thumb-num');"
                 "return getComputedStyle(k).position==='absolute' && k.getBoundingClientRect().top"
                 "< t.getBoundingClientRect().top+8 && n.getBoundingClientRect().bottom"
                 "> t.getBoundingClientRect().bottom-8;})()"))
    check("B6 上传后保存按钮变为可用（脏状态）",
          cdp.js("!document.querySelector('.pt-card.is-new [data-save]').disabled"))
    click(cdp, ".pt-card.is-new [data-save]")
    cdp.wait("!!document.querySelector('.pt-card.is-new')===false || true", 2)
    cdp.pause(1200)
    ta = tpl_by_title(title_a)
    check("B7 模板已保存且带 3 个素材", len(ta.get("media") or []) == 3,
          json.dumps([m.get("file") for m in ta.get("media") or []], ensure_ascii=False))
    check("B8 素材文件真的落在「提示词模板库」目录",
          all((media_dir / m["file"]).is_file() for m in ta.get("media") or []))
    check("B9 含参模板写进 图片-含参.md，原 图片.md 不变",
          md_count("图片-含参.md") == 1 and md_count("图片.md") == before["图片.md"],
          f"含参{md_count('图片-含参.md')} 原{md_count('图片.md')}")
    check("B10 md 里记录了 media 字段（文件名 + 类型）",
          "media: [" in (TPL_DIR / "图片-含参.md").read_text(encoding="utf-8")
          and ta["media"][0]["file"] in (TPL_DIR / "图片-含参.md").read_text(encoding="utf-8"))

    # ---------- C. 查看窗：图片 ----------
    card = f'.pt-card[data-id="{ta["id"]}"]'
    click(cdp, card + " .pt-thumb")
    check("C1 点图片缩略图弹出查看窗", cdp.wait("!!document.querySelector('.pt-viewer')", 8))
    box = viewer_box(cdp)
    check("C2 窗口比例 16:9", box and box[2] == 1.778, str(box))
    check("C3 图片自适应窗口（object-fit: contain）",
          cdp.js("(() => {const i=document.querySelector('.pt-viewer-img');"
                 "return !!i && getComputedStyle(i).objectFit==='contain';})()"))
    check("C4 图片右上角有「复制 + 下载」两个图标",
          cdp.js("document.querySelectorAll('.pt-viewer-tools button').length===2"
                 " && !!document.querySelector('[data-vcopy]') && !!document.querySelector('[data-vdl]')"))
    tools = cdp.js("(() => {const b=document.querySelector('.pt-viewer-tools').getBoundingClientRect();"
                   "const x=document.querySelector('.pt-viewer-box').getBoundingClientRect();"
                   "return [Math.round(x.right-b.right), Math.round(b.top-x.top)];})()")
    check("C5 图标贴在窗口右上角", tools and tools[0] < 24 and tools[1] < 24, str(tools))

    # 复制图片：真的写进剪贴板（需要先 grantPermissions）
    click(cdp, "[data-vcopy]")
    cdp.pause(2500)
    clip = cdp.js("""(async () => { try {
        const items = await navigator.clipboard.read();
        return items.map(i => i.types.join(',')).join('|');
      } catch (e) { return 'ERR ' + e.message; } })()""")
    check("C5b 复制按钮把图片写进了剪贴板", clip == "image/png", str(clip))

    # 下载：真的能取到附件
    dl = HTTP.get(f"/api/prompt-templates/media/{q(ta['media'][0]['file'])}"
                  f"?download=1&name={q(ta['media'][0]['name'])}")
    check("C6 下载返回附件 + 原始文件名",
          dl.status_code == 200 and "attachment" in dl.headers.get("content-disposition", "")
          and len(dl.content) == ta["media"][0]["size"], dl.headers.get("content-disposition", ""))
    click(cdp, "[data-vclose]")
    cdp.wait("!document.querySelector('.pt-viewer')", 5)
    check("C7 关闭按钮能关掉查看窗", not cdp.js("!!document.querySelector('.pt-viewer')"))

    # ---------- D. 查看窗：视频 / 音频，尺寸与图标差异 ----------
    click(cdp, card + " .pt-thumb:nth-of-type(3)")
    cdp.wait("!!document.querySelector('.pt-viewer')", 8)
    check("D1 视频弹窗里有可直接播放的播放器（controls）",
          cdp.js("(() => {const v=document.querySelector('.pt-viewer-video');"
                 "return !!v && v.controls && v.tagName==='VIDEO';})()"))
    check("D2 视频弹窗右上角只有下载，没有复制",
          cdp.js("!document.querySelector('[data-vcopy]') && !!document.querySelector('[data-vdl]')"))
    vbox = viewer_box(cdp)
    check("D3 视频弹窗与图片弹窗同样大小", vbox == box, f"{vbox} vs {box}")
    click(cdp, "[data-vclose]")
    cdp.pause(200)

    # ---------- E. 新建「参考生视频」：1 视频 + 1 图 ----------
    click(cdp, "#ptAdd")
    cdp.wait("!!document.querySelector('.pt-card.is-new')", 8)
    title_b = TITLE_B
    fill_new_card(cdp, title_b, "【参考生视频】沿用参考视频的运镜节奏与景别变化：起幅中景缓推，"
                                "两秒后切近景，收在侧逆光特写上。", "video")
    set_files(cdp, ".pt-card.is-new", [SAMPLES / "ref-光比测试.mp4", SAMPLES / "ref-山湖晨光.png"])
    check("E1 视频类模板的缩略图（视1 图1）", wait_thumbs(cdp, ".pt-card.is-new", 2)
          and thumb_info(cdp, ".pt-card.is-new") == [("视频", "1"), ("图片", "1")],
          str(thumb_info(cdp, ".pt-card.is-new")))
    click(cdp, ".pt-card.is-new [data-save]")
    cdp.pause(1200)
    tb = tpl_by_title(title_b)
    check("E2 视频模板存进 视频-含参.md，原 视频.md 不变",
          len(tb.get("media") or []) == 2 and md_count("视频-含参.md") == 1
          and md_count("视频.md") == before["视频.md"])
    cardb = f'.pt-card[data-id="{tb["id"]}"]'
    click(cdp, cardb + " .pt-thumb")
    cdp.wait("!!document.querySelector('.pt-viewer')", 8)
    check("E3 视频卡点开也是 16:9 同尺寸", viewer_box(cdp) == box)
    click(cdp, "[data-vclose]")
    cdp.pause(200)

    # ---------- F. 新建「参考生音乐」：2 段音频 ----------
    click(cdp, "#ptAdd")
    cdp.wait("!!document.querySelector('.pt-card.is-new')", 8)
    title_c = TITLE_C
    fill_new_card(cdp, title_c, "【参考生音乐】以参考音频的音色与调式为准，把四小节动机延展成 30 秒铺底，"
                                "保留拨弦颗粒感，低频留白。", "audio")
    set_files(cdp, ".pt-card.is-new", [SAMPLES / "ref-古筝动机.wav", SAMPLES / "ref-环境铺底.wav"])
    check("F1 音频缩略图序号 1、2", wait_thumbs(cdp, ".pt-card.is-new", 2)
          and thumb_info(cdp, ".pt-card.is-new") == [("音频", "1"), ("音频", "2")],
          str(thumb_info(cdp, ".pt-card.is-new")))
    click(cdp, ".pt-card.is-new [data-save]")
    cdp.pause(1200)
    tc = tpl_by_title(title_c)
    check("F2 音频模板存进 音频-含参.md",
          len(tc.get("media") or []) == 2 and md_count("音频-含参.md") == 1
          and md_count("音频.md") == before["音频.md"])
    cardc = f'.pt-card[data-id="{tc["id"]}"]'
    click(cdp, cardc + " .pt-thumb")
    cdp.wait("!!document.querySelector('.pt-viewer')", 8)
    check("F3 音频弹窗里是可直接播放的播放器",
          cdp.js("(() => {const a=document.querySelector('.pt-viewer-audio audio');"
                 "return !!a && a.controls;})()"))
    centered = cdp.js("(() => {const a=document.querySelector('.pt-viewer-audio audio');"
                      "const b=document.querySelector('.pt-viewer-box').getBoundingClientRect();"
                      "const r=a.getBoundingClientRect();"
                      "return Math.abs((r.left+r.width/2)-(b.left+b.width/2));})()")
    check("F4 音频播放器水平居中", centered is not None and centered < 3, f"偏移 {centered}px")
    check("F5 音频弹窗与其它两个同尺寸", viewer_box(cdp) == box)
    click(cdp, "[data-vclose]")
    cdp.pause(200)

    # ---------- G. 编辑：素材增删 + 跨文件移动 ----------
    click(cdp, "#ptAdd")
    cdp.wait("!!document.querySelector('.pt-card.is-new')", 8)
    title_d = TITLE_D
    fill_new_card(cdp, title_d, "临时：先不带素材保存，再加素材，验证会从 图片.md 挪进 图片-含参.md。", "image")
    click(cdp, ".pt-card.is-new [data-save]")
    cdp.pause(1200)
    td = tpl_by_title(title_d)
    check("G1 不带素材的模板存进 图片.md", md_count("图片.md") == before["图片.md"] + 1
          and len(td.get("media") or []) == 0, f"图片.md={md_count('图片.md')}")
    cardd = f'.pt-card[data-id="{td["id"]}"]'
    set_files(cdp, cardd, [SAMPLES / "ref-人像氛围.png"])
    check("G2 给已有模板加素材", wait_thumbs(cdp, cardd, 1))
    click(cdp, cardd + " [data-save]")
    cdp.pause(1200)
    td2 = tpl_by_title(title_d)
    check("G3 加了素材后自动挪到 图片-含参.md，图片.md 回退",
          len(td2.get("media") or []) == 1 and md_count("图片.md") == before["图片.md"]
          and md_count("图片-含参.md") == 2,
          f"图片.md={md_count('图片.md')} 含参={md_count('图片-含参.md')}")
    # 移除素材：缩略图右上角的 ×
    click(cdp, cardd + " .pt-thumb-x")
    cdp.pause(300)
    check("G4 移除缩略图后卡片上没有素材了",
          cdp.js(f"document.querySelectorAll({json.dumps(cardd + ' .pt-thumb')}).length") == 0)
    click(cdp, cardd + " [data-save]")
    cdp.pause(1200)
    td3 = tpl_by_title(title_d)
    check("G5 保存后素材从 md 里移除、文件也删掉",
          len(td3.get("media") or []) == 0 and md_count("图片-含参.md") == 1
          and not (media_dir / td2["media"][0]["file"]).exists(),
          f"含参={md_count('图片-含参.md')}")
    # 删除这张临时卡（只删自己建的）
    click(cdp, cardd + " [data-del]")
    cdp.wait("!!document.querySelector('#ptDelYes')", 6)
    click(cdp, "#ptDelYes")
    cdp.pause(1200)
    check("G6 删除临时卡后数据回到基线（只剩 3 条示例）",
          md_count("图片.md") == before["图片.md"] and md_count("图片-含参.md") == 1
          and not tpl_by_title(title_d))

    # ---------- H. 筛选与搜索仍按页面三分类工作 ----------
    tags = api("/api/prompt-templates")["data"]["templates"]
    check("H1 含参模板与普通模板合成同一个分类（图片 651 条）",
          len([t for t in tags if t["category"] == "image"]) == before["图片.md"] + 1,
          str(len([t for t in tags if t["category"] == "image"])))
    cdp.js("(() => {const i=document.querySelector('#ptQ'); i.value='山湖晨光';"
           "i.dispatchEvent(new Event('input',{bubbles:true}));})()")
    cdp.pause(900)
    check("H2 搜索能命中含参模板", cdp.js(
        f"!!document.querySelector('.pt-card[data-id=\"{ta['id']}\"]')"))
    cdp.js("(() => {const i=document.querySelector('#ptQ'); i.value='';"
           "i.dispatchEvent(new Event('input',{bubbles:true}));})()")
    cdp.pause(600)

    # ---------- I. 创作台速取小窗 ----------
    # 只改 hash 不会重新加载页面（路由只在首屏读一次），带一个新查询串强制整页刷新
    api("/api/ui", "POST", {"patch": {"route": "studio", "promptDock": True}})
    cdp.call("Page.navigate", {"url": BASE + f"/?e2e={int(time.time())}#studio"})
    cdp.wait("!!document.querySelector('.app .studio')", 30)
    cdp.wait("!!document.querySelector('.pt-dock') && !document.querySelector('.pt-dock').hidden", 30)
    cdp.wait("document.querySelectorAll('.pt-dcard').length > 0", 30)
    check("I1 速取小窗打开并显示模板", cdp.js("document.querySelectorAll('.pt-dcard').length > 0"))
    got = cdp.wait(f"!!document.querySelector('.pt-dcard[data-id=\"{ta['id']}\"] .pt-dthumb')", 20)
    check("I2 小窗卡片上能看到素材缩略图", got)
    check("I3 小窗缩略图带类型标记与序号", cdp.js(
        f"(() => {{const ds=[...document.querySelectorAll('.pt-dcard[data-id=\"{ta['id']}\"] .pt-dthumb')];"
        "return ds.length===3 && ds.map(d=>[d.querySelector('i').textContent.trim(),"
        "d.querySelector('b').textContent.trim()]).join('|')==='图片,1|图片,2|视频,1';})()"))
    click(cdp, f'.pt-dcard[data-id="{ta["id"]}"] .pt-dthumb')
    check("I4 小窗里点缩略图也能弹 16:9 查看窗", cdp.wait("!!document.querySelector('.pt-viewer')", 8)
          and viewer_box(cdp) == box)
    click(cdp, "[data-vclose]")
    cdp.pause(200)
    click(cdp, f'.pt-dcard[data-id="{tc["id"]}"] [data-dedit]')
    cdp.wait("!!document.querySelector('.pt-dock-editor:not([hidden])')", 8)
    check("I5 小窗编辑面板有素材行和上传按钮", cdp.js(
        "!!document.querySelector('.pt-dock-editor [data-mrow] [data-madd]')"))
    check("I6 小窗编辑面板能看到已有素材", cdp.js(
        "document.querySelectorAll('.pt-dock-editor [data-mrow] .pt-thumb').length===2"))
    check("I7 小窗里没有 JS 异常", not any(
        e.get("method") == "Runtime.exceptionThrown" for e in cdp.events),
        str([e for e in cdp.events if e.get("method") == "Runtime.exceptionThrown"][:1]))
    click(cdp, ".pt-ed-hd [data-ed-back]")
    api("/api/ui", "POST", {"patch": {"promptDock": False}})


def main() -> int:
    try:
        HTTP.get("/api/state", timeout=5)
    except Exception:  # noqa: BLE001
        print("服务没有在 127.0.0.1:8190 运行，请先启动服务")
        return 1
    proc, cdp = base.start_browser()
    try:
        cdp.call("DOM.enable")
        cdp.call("Page.setInterceptFileChooserDialog", {"enabled": True})
        # 图片查看窗的「复制」要写剪贴板，先把权限给上，否则只能看到失败提示
        try:
            cdp.call("Browser.grantPermissions", {
                "permissions": ["clipboardReadWrite", "clipboardSanitizedWrite"]})
        except Exception:  # noqa: BLE001 - 拿不到权限就跳过这一项
            pass
        reset_examples()
        goto_prompts(cdp)
        run(cdp)
    finally:
        try:
            api("/api/ui", "POST", {"patch": {"route": "admin", "adminPage": "prompts",
                                              "promptDock": False}})
        except Exception:  # noqa: BLE001
            pass
        cdp.close()
        proc.terminate()
    passed = sum(1 for _n, ok, _d in base.RESULTS if ok)
    print(f"\n===== 提示词模板库素材 E2E：{passed}/{len(base.RESULTS)} 项通过 =====")
    for name, ok, detail in base.RESULTS:
        if not ok:
            print(f"  FAIL: {name} — {detail}")
    return 0 if passed == len(base.RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
