"""验证参考模式注入：5 档 × 2 模式，只改生效链的模型字段。"""
from __future__ import annotations

import copy
import re
import sys

from app.config import config

config.load()

from app import videoext  # noqa: E402
from app.workflows import registry  # noqa: E402

FUNC = "multiRefFast"
f = videoext.find_func(FUNC)
assert f, "子功能缺失"
assert [m["id"] for m in videoext.modes(FUNC)] == ["fl2v", "ref2v"]

MODEL_CLASSES = ("UNETLoader", "LoraLoaderModelOnly")
REF_RE = re.compile(r"^(\d+|ext_\w+)$")


def model_values(api):
    return {
        nid: (v["inputs"].get("unet_name") or v["inputs"].get("lora_name"))
        for nid, v in api.items()
        if v.get("class_type") in MODEL_CLASSES
    }


def check_links(api):
    """build 删除未用参考节点后，不能有指向不存在节点的连线。"""
    dangling = []
    for nid, node in api.items():
        for key, val in (node.get("inputs") or {}).items():
            if not (isinstance(val, list) and len(val) == 2):
                continue
            target, slot = val
            if isinstance(target, str) and REF_RE.fullmatch(target) and target not in api:
                dangling.append((nid, key, target, slot))
    assert not dangling, f"悬空引用: {dangling[:5]}"


total = 0
for grade in videoext.grades(FUNC):
    wf = videoext.wf_for(FUNC, grade)
    chain = f["modelNodes"][grade]
    for mode in ("fl2v", "ref2v"):
        api = registry.load(wf, "默认")
        before = model_values(api)
        built = videoext.build(
            api, text="一只猫", ratio="16:9", refs=[], seed=1234, prefix="PRE",
            duration=5.0, func=FUNC, grade=grade, mode=mode,
        )
        after = model_values(built)
        expected = f["models"][grade][mode]
        node_unet, node_lora = str(chain["unet"]), chain["lora"]
        assert built[node_unet]["inputs"]["unet_name"] == expected["unet"], (grade, mode, "unet")
        if node_lora and expected["lora"]:
            assert built[str(node_lora)]["inputs"]["lora_name"] == expected["lora"], (grade, mode, "lora")
        # 除生效链之外的模型节点一律保持原样（备用链不动；20 采 LoRA 不动）
        touched = {node_unet, str(node_lora) if node_lora and expected["lora"] else ""}
        changed = {nid for nid in before if before[nid] != after.get(nid)}
        assert changed <= touched, (grade, mode, "改动了不该改的节点", changed, touched)
        if grade.startswith("20采"):
            assert not changed & {"273", "280"}, (grade, mode, "20 采不该改 LoRA")
        # 其他注入仍然生效
        assert built["250"]["inputs"]["prompt"].startswith("一只猫"), (grade, mode, "prompt")
        assert built["129"]["inputs"]["noise_seed"] == 1234, (grade, mode, "seed")
        assert "16:9" in built["115"]["inputs"]["aspect_ratio"], (grade, mode, "ratio")
        assert built["205"]["inputs"]["select"] == 1, (grade, mode, "duration")
        assert built["92"]["inputs"]["filename_prefix"] == "PRE", (grade, mode, "prefix")
        check_links(built)
        print(f"OK {grade:8s} {mode:5s} unet={node_unet} lora={node_lora or '-'}")
        total += 1

# 参考素材动态槽仍正常：2 图 + 1 视频 + 1 音频，未用槽不连线
api = registry.load(videoext.wf_for(FUNC, "8采480P"), "默认")
refs = [
    {"kind": "image", "comfyName": "a.png"},
    {"kind": "image", "comfyName": "b.png"},
    {"kind": "video", "comfyName": "c.mp4"},
    {"kind": "audio", "comfyName": "d.wav"},
]
built = videoext.build(api, text="x", ratio="9:16", refs=refs, seed=1, prefix="P",
                       duration=7.0, func=FUNC, grade="8采480P", mode="ref2v")
h3 = built["230"]["inputs"]
assert sum(k.startswith("ref_images.") for k in h3) == 2
assert sum(k.startswith("ref_videos.") for k in h3) == 1
assert sum(k.startswith("ref_audios.") for k in h3) == 1
check_links(built)
print("OK refs 2图/1视频/1音频 + ref2v 模型注入")
print(f"ALL PASS: {total} combos + refs")
