"""导入后的验收：接口数据 + 真实浏览器界面。

检查项
1. 采集条目数 / 分类分布 / 素材总数；
2. 字数：图片 ≥500、视频与音频 ≥800（示例三条除外）；
3. 提示词里的 [图片 N]/[视频 N]/[音频 N] 与卡片素材严格一一对应；
4. 每个素材文件都能取到（含 ?download=1 原名下载）；
5. 界面：缩略图类型标记与同类序号、16:9 查看窗、图片复制/下载、视频可播、音频居中、窄屏不溢出。
"""
from __future__ import annotations

import base64
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

APP = Path("D:/workspace/WorkBuddyProject/ComfyBridge-Desktop")
sys.path.insert(0, str(APP / "tests"))
import ui_e2e_test as base  # noqa: E402

API = "http://127.0.0.1:8190/api/prompt-templates"
SHOTS = APP / "_dev" / "_shots500"
SHOTS.mkdir(parents=True, exist_ok=True)
KIND_CN = {"image": "图片", "video": "视频", "audio": "音频"}
# 需求 A 留下的功能演示条目（短文本、混合素材），不参与字数统计
DEMO = {"参考生图 · 山湖晨光构图复位", "参考生视频 · 运镜节奏复刻",
        "参考生音乐 · 古筝动机延展"}
PFX = {"图生视频": "video", "首尾帧": "video", "人物替换": "video",
       "故事板生视频": "video", "图生图": "image", "参考图出故事板": "image",
       "参考生音乐": "audio", "音乐翻唱": "audio"}


def _pfx_cat(title: str) -> str:
    head = title.split(" · ")[0]
    return PFX.get(head, "")


def api_check() -> list[tuple[str, bool, str]]:
    out: list[tuple[str, bool, str]] = []
    d = httpx.get(API, timeout=180).json()["data"]
    tpls = d["templates"]
    media_tpls = [t for t in tpls if t.get("media")]
    coll = [t for t in media_tpls if t["title"] not in DEMO]

    dist: dict[str, int] = {}
    pfx_num: dict[str, int] = {}
    for t in coll:
        c = _pfx_cat(t["title"])
        dist[c] = dist.get(c, 0) + 1
        pfx_num[t["title"].split(" · ")[0]] = pfx_num.get(t["title"].split(" · ")[0], 0) + 1
    total_media = sum(len(t["media"]) for t in media_tpls)

    out.append(("采集条目总数 ≥500", len(coll) >= 500, f"{len(coll)} 条（另有演示 {len(media_tpls)-len(coll)} 条）"))
    out.append(("视频 ≥100 / 音乐 ≥100",
                dist.get("video", 0) >= 100 and dist.get("audio", 0) >= 100,
                f"视频 {dist.get('video',0)} / 图片 {dist.get('image',0)} / 音频 {dist.get('audio',0)}"))
    out.append(("八类细分齐全", all(pfx_num.get(k, 0) >= 40 for k in PFX),
                " ".join(f"{k}{pfx_num.get(k,0)}" for k in PFX)))
    out.append(("素材文件条数", total_media >= 700, f"{total_media} 个"))

    bad_len, bad_ref, bad_cat = [], [], []
    for t in media_tpls:
        body = t["content"]
        need = 500 if t["category"] == "image" else 800
        if t["title"] not in DEMO and len(re.sub(r"\s", "", body)) < need:
            bad_len.append((t["title"], len(re.sub(r"\s", "", body))))
        cnt = {"image": 0, "video": 0, "audio": 0}
        for m in t["media"]:
            cnt[m["kind"]] += 1
        for kind, n in cnt.items():
            for i in range(1, n + 1):
                if f"[{KIND_CN[kind]} {i}]" not in body:
                    bad_ref.append((t["title"], f"缺 [{KIND_CN[kind]} {i}]"))
            if f"[{KIND_CN[kind]} {n + 1}]" in body:
                bad_ref.append((t["title"], f"多写 [{KIND_CN[kind]} {n + 1}]"))
        kinds = {m["kind"] for m in t["media"]}
        if t["title"] not in DEMO:
            # 图生视频/首尾帧/故事板天然只带图片，视频类允许「图片」或「视频」；
            # 图片类必须带图，音频类必须带音频。
            want = {"video": {"video", "image"}, "image": {"image"}, "audio": {"audio"}}[t["category"]]
            if not (kinds & want):
                bad_cat.append((t["title"], t["category"], sorted(kinds)))

    out.append(("图片≥500 字 / 音视频≥800 字", not bad_len,
                f"不达标 {len(bad_len)} 条 {bad_len[:3]}"))
    out.append(("引用标记与素材一一对应", not bad_ref,
                f"异常 {len(bad_ref)} 处 {bad_ref[:3]}"))
    out.append(("分类与素材类型一致", not bad_cat, f"异常 {len(bad_cat)} 条 {bad_cat[:3]}"))

    # ---- 素材可取性：全部逐个拉一遍首字节
    targets = [(t["title"], m) for t in media_tpls for m in t["media"]]
    errs: list[str] = []

    def probe(item: tuple[str, dict]):
        title, m = item
        url = f"{API}/media/{m['file']}"
        try:
            with httpx.stream("GET", url, timeout=60,
                              headers={"Range": "bytes=0-1023"}) as r:
                r.read()
                if r.status_code not in (200, 206):
                    return f"{title}::{m['name']} HTTP {r.status_code}"
                ctype = r.headers.get("content-type", "")
                want = {"image": "image/", "video": "video/", "audio": "audio/"}[m["kind"]]
                if not ctype.startswith(want):
                    return f"{title}::{m['name']} type={ctype}"
        except Exception as e:  # noqa: BLE001
            return f"{title}::{m['name']} {type(e).__name__}"
        return ""

    with ThreadPoolExecutor(max_workers=16) as pool:
        errs = [x for x in pool.map(probe, targets) if x]
    out.append(("素材全部可读", not errs, f"{len(targets)} 个文件，失败 {len(errs)} {errs[:3]}"))

    # ---- 原名下载
    dl_err = []
    for kind in ("image", "video", "audio"):
        one = next((m for t in media_tpls for m in t["media"] if m["kind"] == kind), None)
        if not one:
            dl_err.append(kind + " 无样本")
            continue
        r = httpx.get(f"{API}/media/{one['file']}?download=1&name={one['name']}", timeout=60)
        cd = r.headers.get("content-disposition", "")
        if r.status_code != 200 or "attachment" not in cd:
            dl_err.append(f"{kind} {r.status_code} {cd[:40]}")
    out.append(("三类素材均可原名下载", not dl_err, "; ".join(dl_err) or "content-disposition 正常"))

    # ---- 标签
    tags_used = {x for t in coll for x in t.get("tags", [])}
    out.append(("采集条目带标签", len(tags_used) >= 8, f"用到 {len(tags_used)} 个标签 / 库内 {len(d['tags'])} 个"))
    return out


# ------------------------------------------------------------------ 界面
def shot(cdp, name: str, clip: dict | None = None) -> None:
    kw: dict = {"format": "png"}
    if clip:
        kw["clip"] = {**clip, "scale": 1}
    (SHOTS / name).write_bytes(base64.b64decode(cdp.call("Page.captureScreenshot", kw)["data"]))
    print("  shot", name)


def card_clip(cdp, tid: str) -> dict:
    return cdp.js(f"""(()=>{{const c=document.querySelector('.pt-card[data-id="{tid}"]');
      if(!c) return null; const b=c.getBoundingClientRect(); const sy=scrollY;
      return {{x:Math.max(0,Math.round(b.left)-10),y:Math.round(b.top)+sy-10,
               width:Math.round(b.width)+20,height:Math.round(b.height)+20}};}})()""")


def find_card(cdp, title: str) -> str:
    """用搜索框把目标卡片筛出来，返回它的 data-id。"""
    cdp.js(f"""(()=>{{const q=document.querySelector('#ptQ'); q.value={json.dumps(title)};
      q.dispatchEvent(new Event('input',{{bubbles:true}}));}})()""")
    time.sleep(1.2)
    return cdp.js(f"""(()=>{{const c=[...document.querySelectorAll('.pt-card')]
      .find(x=>{{const t=x.querySelector('.pt-title'); return t && t.value.trim()==={json.dumps(title)};}});
      if(!c) return ''; window.scrollTo(0, c.getBoundingClientRect().top+scrollY-160); return c.dataset.id;}})()""")


def open_thumb(cdp, tid: str, kind_cn: str, idx: int = 0) -> bool:
    return bool(cdp.js(f"""(()=>{{const c=document.querySelector('.pt-card[data-id="{tid}"]');
      if(!c) return false; const ts=[...c.querySelectorAll('.pt-thumb')]
        .filter(x=>{{const k=x.querySelector('.pt-thumb-kind'); return k && k.textContent.trim()==='{kind_cn}';}});
      if(ts.length<={idx}) return false; ts[{idx}].click(); return true;}})()"""))


def ui_check() -> None:
    base.api("/api/ui", "POST", {"patch": {"route": "admin", "adminPage": "prompts", "promptDock": False}})
    proc, cdp = base.start_browser()
    try:
        cdp.call("Emulation.setDeviceMetricsOverride",
                 {"width": 1440, "height": 1060, "deviceScaleFactor": 1, "mobile": False})
        cdp.call("Page.navigate", {"url": base.BASE + f"/?v={int(time.time())}#admin/prompts"})
        cdp.wait("document.querySelectorAll('.pt-card').length>2", 40)
        time.sleep(3)
        print("  首屏卡片", cdp.js("document.querySelectorAll('.pt-card').length"),
              "| 缩略图", cdp.js("document.querySelectorAll('.pt-thumb').length"),
              "|", cdp.js("document.querySelector('#ptCount').textContent"))
        shot(cdp, "01-cards.png")

        # 视频类演示卡：2 视频 + 1 图片，验证类型标记与同类序号
        tid = find_card(cdp, "参考生视频 · 运镜节奏复刻")
        print("  定位卡片", tid)
        if tid:
            shot(cdp, "02-card-video.png", card_clip(cdp, tid))
            print("  类型标记", cdp.js(f"""[...document.querySelectorAll('.pt-card[data-id="{tid}"] .pt-thumb-kind')].map(x=>x.textContent)"""))
            print("  同类序号", cdp.js(f"""[...document.querySelectorAll('.pt-card[data-id="{tid}"] .pt-thumb-num')].map(x=>x.textContent)"""))
            print("  上传按钮", cdp.js(f"""document.querySelectorAll('.pt-card[data-id="{tid}"] .pt-thumb-x, .pt-card[data-id="{tid}"] .pt-madd').length"""))
            # 视频查看窗
            open_thumb(cdp, tid, "视频", 0)
            time.sleep(2.5)
            print("  视频窗", cdp.js("""(()=>{const v=document.querySelector('.pt-viewer-video');
              const b=document.querySelector('.pt-viewer-box').getBoundingClientRect();
              return [Math.round(b.width),Math.round(b.height),Number((b.width/b.height).toFixed(3)),
                      v?v.readyState:-1, v?Number(v.currentTime.toFixed(2)):-1,
                      v?v.videoWidth+'x'+v.videoHeight:''];})()"""))
            shot(cdp, "03-viewer-video.png")
            cdp.js("document.querySelector('.pt-viewer-close').click()")

        # 图片查看窗：复制 + 下载
        tid2 = find_card(cdp, "参考生图 · 山湖晨光构图复位")
        if tid2:
            open_thumb(cdp, tid2, "图片", 0)
            time.sleep(1.8)
            print("  图片窗", cdp.js("""(()=>{const b=document.querySelector('.pt-viewer-box').getBoundingClientRect();
              const img=document.querySelector('.pt-viewer-img');
              return [Math.round(b.width),Math.round(b.height),Number((b.width/b.height).toFixed(3)),
                      !!document.querySelector('[data-vcopy]'), !!document.querySelector('[data-vdl]'),
                      img?img.naturalWidth+'x'+img.naturalHeight:''];})()"""))
            shot(cdp, "04-viewer-image.png")
            cdp.js("document.querySelector('.pt-viewer-close').click()")

        # 音频查看窗：居中
        tid3 = find_card(cdp, "参考生音乐 · 古筝动机延展")
        if tid3:
            open_thumb(cdp, tid3, "音频", 0)
            time.sleep(1.8)
            print("  音频窗", cdp.js("""(()=>{const b=document.querySelector('.pt-viewer-box').getBoundingClientRect();
              const a=document.querySelector('.pt-viewer-audio audio');
              const w=document.querySelector('.pt-viewer-audio');
              const ab=a.getBoundingClientRect(); const wb=w.getBoundingClientRect();
              return [Math.round(b.width),Math.round(b.height),Number((b.width/b.height).toFixed(3)),
                      Math.round(ab.left-b.left), Math.round((b.right-ab.right)),
                      Math.round((wb.left+wb.width/2)-(b.left+b.width/2)), a?a.readyState:-1];})()"""))
            shot(cdp, "05-viewer-audio.png")
            cdp.js("document.querySelector('.pt-viewer-close').click()")

        # 清空搜索，回到全量
        cdp.js("""(()=>{const q=document.querySelector('#ptQ'); q.value='';
          q.dispatchEvent(new Event('input',{bubbles:true}));})()""")
        time.sleep(1.2)
        # 滚到底加载更多，确认无报错
        cdp.js("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(2)
        print("  滚动后卡片", cdp.js("document.querySelectorAll('.pt-card').length"),
              "|", cdp.js("""(document.querySelector('#ptMore')||{}).textContent||'无更多标记'"""))

        # 窄屏不溢出
        cdp.call("Emulation.setDeviceMetricsOverride",
                 {"width": 430, "height": 900, "deviceScaleFactor": 2, "mobile": True})
        time.sleep(1.5)
        print("  窄屏溢出", cdp.js("""(()=>{const d=document.documentElement;
          return [d.scrollWidth, d.clientWidth, d.scrollWidth-d.clientWidth];})()"""))
        shot(cdp, "06-narrow.png")
    finally:
        cdp.close()
        proc.terminate()


def main() -> None:
    print("== 接口校验 ==")
    rows = api_check()
    for name, ok, detail in rows:
        print(("  ✔ " if ok else "  ✘ ") + name + "：" + detail)
    print("== 界面校验 ==")
    ui_check()
    print("完成，截图在", SHOTS)


if __name__ == "__main__":
    main()
