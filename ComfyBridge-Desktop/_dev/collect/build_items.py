"""把作者写的「创作要点」TSV 组装成可导入的模板清单。

输入
  specs/video.tsv   key|role|title|assets|主体|场景|风格|镜头|补充|时长|参考描述
  specs/image.tsv   同上（镜头列写构图，补充列写笔触）
  specs/audio.tsv   key|role|title|preset|bpm|root|情绪|用途|时长|微调
输出
  media_plan.json   需要下载的原生素材
  items.json        标题 / 正文 / 分类 / 文件清单
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

PARSE_ONLY = os.environ.get("PARSE_ONLY") == "1"


def ensure_src(cid: str) -> Path:
    """取得一条原视频（裁剪过）。PARSE_ONLY 时只登记下载任务。"""
    src = MEDIA / f"src_{cid}.mp4"
    if not PARSE_ONLY and not src.exists():
        mlib.video_of(cat, cid, src)
    return src

HERE = Path(__file__).resolve().parent
MEDIA = HERE / "media"
sys.path.insert(0, str(HERE))

import compose  # noqa: E402
import media as mlib  # noqa: E402
import synth_audio  # noqa: E402
import music_presets  # noqa: E402
from music_presets import PRESETS, MOODS, USES  # noqa: E402

ROLE_TAGS = {
    "i2v": ["图生视频", "运镜设计"], "flf": ["首尾帧生视频", "首尾帧"],
    "swap": ["角色替换", "人物"], "board": ["故事板生视频", "故事板"],
    "i2i": ["风格化"], "sheet": ["故事板", "角色人物", "场景环境"],
    "music": ["影视配乐", "纯音乐"], "cover": ["影视配乐", "纯音乐"],
}
STYLE_TAGS = [
    ("水墨", "水墨"), ("油画", "油画"), ("水彩", "水彩"), ("像素", "像素艺术"),
    ("赛博", "赛博朋克"), ("黑白", "黑白"), ("三维", "3D渲染"), ("3D", "3D渲染"),
    ("极简", "极简"), ("建筑", "建筑"), ("美食", "美食"), ("时尚", "时尚大片"),
    ("写实", "写实"), ("电影", "电影感"), ("国风", "国风"), ("古风", "古风"),
    ("科幻", "科幻"), ("素描", "素描速写"), ("线稿", "素描速写"), ("炭笔", "素描速写"),
    ("复古", "复古"), ("超现实", "超现实"), ("暗调", "电影感"), ("纪实", "纪实"),
    ("产品", "广告产品"), ("人像", "人像"), ("工业", "游戏概念"), ("奇幻", "游戏概念"),
]
GENRE_TAGS = {
    "古风国乐": ["国风"], "电影配乐": ["史诗"], "Lo-Fi 嘻哈": ["Lo-fi"], "未来贝斯": ["电子"],
    "爵士三重奏": ["爵士"], "氛围环境": ["氛围纯音乐"], "国风电子": ["国风", "电子"],
    "摇滚": ["摇滚"], "City Pop": ["流行"], "西藏/世界音乐": ["氛围纯音乐"],
    "蒸汽波": ["电子"], "Trap": ["嘻哈说唱"], "House": ["电子"], "凯尔特民谣": ["民谣"],
    "Bossa Nova": ["拉丁"], "恐怖悬疑": ["紧张"], "轻快电子": ["电子"],
    "蓝调": ["布鲁斯"], "探戈": ["拉丁"], "童声/摇篮": ["儿童"],
}
MOOD_TAGS = [("清冷", "伤感"), ("悲伤", "伤感"), ("孤独", "伤感"), ("压抑", "伤感"),
             ("轻快", "欢快"), ("好心情", "欢快"), ("明亮", "欢快"), ("温暖", "治愈放松"),
             ("安心", "治愈放松"), ("松弛", "治愈放松"), ("紧张", "紧张"), ("不安", "紧张"),
             ("宿命", "史诗"), ("辽阔", "史诗"), ("怀旧", "怀旧"), ("回忆", "怀旧")]


def tags_for(it: dict) -> list[str]:
    role = it["role"]
    out = list(ROLE_TAGS.get(role, []))
    blob = it.get("style_blob", "")
    for kw, tag in STYLE_TAGS:
        if kw in blob and tag not in out:
            out.append(tag)
            if len(out) >= 4:
                break
    for kw, tag in GENRE_TAGS.items():
        if kw == it.get("preset"):
            out = [t for t in out if t not in ("影视配乐", "纯音乐")] + tag + ["影视配乐", "纯音乐"]
            break
    for kw, tag in MOOD_TAGS:
        if kw in it.get("mood", "") and tag not in out:
            out.append(tag)
            break
    seen, final = set(), []
    for t in out:
        if t not in seen:
            seen.add(t)
            final.append(t)
    return final[:4]


CAT_OF = {"i2v": "video", "flf": "video", "swap": "video", "board": "video",
          "i2i": "image", "sheet": "image", "music": "audio", "cover": "audio"}


def read_tsv(prefix: str) -> list[list[str]]:
    """读 specs/<prefix>*.tsv，按文件名顺序拼起来，方便分批写。"""
    rows = []
    for p in sorted((HERE / "specs").glob(prefix + "*.tsv")):
        for line in p.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            rows.append([c.strip() for c in line.split("|")])
    return rows


def _size_cache() -> dict[str, list[int]]:
    """尺寸缓存（文件 -> [宽,高]），免得每次重建都去 ffprobe 一遍。"""
    p = HERE / "sizes.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _probe(path: Path, cache: dict[str, list[int]]) -> list[int] | None:
    key = path.name
    if key in cache:
        return cache[key]
    size = None
    try:
        if path.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"):
            from PIL import Image
            with Image.open(path) as im:
                size = list(im.size)
        elif path.suffix.lower() in (".mp4", ".mov", ".webm"):
            import subprocess
            out = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=width,height", "-of", "csv=p=0", str(path)],
                capture_output=True, text=True).stdout.strip()
            w, h = out.split(",")[:2]
            size = [int(w), int(h)]
    except Exception:  # noqa: BLE001
        size = None
    if size:
        cache[key] = size
    return size


def _primary_size(it: dict, cache: dict[str, list[int]]) -> list[int]:
    """取这条模板「主参考」的宽高：优先图片，其次视频。"""
    files = it["files"]
    order = [f for f in files if f.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))]
    order += [f for f in files if f not in order]
    for fn in order:
        size = _probe(MEDIA / fn, cache)
        if size:
            return size
    return [1536, 864]


def main() -> None:
    global cat
    cat = json.loads((HERE / "catalog.json").read_text(encoding="utf-8"))
    MEDIA.mkdir(parents=True, exist_ok=True)
    plan: list[dict] = []
    items: list[dict] = []

    # ---------------- 视频 ----------------
    for row in read_tsv("video"):
        key, role, title, assets, body, scene, style, lens, extra, dur, descs = row[:11]
        ids = [a.strip() for a in assets.split(",") if a.strip()]
        desc_list = [d.strip() for d in descs.split(";")]
        refs, files = [], []

        def add_image(cid: str, role_cn: str, i: int) -> None:
            fn = f"{key}_{i}.jpg"
            plan.append({"op": "image", "id": cid, "file": fn})
            refs.append({"kind": "image", "role": role_cn, "desc": desc_list[len(refs)]})
            files.append(fn)

        if role == "i2v":
            add_image(ids[0][2:] if ids[0][:2] in ("p:", "v:") else ids[0], "首帧", 1)
            if len(ids) > 1 and ids[1].startswith("v:"):
                cid = ids[1][2:]
                fn = f"{key}_2.mp4"
                plan.append({"op": "video", "id": cid, "file": fn})
                refs.append({"kind": "video", "role": "运镜参考", "desc": desc_list[len(refs)]})
                files.append(fn)
        elif role == "flf":
            cid = ids[0][2:]
            plan.append({"op": "video", "id": cid, "file": f"src_{cid}.mp4"})
            src = ensure_src(cid)
            f1, f2 = MEDIA / f"{key}_1.jpg", MEDIA / f"{key}_2.jpg"
            if not PARSE_ONLY:
                mlib.frame_pair(src, f1, f2)
            refs.append({"kind": "image", "role": "首帧", "desc": desc_list[0]})
            refs.append({"kind": "image", "role": "尾帧", "desc": desc_list[1]})
            files += [f1.name, f2.name]
        elif role == "swap":
            vid = [a for a in ids if a.startswith("v:")][0][2:]
            img = [a for a in ids if a.startswith("p:")][0][2:]
            fn = f"{key}_1.mp4"
            plan.append({"op": "video", "id": vid, "file": fn})
            refs.append({"kind": "video", "role": "原片", "desc": desc_list[0]})
            files.append(fn)
            add_image(img, "人物参考", 2)
        elif role == "board":
            cid = ids[0][2:]
            plan.append({"op": "video", "id": cid, "file": f"src_{cid}.mp4"})
            src = ensure_src(cid)
            sheet = MEDIA / f"{key}_1.jpg"
            if not PARSE_ONLY:
                mlib.storyboard_sheet(src, sheet, 6)
            refs.append({"kind": "image", "role": "故事板", "desc": desc_list[0]})
            files.append(sheet.name)
            if len(ids) > 1 and ids[1].startswith("v:"):
                cid2 = ids[1][2:]
                fn = f"{key}_2.mp4"
                plan.append({"op": "video", "id": cid2, "file": fn})
                refs.append({"kind": "video", "role": "运镜参考", "desc": desc_list[1]})
                files.append(fn)
        else:
            raise RuntimeError("未知视频角色 " + role)

        items.append({
            "key": key, "category": "video", "title": title, "files": files,
            "role": role, "style_blob": style + lens + extra,
            "spec": {"key": key, "category": "video", "role": role, "duration": float(dur),
                     "refs": refs, "主体": body, "场景": scene, "风格": style,
                     "镜头": lens, "补充": extra},
        })

    # ---------------- 图片 ----------------
    for row in read_tsv("image"):
        key, role, title, assets, body, scene, style, comp, stroke, extra, descs = row[:11]
        ids = [a.strip() for a in assets.split(",") if a.strip()]
        desc_list = [d.strip() for d in descs.split(";")]
        refs, files = [], []
        roles = {"i2i": ["原图", "风格参考", "构图参考"],
                 "sheet": ["角色参考", "场景参考", "构图参考"]}[role]
        for i, a in enumerate(ids):
            cid = a[2:] if a[:2] in ("p:", "v:") else a
            fn = f"{key}_{i+1}.jpg"
            plan.append({"op": "image", "id": cid, "file": fn})
            refs.append({"kind": "image", "role": roles[min(i, 2)], "desc": desc_list[i]})
            files.append(fn)

        items.append({
            "key": key, "category": "image", "title": title, "files": files,
            "role": role, "style_blob": style + comp + stroke + extra,
            "spec": {"key": key, "category": "image", "role": role, "refs": refs,
                     "主体": body, "场景": scene, "风格": style, "构图": comp,
                     "笔触": stroke, "补充": extra},
        })

    # ---------------- 音频 ----------------
    for row in read_tsv("audio"):
        key, role, title, pid, bpm, root, mood, use, dur, tune = row[:10]
        pre = PRESETS[int(pid)]
        bpm_i = int(bpm) if bpm else pre["bpm"][0]
        root_s = root or pre["root"][0]
        dur_i = int(dur or 40)
        dur_i = max(32, min(52, dur_i))
        base = {
            "bpm": bpm_i, "root": root_s, "scale": pre["mode"], "mode_cn": pre["mode_cn"],
            "progression": pre["prog"][0], "progression_cn": pre["prog_cn"],
            "pad": pre["pad"], "pluck": pre["pluck"], "drums": pre["drums"],
            "lead": pre["lead"], "vibrato": pre["vibrato"], "wet": pre["wet"],
            "bars": 16, "seed": abs(hash(key)) % 100000,
        }
        refs, files = [], []
        if role == "music":
            fn = f"{key}_1.mp3"
            spec = dict(base, key=key)
            if not PARSE_ONLY:
                synth_audio.render(spec, MEDIA / fn)
            refs.append({"kind": "audio", "role": "参考曲",
                         "desc": f"{pre['name']}参考，{bpm_i}BPM，{root_s} {pre['mode_cn']}，{pre['配器']}"})
            files.append(fn)
        else:  # cover：原曲 + 音色样本
            fn1, fn2 = f"{key}_1.mp3", f"{key}_2.mp3"
            if not PARSE_ONLY and not (MEDIA / fn1).exists():
                synth_audio.render(dict(base, key=key), MEDIA / fn1)
            timbre = dict(base, key=key + "_timbre", bars=6, drums="none", pluck="no",
                          arp=False, wet=0.22, lead=pre["lead"])
            if not PARSE_ONLY and not (MEDIA / fn2).exists():
                synth_audio.render(timbre, MEDIA / fn2)
            refs.append({"kind": "audio", "role": "原曲",
                         "desc": f"原曲，{bpm_i}BPM，{root_s} {pre['mode_cn']}，{pre['配器']}"})
            refs.append({"kind": "audio", "role": "音色样本",
                         "desc": f"音色样本，单件主奏，{pre['mode_cn']}的共鸣与咬字质感"})
            files += [fn1, fn2]

        items.append({
            "key": key, "category": "audio", "title": title, "files": files,
            "role": role, "preset": pre["name"], "mood": mood + pre["情绪"][0],
            "spec": {"key": key, "category": "audio", "role": role, "refs": refs,
                     "bpm": bpm_i, "root": root_s, "mode_cn": pre["mode_cn"],
                     "progression_cn": pre["prog_cn"],
                     "配器": pre["配器"] + (f"；{tune}" if tune else ""),
                     "前奏": "铺底与单件主奏", "主段": "主奏进入，节奏组跟进",
                     "抬升": "加一层对位与打击乐，动态推到最高",
                     "律动": pre["律动"], "情绪": mood or pre["情绪"][0],
                     "用途": use or USES[0], "duration": dur_i,
                     "preset": pre["name"]},
        })

    # 组装正文（先把参考素材的实际尺寸量出来，画幅参数才能跟素材同比例）
    _dims = _size_cache()
    for it in items:
        if PARSE_ONLY:
            break
        if it["category"] in ("video", "image"):
            it["spec"]["尺寸"] = _primary_size(it, _dims)
        it["content"] = compose.build(it["spec"])
        it["chars"] = compose._len(it["content"])
        it["tags"] = tags_for(it)
        it.pop("style_blob", None)
        it.pop("preset", None)
        it.pop("mood", None)
        it.pop("role", None)
        del it["spec"]

    (HERE / "media_plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=1),
                                          encoding="utf-8")
    if not PARSE_ONLY:
        (HERE / "sizes.json").write_text(json.dumps(_dims, ensure_ascii=False, indent=1),
                                         encoding="utf-8")
        (HERE / "items.json").write_text(json.dumps(items, ensure_ascii=False, indent=1),
                                         encoding="utf-8")
    from collections import Counter
    print("条目", len(items), dict(Counter(x["category"] for x in items)))
    if PARSE_ONLY:
        print("（仅解析，未下载/未合成/未成文）")
    print("待下载原生素材", len(plan))
    if PARSE_ONLY:
        return
    bad = [x for x in items if (x["category"] == "image" and x["chars"] < 500)
           or (x["category"] != "image" and x["chars"] < 800)]
    print("字数未达标", len(bad), [x["key"] for x in bad][:10])
    print("字数区间", min(x["chars"] for x in items), "-", max(x["chars"] for x in items))


if __name__ == "__main__":
    sys.exit(main())
