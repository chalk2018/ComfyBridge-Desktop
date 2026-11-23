"""把生成好的模板推进 ComfyBridge：先上传素材，再建模板。

幂等：按标题判重，重跑不会重复插入。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8190"
API = BASE + "/api/prompt-templates"
HERE = Path(__file__).resolve().parent
MEDIA = HERE / "media"

CAT_MAP = {"video": "video", "image": "image", "audio": "audio"}


def upload(client: httpx.Client, path: Path) -> dict:
    with path.open("rb") as f:
        r = client.post(API + "/media", files={"file": (path.name, f)})
    r.raise_for_status()
    d = r.json()
    if not d.get("ok"):
        raise RuntimeError(f"上传失败 {path.name}: {d}")
    return d["data"]


def main() -> None:
    items = json.loads((HERE / "items.json").read_text(encoding="utf-8"))
    tags = {t["name"]: t["id"] for t in
            httpx.get(API, timeout=60).json()["data"]["tags"]}
    existing = {t["title"] for t in
                httpx.get(API, timeout=60).json()["data"]["templates"]}
    todo = [it for it in items if it["title"] not in existing]
    print(f"待导入 {len(todo)} / 共 {len(items)}（已有 {len(items)-len(todo)}）", flush=True)
    ok = fail = 0
    with httpx.Client(timeout=180.0) as client:
        for i, it in enumerate(todo, 1):
            try:
                media = []
                for fn in it["files"]:
                    p = MEDIA / fn
                    if not p.exists():
                        raise RuntimeError("缺文件 " + fn)
                    media.append(upload(client, p))
                payload = {
                    "title": it["title"],
                    "content": it["content"],
                    "category": CAT_MAP[it["category"]],
                    "tags": [tags[n] for n in it.get("tags", []) if n in tags],
                    "media": media,
                }
                r = client.post(API, json=payload)
                if r.status_code != 200 or not r.json().get("ok"):
                    raise RuntimeError(f"建模板失败 {r.status_code} {r.text[:160]}")
                ok += 1
            except Exception as e:  # noqa: BLE001
                fail += 1
                print("FAIL", it["title"], type(e).__name__, str(e)[:140], flush=True)
            if i % 25 == 0:
                print(f"  进度 {i}/{len(todo)} 成功 {ok} 失败 {fail}", flush=True)
                time.sleep(0.2)
    print(f"完成：成功 {ok}，失败 {fail}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
