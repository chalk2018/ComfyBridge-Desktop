"""给所有带参考素材的模板补「参数」标签，便于在模板库里按标签过滤。

用法：.venv/Scripts/python.exe _dev/collect/add_param_tag.py
幂等：已经有标签的模板会跳过，不会重复挂。
"""
from __future__ import annotations

import collections

import httpx

API = "http://127.0.0.1:8190/api/prompt-templates"
TAG_NAME = "参数"
TAG_COLOR = "#e67e22"


def main() -> None:
    with httpx.Client(timeout=180) as c:
        tags = c.get(f"{API}/tags").json()["data"]
        tag = next((t for t in tags if t["name"] == TAG_NAME), None)
        if tag is None:
            r = c.post(f"{API}/tags", json={"name": TAG_NAME, "color": TAG_COLOR})
            r.raise_for_status()
            tag = r.json()["data"]
            print("新建标签", tag)
        else:
            print("已有标签", tag)

        tpls = c.get(API).json()["data"]["templates"]
        todo = [t for t in tpls if t.get("media") and tag["id"] not in (t.get("tags") or [])]
        print("带素材模板", sum(1 for t in tpls if t.get("media")), "待补标签", len(todo), flush=True)

        done = 0
        errors: list[tuple[str, str]] = []
        for i, t in enumerate(todo, 1):
            body = {
                "title": t["title"],
                "content": t["content"],
                "category": t["category"],
                "tags": list(dict.fromkeys((t.get("tags") or []) + [tag["id"]])),
                "media": t.get("media") or [],
            }
            try:
                r = c.put(f"{API}/item/{t['id']}", json=body)
                r.raise_for_status()
                done += 1
            except Exception as exc:  # 单条失败不中断整批
                errors.append((t["title"], str(exc)[:160]))
            if i % 100 == 0 or i == len(todo):
                print(f"  进度 {i}/{len(todo)}，成功 {done}，失败 {len(errors)}", flush=True)

        # 复核
        after = c.get(API).json()["data"]["templates"]
        with_media = [t for t in after if t.get("media")]
        tagged = [t for t in with_media if tag["id"] in (t.get("tags") or [])]
        wrong = [t["title"] for t in after if not t.get("media") and tag["id"] in (t.get("tags") or [])]
        print("复核：带素材", len(with_media), "已挂参数标签", len(tagged), "误挂无参条目", len(wrong))
        print("分类分布", dict(collections.Counter(t["category"] for t in tagged)))
        for title, err in errors[:10]:
            print("失败", title, err)


if __name__ == "__main__":
    main()
