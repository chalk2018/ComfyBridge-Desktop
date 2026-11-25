"""把 ComfyUI 0.42 的 subgraph（子图）UI 工作流展开成可 POST /prompt 的 API 格式。

为什么需要它：ComfyUI 的 /prompt 只接受 {node_id: {class_type, inputs}} 的扁平图，
而用户在 ComfyUI 界面里保存的工作流把主体逻辑放在 definitions.subgraphs 里，
界面上只是一个 UUID 类型的子图实例节点。直接提交 UI 格式必然被拒。

本脚本只在开发阶段运行一次：按需向本机 ComfyUI 请求 /object_info/<class>（不是全量），
把 widget 值映射成 API 输入；产物是可直接提交的两个 .json 工作流、
一份 .bridge.json 注入点描述、一份 schema.json 参数控件描述。
运行时应用只读这些静态文件，不请求 object_info。

Usage:
    python tools/convert_workflows.py
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
WF_DIR = Path(
    r"D:\ComfyUI-Desktop\Comfy-Desktop\ComfyUI-Installs\ComfyUI (1)\ComfyUI\user\default\workflows"
)
OUT_DIR = ROOT / "workflows"
SOURCE_DIR = OUT_DIR / "source"
COMFY = "http://127.0.0.1:8188"
OI_CACHE = ROOT / "_reference" / "oi_classes.json"

# 源文件名 -> 应用内工作流文件名（保留中文可读名，配置里只登记这两个真实文件）
FILES = {
    "[图][文生图]image_qwen_image_2_1_turbo_t2i.json": "图片生成-文生图-qwen2.1turbo",
    "[图][编辑]image_qwen_image_2_1_turbo_image_edit.json": "图片生成-参考生图-qwen2.1turbo",
}

# 这三类节点按 class_type 判断，不进"可设置参数节点"清单（需求 X28c）
PROMPT_CLASSES = {
    "CLIPTextEncode", "CLIPTextEncodeSDXL", "TextEncodeQwenImage21", "TextGenerate",
    "PrimitiveStringMultiline", "T5TextEncode", "MusicGenTextEncode", "PromptBuilder",
}
UPLOAD_CLASSES = {"LoadImage", "LoadImageMask", "LoadAudio", "LoadVideo", "VHS_LoadVideo"}
OUTPUT_CLASSES = {"SaveImage", "SaveImageAdvanced", "SaveVideo", "SaveAudio", "PreviewImage", "PreviewAny", "ImageCompare"}
# 纯前端节点（object_info 里没有），提交前剔除
FRONTEND_CLASSES = {"MarkdownNote", "Note", "ImageCompare"}

LINK_TYPES = {
    "IMAGE", "LATENT", "MODEL", "CLIP", "VAE", "CONDITIONING", "NOISE", "SIGMAS",
    "GUIDER", "SAMPLER", "MASK", "AUDIO", "VIDEO", "STRING", "INT", "FLOAT", "BOOLEAN",
}


class ObjectInfo:
    """按需读取 /object_info/<class>，缓存到项目目录，避免重复网络请求。"""

    def __init__(self) -> None:
        self.cache: dict[str, Any] = {}
        if OI_CACHE.exists():
            self.cache = json.loads(OI_CACHE.read_text(encoding="utf-8"))

    def get(self, cls: str) -> dict[str, Any] | None:
        """返回单个节点的输入定义；不存在返回 None。"""
        if cls in self.cache:
            return self.cache[cls]
        url = f"{COMFY}/object_info/{urllib.parse.quote(cls, safe='')}"
        try:
            with urllib.request.urlopen(url, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            info = data.get(cls)
        except Exception:
            info = None
        self.cache[cls] = info
        OI_CACHE.parent.mkdir(parents=True, exist_ok=True)
        OI_CACHE.write_text(json.dumps(self.cache, ensure_ascii=False, indent=2), encoding="utf-8")
        return info

    def input_names(self, cls: str) -> set[str]:
        """节点在 API 格式里允许的输入名集合。"""
        info = self.get(cls) or {}
        inp = info.get("input", {}) or {}
        return set((inp.get("required") or {}).keys()) | set((inp.get("optional") or {}).keys())

    def dynamic_fields(self, cls: str) -> set[str]:
        """返回 dynamic combo 类型的输入名（它们的值要组装成 dict）。"""
        info = self.get(cls) or {}
        out: set[str] = set()
        for group in ("required", "optional"):
            for key, spec in ((info.get("input", {}) or {}).get(group) or {}).items():
                if isinstance(spec, list) and spec and spec[0] == "COMFY_DYNAMICCOMBO_V3":
                    out.add(key)
        return out

    def autogrow_fields(self, cls: str) -> set[str]:
        """返回 autogrow 类型的输入名（值是 {子键: [节点, 槽]} 的字典）。"""
        info = self.get(cls) or {}
        out: set[str] = set()
        for group in ("required", "optional"):
            for key, spec in ((info.get("input", {}) or {}).get(group) or {}).items():
                if isinstance(spec, list) and spec and spec[0] == "COMFY_AUTOGROW_V3":
                    out.add(key)
        return out

    def widget_order(self, cls: str) -> list[str]:
        """按 object_info 声明的 input_order 返回 widget 类字段名（UI inputs 为空时兜底）。"""
        info = self.get(cls) or {}
        order: list[str] = []
        for group in ("required", "optional"):
            for name in (info.get("input_order", {}) or {}).get(group, []) or []:
                spec = ((info.get("input", {}) or {}).get(group) or {}).get(name)
                if not isinstance(spec, list) or not spec:
                    continue
                kind = spec[0]
                if kind in ("INT", "FLOAT", "BOOLEAN", "STRING", "COMBO", "COMFY_DYNAMICCOMBO_V3"):
                    order.append(name)
        return order


def normalize_link(link: Any) -> dict[str, Any]:
    """根图 link 是数组，子图 link 是对象，统一成字典。"""
    if isinstance(link, dict):
        return link
    return {
        "id": link[0], "origin_id": link[1], "origin_slot": link[2],
        "target_id": link[3], "target_slot": link[4], "type": link[5],
    }


class Converter:
    """把一个 UI 工作流（含 subgraph）展开成 API prompt。"""

    def __init__(self, data: dict[str, Any], oi: ObjectInfo) -> None:
        self.data = data
        self.oi = oi
        self.subgraphs = {sg["id"]: sg for sg in data.get("definitions", {}).get("subgraphs", [])}
        self.root_nodes: list[dict[str, Any]] = data.get("nodes", [])
        self.root_links: dict[int, dict[str, Any]] = {
            int(l["id"]): l for l in (normalize_link(x) for x in data.get("links", []))
        }
        # 每个子图实例建立一个 scope：root / f"sg{instance_id}"
        self.scopes: dict[str, dict[str, Any]] = {}
        self.api_ids: dict[tuple[str, int], str] = {}
        self.instances: dict[int, dict[str, Any]] = {}
        self.output_sources: dict[tuple[int, int], Any] = {}
        self.api: dict[str, dict[str, Any]] = {}
        self.bridge: dict[str, Any] = {
            "prompt_targets": [], "seed_targets": [], "ref_slots": [],
            "output_nodes": [], "root_load_images": [], "class_types": {},
        }
        self.notes: list[str] = []

        for node in self.root_nodes:
            if node.get("type") in self.subgraphs:
                inst_id = int(node["id"])
                sg = self.subgraphs[node["type"]]
                scope = f"sg{inst_id}"
                self.scopes[scope] = {
                    "nodes": sg.get("nodes", []),
                    "links": {int(l["id"]): l for l in (normalize_link(x) for x in sg.get("links", []))},
                    "instance_id": inst_id,
                }
                self.instances[inst_id] = node
        self.scopes["root"] = {"nodes": self.root_nodes, "links": self.root_links, "instance_id": None}

        # 分配 API 节点 id：root 用原 id，子图内部加实例前缀（避免与 root id 冲突）
        for scope, info in self.scopes.items():
            for node in info["nodes"]:
                old = int(node["id"])
                self.api_ids[(scope, old)] = str(old) if scope == "root" else f"{info['instance_id']}_{old}"
        # id 表就绪后再登记子图输出源（否则拿不到内部节点的 api id）
        for inst_id in self.instances:
            self._collect_output_sources(inst_id, self.subgraphs[self.instances[inst_id]["type"]])

    def _collect_output_sources(self, inst_id: int, sg: dict[str, Any]) -> None:
        """记录子图每个输出槽实际来自哪个内部节点（target_id == -20 的 link）。"""
        scope = f"sg{inst_id}"
        for link in (normalize_link(x) for x in sg.get("links", [])):
            if int(link.get("target_id", 0)) == -20:
                origin = int(link["origin_id"])
                if origin == -10:
                    self.output_sources[(inst_id, int(link["target_slot"]))] = ("instance_input", int(link["origin_slot"]))
                else:
                    self.output_sources[(inst_id, int(link["target_slot"]))] = [self.api_ids[(scope, origin)], int(link["origin_slot"])]

    def _resolve_root_link(self, link_or_id: Any) -> Any:
        """解析 root scope 的一条连线，返回 [api_id, slot] 或字面量。"""
        link = self.root_links[int(link_or_id)] if not isinstance(link_or_id, dict) else link_or_id
        origin = int(link["origin_id"])
        slot = int(link["origin_slot"])
        if origin in self.instances:
            src = self.output_sources.get((origin, slot))
            if isinstance(src, tuple) and src and src[0] == "instance_input":
                return self._instance_input_value(origin, src[1])
            return src
        return [self.api_ids[("root", origin)], slot]

    def _instance_input_value(self, inst_id: int, slot: int) -> Any:
        """子图输入槽的值：外部连线（优先）或实例 widget 值。"""
        inst = self.instances[inst_id]
        inputs = inst.get("inputs", []) or []
        item = inputs[slot] if slot < len(inputs) else {}
        if item.get("link") is not None:
            return self._resolve_root_link(item["link"])
        widgets = inst.get("widgets_values", []) or []
        return widgets[slot] if slot < len(widgets) else None

    def _resolve_scope_link(self, scope: str, link_or_id: Any) -> Any:
        """解析子图内部的一条连线，返回 [api_id, slot] / 字面量 / None。"""
        info = self.scopes[scope]
        link = info["links"][int(link_or_id)] if not isinstance(link_or_id, dict) else link_or_id
        origin = int(link["origin_id"])
        slot = int(link["origin_slot"])
        if origin == -10:
            return self._instance_input_value(info["instance_id"], slot)
        return [self.api_ids[(scope, origin)], slot]

    def _widget_values(self, node: dict[str, Any]) -> dict[str, Any]:
        """把 widgets_values 按 UI 输入顺序映射到参数名。

        ComfyUI V3 的动态输入在 API prompt 里就是点号扁平键（format.bit_depth、
        sampling_mode.temperature、images.image_1），父字段的值只是字符串 key。
        后端 get_finalized_class_inputs 会据此展开 schema 再重组，所以这里绝不能
        把点号字段预先合并成 dict，否则执行时该输入会被直接丢掉。
        """
        cls = node.get("type", "")
        names = [i["name"] for i in (node.get("inputs") or []) if "widget" in i]
        raw = node.get("widgets_values", []) or []
        # 有些节点 UI 里把 widget 收起来了（inputs 为空），此时按 object_info 的声明顺序兜底
        if not names and raw:
            names = self.oi.widget_order(cls)[: len(raw)]
        values = {name: raw[i] for i, name in enumerate(names) if i < len(raw)}
        return {k: v for k, v in values.items() if "control_after_generate" not in k}

    def _build_node(self, scope: str, node: dict[str, Any]) -> None:
        """构造单个 API 节点；子图实例节点本身跳过（已展开）。"""
        cls = node.get("type", "")
        if cls in self.subgraphs:
            return
        if cls in FRONTEND_CLASSES or self.oi.get(cls) is None:
            self.notes.append(f"剔除前端/未知节点 {scope}:{node.get('id')} {cls}")
            return
        api_id = self.api_ids[(scope, int(node["id"]))]
        valid_names = self.oi.input_names(cls)
        widgets = self._widget_values(node)
        inputs: dict[str, Any] = {}
        for index, item in enumerate(node.get("inputs") or []):
            name = item.get("name")
            # upload 是前端的"选择文件"按钮，不是后端输入；混入会在校验里报未知字段
            if not name or name in {"upload", "control_after_generate"}:
                continue
            # 点号字段是 V3 动态输入的合法键，object_info 展开前看不到，直接放行
            if "." not in name and name not in valid_names:
                continue
            link = item.get("link")
            if link is not None:
                # 先登记注入点，再看该槽当前有没有值（未接参考图的槽运行时会动态插入）
                self._record_instance_input(scope, node, index, item, None, api_id)
                value = (self._resolve_scope_link(scope, link) if scope != "root"
                         else self._resolve_root_link(link))
                if value is None:
                    continue
                inputs[name] = value
            elif "widget" in item and name in widgets:
                inputs[name] = widgets[name]
        # UI 里没列出、但 object_info 声明为 widget 的值也要补上（含点号动态字段）
        for name, value in widgets.items():
            if name not in inputs:
                inputs[name] = value
        self.api[api_id] = {"class_type": cls, "inputs": inputs}
        self.bridge["class_types"][api_id] = cls
        if cls.startswith("Save") or cls.startswith("PreviewImage"):
            self.bridge["output_nodes"].append(api_id)
        if cls in UPLOAD_CLASSES and scope == "root":
            self.bridge["root_load_images"].append(api_id)

    def _record_instance_input(
        self, scope: str, node: dict[str, Any], index: int, item: dict[str, Any], value: Any, api_id: str
    ) -> None:
        """子图内部某输入来自子图实例输入槽时，登记运行时注入点。

        例如实例 prompt 槽同时接到 ComfySwitchNode.on_false 和 TextGenerate.prompt，
        运行时要把用户描述写进这两个位置，才能让"描述框"真正生效。
        """
        if scope == "root":
            return
        inst_id = self.scopes[scope]["instance_id"]
        # 找到这条内部 link 的 origin_slot（即实例输入槽）
        link_id = item.get("link")
        link = self.scopes[scope]["links"].get(int(link_id)) if link_id is not None else None
        if not link or int(link.get("origin_id", 0)) != -10:
            return
        slot = int(link["origin_slot"])
        inst_inputs = self.instances[inst_id].get("inputs", []) or []
        if slot >= len(inst_inputs):
            return
        port_name = str(inst_inputs[slot].get("name", ""))
        target = {"node": api_id, "field": item.get("name"), "scope_slot": slot}
        if port_name in ("prompt", "positive_prompt"):
            self.bridge["prompt_targets"].append(target)
        elif port_name in ("seed", "noise_seed"):
            self.bridge["seed_targets"].append(target)
        elif port_name.startswith("image"):
            entry = next((x for x in self.bridge["ref_slots"] if x["name"] == port_name), None)
            if entry is None:
                entry = {"name": port_name, "slot": slot, "targets": [], "linked": item.get("link") is not None}
                self.bridge["ref_slots"].append(entry)
            entry["targets"].append(target)

    def convert(self) -> tuple[dict[str, Any], dict[str, Any]]:
        """执行展开，返回 (api_prompt, bridge)。"""
        for scope, info in self.scopes.items():
            for node in info["nodes"]:
                if int(node.get("mode", 0)) in (2, 4):  # muted / bypass
                    continue
                self._build_node(scope, node)

        # 出图种子节点：RandomNoise / KSampler 的 seed 输入（即使实例没连线也要能注入）
        for api_id, spec in self.api.items():
            cls = spec["class_type"]
            if cls in ("RandomNoise", "KSampler", "KSamplerAdvanced"):
                field = "noise_seed" if cls == "RandomNoise" else "seed"
                target = {"node": api_id, "field": field, "scope_slot": -1}
                if target not in self.bridge["seed_targets"]:
                    self.bridge["seed_targets"].append(target)

        # 剔除引用了不存在节点的连线（被剔除的前端节点大多没有下游，主要是保险）
        for spec in self.api.values():
            for key, value in list(spec["inputs"].items()):
                if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str) and value[0] not in self.api:
                    self.notes.append(f"移除悬空连线 {key} -> {value[0]}")
                    del spec["inputs"][key]
        self.bridge["output_nodes"] = sorted(set(self.bridge["output_nodes"]))
        self.bridge["ref_slots"] = sorted(self.bridge["ref_slots"], key=lambda x: x["slot"])
        self.bridge["notes"] = self.notes
        return self.api, self.bridge

    def validate(self) -> list[str]:
        """静态校验：必填输入是否齐、输入名是否合法。"""
        problems: list[str] = []
        for api_id, spec in self.api.items():
            cls = spec["class_type"]
            info = self.oi.get(cls) or {}
            required = ((info.get("input", {}) or {}).get("required") or {}).keys()
            valid = self.oi.input_names(cls)
            for name in spec["inputs"]:
                if name not in valid and "." not in name:
                    problems.append(f"{api_id} {cls}: 未知输入 {name}")
            for name in required:
                # 动态字段可以由点号子键满足（images.image_1 / format.bit_depth）
                if name not in spec["inputs"] and not any(k.startswith(name + ".") for k in spec["inputs"]):
                    problems.append(f"{api_id} {cls}: 缺必填 {name}")
        return problems


def build_schema(oi: ObjectInfo, api_prompts: list[dict[str, Any]]) -> dict[str, Any]:
    """生成参数控件描述：类型/选项/范围来自工作流自身声明，供档位表渲染。"""
    schema: dict[str, Any] = {}
    for prompt in api_prompts:
        for spec in prompt.values():
            cls = spec["class_type"]
            if cls in schema:
                continue
            info = oi.get(cls) or {}
            fields: dict[str, Any] = {}
            for group in ("required", "optional"):
                for name, decl in ((info.get("input", {}) or {}).get(group) or {}).items():
                    if not isinstance(decl, list) or not decl:
                        continue
                    kind = decl[0]
                    conf = decl[1] if len(decl) > 1 and isinstance(decl[1], dict) else {}
                    if kind in ("INT", "FLOAT", "BOOLEAN", "STRING", "COMBO", "COMFY_DYNAMICCOMBO_V3"):
                        fields[name] = {
                            "type": {"INT": "int", "FLOAT": "float", "BOOLEAN": "bool",
                                    "STRING": "text", "COMBO": "combo",
                                    "COMFY_DYNAMICCOMBO_V3": "dynamic"}[kind],
                            "options": conf.get("options") if kind == "COMBO" else None,
                            "min": conf.get("min"), "max": conf.get("max"),
                            "step": conf.get("step"),
                        }
            schema[cls] = {"role": ("prompt" if cls in PROMPT_CLASSES else
                                     "upload" if cls in UPLOAD_CLASSES else
                                     "output" if cls in OUTPUT_CLASSES else "param"),
                           "fields": fields}
    return schema


def main() -> None:
    """转换两个工作流并写出产物。"""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    SOURCE_DIR.mkdir(parents=True, exist_ok=True)
    oi = ObjectInfo()
    prompts: list[dict[str, Any]] = []
    report: list[str] = []
    for src_name, out_stem in FILES.items():
        src_path = WF_DIR / src_name
        data = json.loads(src_path.read_text(encoding="utf-8"))
        (SOURCE_DIR / src_name).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        conv = Converter(data, oi)
        api, bridge = conv.convert()
        (OUT_DIR / f"{out_stem}.json").write_text(json.dumps(api, ensure_ascii=False, indent=2), encoding="utf-8")
        (OUT_DIR / f"{out_stem}.bridge.json").write_text(json.dumps(bridge, ensure_ascii=False, indent=2), encoding="utf-8")
        prompts.append(api)
        problems = conv.validate()
        report.append(f"===== {src_name} -> {out_stem} =====")
        report.append(f"API nodes: {len(api)}; outputs: {bridge['output_nodes']}")
        report.append(f"prompt_targets: {bridge['prompt_targets']}")
        report.append(f"seed_targets: {bridge['seed_targets']}")
        report.append(f"ref_slots: {[(x['name'], len(x['targets'])) for x in bridge['ref_slots']]}")
        report.append(f"root_load_images: {bridge['root_load_images']}")
        report.append("校验问题: " + ("无" if not problems else "\n  " + "\n  ".join(problems)))
        report.append("转换备注:\n  " + "\n  ".join(bridge["notes"][:40]))
        report.append("")
    schema = build_schema(oi, prompts)
    (OUT_DIR / "schema.json").write_text(json.dumps(schema, ensure_ascii=False, indent=2), encoding="utf-8")
    (ROOT / "_reference" / "convert_report.txt").write_text("\n".join(report), encoding="utf-8")
    print("\n".join(report))


if __name__ == "__main__":
    main()
