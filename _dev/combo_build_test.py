"""构建层全组合验证：5 档 × 2 宽高比 × 2 参考模式 × 5 参考场景。

只调用 build()（不跑 ComfyUI），断言注入结果、参考槽、无悬空引用、备用链不动、磁盘工作流不变。
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from app.config import config

config.load()

from app import videoext  # noqa: E402
from app.workflows import registry  # noqa: E402

FUNC = "multiRefFast"
ROOT = Path(__file__).resolve().parents[1]
WF_DIR = ROOT / "workflows"
RATIOS = ["16:9", "9:16"]
MODES = ["fl2v", "ref2v"]

SCENES = {
    "none": [],
    "one_image": [{"kind": "image", "comfyName": "a.png"}],
    "one_video": [{"kind": "video", "comfyName": "a.mp4"}],
    "one_audio": [{"kind": "audio", "comfyName": "a.wav"}],
    "mixed": (
        [{"kind": "image", "comfyName": f"img{i}.png"} for i in range(6)]
        + [{"kind": "video", "comfyName": "clip.mp4"}]
        + [{"kind": "audio", "comfyName": f"aud{i}.wav"} for i in range(3)]
    ),
}
EXPECT_COUNTS = {
    "none": (0, 0, 0), "one_image": (1, 0, 0), "one_video": (0, 1, 0),
    "one_audio": (0, 0, 1), "mixed": (6, 1, 3),
}
MODEL_CLASSES = ("UNETLoader", "LoraLoaderModelOnly")
NODE_RE = re.compile(r"^(\d+|ext_\w+)$")
f = videoext.find_func(FUNC)


def model_values(api):
    return {
        nid: v["inputs"].get("unet_name") or v["inputs"].get("lora_name")
        for nid, v in api.items() if v.get("class_type") in MODEL_CLASSES
    }


def check_dangling(api, label):
    bad = []
    for nid, node in api.items():
        for key, val in (node.get("inputs") or {}).items():
            if isinstance(val, list) and len(val) == 2 and isinstance(val[0], str):
                if NODE_RE.fullmatch(val[0]) and val[0] not in api:
                    bad.append((nid, key, val[0]))
    assert not bad, f"{label} 悬空引用: {bad[:4]}"


def digest_all():
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in WF_DIR.glob("视频扩展-多参极速生视频-*.json")}


before_files = digest_all()
count = 0
fails = []
for grade in videoext.grades(FUNC):
    wf = videoext.wf_for(FUNC, grade)
    chain = f["modelNodes"][grade]
    for ratio in RATIOS:
        for mode in MODES:
            for scene, refs in SCENES.items():
                label = f"{grade}/{ratio}/{mode}/{scene}"
                expected_model = f["models"][grade][mode]
                try:
                    api = registry.load(wf, "默认")
                    original_models = model_values(api)
                    built = videoext.build(
                        api, text="一只橘猫在窗边看雨", ratio=ratio, refs=refs,
                        seed=987654, prefix="COMBO", duration=7.0,
                        func=FUNC, grade=grade, mode=mode,
                    )
                    # 1. 模型按「档位 × 模式」正确切换
                    assert built[str(chain["unet"])]["inputs"]["unet_name"] == expected_model["unet"], "UNET"
                    if chain["lora"] and expected_model["lora"]:
                        assert built[str(chain["lora"])]["inputs"]["lora_name"] == expected_model["lora"], "LoRA"
                    after_models = model_values(built)
                    touched = {str(chain["unet"])}
                    if chain["lora"] and expected_model["lora"]:
                        touched.add(str(chain["lora"]))
                    changed = {n for n in original_models if original_models[n] != after_models.get(n)}
                    assert changed <= touched, f"改了备用链: {changed - touched}"
                    if grade.startswith("20采"):
                        assert not changed & {"273", "280"}, "20采不该挂/改 LoRA"
                    # 2. 宽高比注入
                    assert ratio in built["115"]["inputs"]["aspect_ratio"], "宽高比"
                    # 3. 参考槽按实际数量连接，未用的不连
                    h3 = built["230"]["inputs"]
                    got = tuple(sum(k.startswith(pre) for k in h3)
                                for pre in ("ref_images.", "ref_videos.", "ref_audios."))
                    assert got == EXPECT_COUNTS[scene], f"参考槽 {got} != {EXPECT_COUNTS[scene]}"
                    # 4. 其他注入
                    assert built["250"]["inputs"]["prompt"].startswith("一只橘猫"), "提示词"
                    assert built["129"]["inputs"]["noise_seed"] == 987654, "种子"
                    assert built["205"]["inputs"]["select"] == 3, "时长=7秒"
                    assert built["92"]["inputs"]["filename_prefix"] == "COMBO", "前缀"
                    check_dangling(built, label)
                    count += 1
                except Exception as exc:
                    fails.append((label, str(exc)))

assert digest_all() == before_files, "磁盘工作流文件被改动！"
print(f"PASS {count}/100 组合" if count == 100 else f"PARTIAL {count}/100")
for label, msg in fails:
    print("FAIL", label, msg)
print("工作流文件哈希未变（10 个文件）")
raise SystemExit(1 if fails or count != 100 else 0)
