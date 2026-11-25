"""抓取 Pexels 图片 / 视频搜索结果，产出可用的素材目录。

只做采集与解析，不下载大文件；结果落 catalog.json。
页面里的数据是 Next.js 流式 JSON，条目形如
  {"id":"123","type":"photo","attributes":{...}}
  {"id":"456","type":"video","attributes":{...}}
这里用大括号配对把每个 attributes 对象切出来，再取字段。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE = HERE / "html"
OUT = HERE / "catalog.json"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9",
           "Accept": "text/html,application/xhtml+xml"}


def fetch(url: str, tag: str, force: bool = False) -> str:
    """用 curl 取页面：Python urllib 会被 Pexels 的 Cloudflare 403，curl 正常。"""
    CACHE.mkdir(parents=True, exist_ok=True)
    p = CACHE / (tag + ".html")
    if p.exists() and p.stat().st_size > 50000 and not force:
        return p.read_text(encoding="utf-8", errors="ignore")
    proxy = os.environ.get("https_proxy") or os.environ.get("HTTPS_PROXY") or ""
    for attempt in range(3):
        cmd = ["curl", "-sL", "-m", "45", "-A", UA,
               "-H", "Accept-Language: en-US,en;q=0.9", url]
        if proxy:
            cmd[1:1] = ["-x", proxy]
        r = subprocess.run(cmd, capture_output=True)
        html = r.stdout.decode("utf-8", "ignore")
        if len(html) > 20000:
            p.write_text(html, encoding="utf-8")
            return html
        print("  retry", attempt + 1, "len", len(html), r.stderr.decode("utf-8", "ignore")[:80], flush=True)
        time.sleep(2 + attempt * 2)
    raise RuntimeError("抓取失败 " + url)


def slice_object(text: str, start: int) -> str:
    """从 start（指向 '{'）开始按大括号配对切一段 JSON 文本。"""
    depth = 0
    i = start
    n = len(text)
    in_str = False
    esc = False
    while i < n:
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
        i += 1
    return text[start:]


def get_str(blob: str, key: str, default: str = "") -> str:
    m = re.search(r'"' + key + r'":"((?:[^"\\]|\\.)*)"', blob)
    if not m:
        return default
    try:
        return json.loads('"' + m.group(1) + '"')
    except Exception:  # noqa: BLE001
        return m.group(1)


def get_int(blob: str, key: str, default: int = 0) -> int:
    m = re.search(r'"' + key + r'":(\d+)', blob)
    return int(m.group(1)) if m else default


def get_tags(blob: str) -> list[str]:
    m = re.search(r'"tags":\[(.*?)\],"liked"', blob, re.S)
    if not m:
        m = re.search(r'"tags":\[(.*?)\]', blob, re.S)
    if not m:
        return []
    return [get_str(x, "name") for x in re.findall(r'\{"name":"[^"]*"[^}]*\}', m.group(1))]


def parse_items(html: str) -> list[dict]:
    html = html.replace('\\"', '"').replace("\\u0026", "&").replace("\\u0027", "'")
    items: list[dict] = []
    for m in re.finditer(r'\{"id":"(\d+)","type":"(photo|video)","attributes":\{', html):
        kind = m.group(2)
        start = html.index("{", m.end() - 1)
        blob = slice_object(html, start)
        if len(blob) < 200:
            continue
        if kind == "photo":
            img = re.search(r'"image":\{([^{}]*)\}', blob)
            large = ""
            if img:
                large = get_str(img.group(1), "large") or get_str(img.group(1), "medium")
            pid = get_int(blob, "id")
            items.append({
                "kind": "photo",
                "id": str(pid or m.group(1)),
                "slug": get_str(blob, "slug"),
                "desc": get_str(blob, "description") or get_str(blob, "alt"),
                "alt": get_str(blob, "alt"),
                "w": get_int(blob, "width"),
                "h": get_int(blob, "height"),
                "file": large or f"https://images.pexels.com/photos/{pid}/pexels-photo-{pid}.jpeg",
                "tags": get_tags(blob),
                "duration": 0,
                "fps": 0,
                "cover": "",
            })
        else:
            vm = re.search(r'"video":\{', blob)
            src = prev = ""
            if vm:
                vb = slice_object(blob, blob.index("{", vm.end() - 1))
                src = get_str(vb, "src")
                prev = get_str(vb, "preview_src")
            thumb = ""
            tm = re.search(r'"thumbnail":\{([^{}]*)\}', blob)
            if tm:
                thumb = get_str(tm.group(1), "small")
            items.append({
                "kind": "video",
                "id": m.group(1),
                "slug": get_str(blob, "slug"),
                "desc": get_str(blob, "description") or get_str(blob, "alt"),
                "alt": get_str(blob, "alt"),
                "w": get_int(blob, "width"),
                "h": get_int(blob, "height"),
                "file": prev or src,
                "hd": src,
                "tags": get_tags(blob),
                "duration": get_int(blob, "duration"),
                "fps": get_int(blob, "fps"),
                "cover": thumb,
            })
    return items


def dedupe(items: list[dict]) -> list[dict]:
    seen = set()
    out = []
    for it in items:
        key = it["kind"] + it["id"]
        if key in seen:
            continue
        if not it.get("file"):
            continue
        seen.add(key)
        out.append(it)
    return out


def main() -> None:
    plan = json.loads((HERE / "keywords.json").read_text(encoding="utf-8"))
    result: dict[str, list[dict]] = {"photo": [], "video": []}
    for kind, words in plan.items():
        items: list[dict] = []
        for kw in words:
            tag = f"{kind}_{re.sub(r'[^a-z0-9]+', '_', kw.lower()).strip('_')}"
            url = (f"https://www.pexels.com/search/{urllib.parse.quote(kw)}/"
                   if kind == "photo" else
                   f"https://www.pexels.com/search/videos/{urllib.parse.quote(kw)}/")
            try:
                html = fetch(url, tag)
                got = dedupe(parse_items(html))
                for it in got:
                    it["keyword"] = kw
                items += got
                print(f"{kind:5} {kw:32} {len(got):3} 条  累计 {len(dedupe(items))}")
            except Exception as e:  # noqa: BLE001
                print(f"{kind:5} {kw:32} 失败 {type(e).__name__} {str(e)[:60]}")
            time.sleep(1.0)
        result[kind] = dedupe(items)
        print(f"== {kind} 合计 {len(result[kind])} 条 ==")
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print("已写", OUT, OUT.stat().st_size // 1024, "KB")


if __name__ == "__main__":
    sys.exit(main())
