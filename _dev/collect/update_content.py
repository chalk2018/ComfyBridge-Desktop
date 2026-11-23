"""把 items.json 里的最新提示词正文同步到已有模板（按标题匹配，只改 content，不动素材与标签）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

API = "http://127.0.0.1:8190/api/prompt-templates"
HERE = Path(__file__).resolve().parent


def main() -> None:
    items = json.loads((HERE / "items.json").read_text(encoding="utf-8"))
    d = httpx.get(API, timeout=180).json()["data"]
    by_title = {t["title"]: t for t in d["templates"]}
    ok = fail = miss = same = 0
    with httpx.Client(timeout=120.0) as client:
        for i, it in enumerate(items, 1):
            t = by_title.get(it["title"])
            if not t:
                miss += 1
                print("MISS", it["title"], flush=True)
                continue
            if t["content"] == it["content"]:
                same += 1
                continue
            body = {"title": t["title"], "content": it["content"],
                    "category": t["category"], "tags": t.get("tags", []),
                    "media": t["media"]}
            r = client.put(f"{API}/item/{t['id']}", json=body)
            if r.status_code == 200 and r.json().get("ok"):
                ok += 1
            else:
                fail += 1
                print("FAIL", it["title"], r.status_code, r.text[:120], flush=True)
            if i % 100 == 0:
                print(f"  进度 {i}/{len(items)} 更新 {ok}", flush=True)
    print(f"完成：更新 {ok}，未变 {same}，缺失 {miss}，失败 {fail}", flush=True)
    sys.exit(0 if not (fail or miss) else 1)


if __name__ == "__main__":
    main()
