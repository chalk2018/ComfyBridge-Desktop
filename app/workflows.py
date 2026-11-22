"""工作流层：读 API 格式工作流、填格子、造档位拷贝、给页面生成节点清单。

工作流文件只在应用自己的 workflows/ 目录里读；原始母版只读，档位是
workflows/ComfyBridge/ 下的独立拷贝（需求 X35 / 02 章 §1.4）。
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import threading
from pathlib import Path
from typing import Any

from .config import CONFIG_PATH, ROOT, ConfigError, config

PROMPT_CLASSES = {
    "CLIPTextEncode", "CLIPTextEncodeSDXL", "TextEncodeQwenImage21", "TextGenerate",
    "PrimitiveStringMultiline", "T5TextEncode", "MusicGenTextEncode", "PromptBuilder",
}
UPLOAD_CLASSES = {"LoadImage", "LoadImageMask", "LoadAudio", "LoadVideo", "VHS_LoadVideo"}
OUTPUT_CLASSES = {"SaveImage", "SaveImageAdvanced", "SaveVideo", "SaveAudio", "PreviewImage", "PreviewAny", "ImageCompare"}

_MISSING = object()
ASPECT_MAP: dict[str, tuple[int, int]] = {
    "1:1 (Square)": (1, 1), "2:3 (Portrait Photo)": (2, 3), "3:2 (Photo)": (3, 2),
    "3:4 (Portrait Standard)": (3, 4), "4:3 (Standard)": (4, 3),
    "9:16 (Portrait Widescreen)": (9, 16), "16:9 (Widescreen)": (16, 9),
    "21:9 (Ultrawide)": (21, 9),
}


def is_link(value: Any) -> bool:
    """ComfyUI API 里的连线值是 [节点 id, 输出槽]。"""
    return isinstance(value, list) and len(value) == 2 and isinstance(value[0], str)


class WorkflowError(Exception):
    """工作流读取/注入失败。"""


class WorkflowRegistry:
    """持有工作流母版、bridge 注入点、参数控件 schema。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._cache: dict[str, dict[str, Any]] = {}
        self._bridges: dict[str, dict[str, Any]] = {}
        # 档位拷贝只在「档位定义 / 母版 / 配置文件」真的变了时重建，
        # 既保证改过的参数一定生效，又不会让每秒的状态轮询反复写文件。
        self._copy_stamp: dict[str, str] = {}
        self._effective_cache: dict[str, dict[str, Any]] = {}
        self._config_mtime: int | None = None
        schema_path = config.resolve(config.service.wfDir) / "schema.json"
        self.schema: dict[str, Any] = json.loads(schema_path.read_text(encoding="utf-8")) if schema_path.exists() else {}

    def _read(self, path: Path) -> dict[str, Any]:
        key = str(path)
        with self._lock:
            if key not in self._cache:
                if not path.exists():
                    raise WorkflowError(f"找不到工作流文件：{path.name}")
                self._cache[key] = json.loads(path.read_text(encoding="utf-8"))
            return copy.deepcopy(self._cache[key])

    def invalidate(self, path: Path | None = None) -> None:
        """配置/档位改动后清缓存。"""
        with self._lock:
            if path is None:
                self._cache.clear()
            else:
                self._cache.pop(str(path), None)

    def bridge(self, wf_name: str) -> dict[str, Any]:
        """读取该工作流的注入点描述（描述框/种子/参考图/出片节点）。"""
        with self._lock:
            if wf_name in self._bridges:
                return self._bridges[wf_name]
        wf = config.workflow(wf_name)
        if not wf:
            raise WorkflowError(f"配置里没有工作流「{wf_name}」")
        path = config.resolve(wf["bridge"])
        data = json.loads(path.read_text(encoding="utf-8"))
        with self._lock:
            self._bridges[wf_name] = data
        return data

    def _sync_external_config(self) -> None:
        """手改 config.yaml 后也能生效：发现文件变了就重读，不要求用户点按钮。"""
        try:
            mtime = CONFIG_PATH.stat().st_mtime_ns
        except OSError:
            return
        if self._config_mtime is None:
            self._config_mtime = mtime
            return
        if mtime == self._config_mtime:
            return
        self._config_mtime = mtime
        try:
            config.load()
            self.invalidate()
            self._bridges.clear()
            self._copy_stamp.clear()
            self._effective_cache.clear()
        except ConfigError:
            # 配置坏掉时继续用内存里上一份可用配置，页面顶部红条另行提示
            pass

    def grade_params(self, wf_name: str, grade: str | None) -> dict[str, Any]:
        """档位名 → 该档的参数覆盖（默认档返回空，表示母版原样）。"""
        wf = config.workflow(wf_name)
        if not wf:
            raise WorkflowError(f"配置里没有工作流「{wf_name}」")
        if not grade or grade == "默认":
            return {}
        grade_def = next((g for g in wf.get("grades", []) if g.get("name") == grade), None)
        if not grade_def:
            raise WorkflowError(f"「{wf_name}」没有档位「{grade}」")
        return dict(grade_def.get("params") or {})

    def load(
        self,
        wf_name: str,
        grade: str | None = None,
        params: dict[str, Any] | None = None,
        tag: str | None = None,
    ) -> dict[str, Any]:
        """返回一份可直接提交的工作流：默认档用母版，其余档用最新参数重建的拷贝。

        以前只在启动时补「不存在的」拷贝，导致页面改完参数 / 手改配置后
        生成时仍读旧文件——这正是「改成 16:9 却还是 1:1」的一类根因。

        Args:
            wf_name: 配置里的工作流名。
            grade: 配置里的档位名（图片生成用；给了 params 就不再查它）。
            params: 直接给一份参数覆盖（角色设定图的组合档位走这条路，
                因为它的档位不在 config 的 grades 里，而是按组合算出来的）。
            tag: 参数覆盖的缓存键与拷贝文件名后缀，默认用档位名。
        """
        self._sync_external_config()
        wf = config.workflow(wf_name)
        if not wf:
            raise WorkflowError(f"配置里没有工作流「{wf_name}」")
        if params is None:
            params = self.grade_params(wf_name, grade)
            tag = tag or grade
        if params:
            path = self._ensure_fresh_copy(wf_name, {"name": tag or grade or "默认", "params": params})
            return self._read(path)
        return self._read(config.resolve(wf["file"]))

    def grade_copy_path(self, wf_name: str, grade: str) -> Path:
        """档位拷贝统一放在独立子目录，文件名带档位名（X35b）。"""
        safe = re.sub(r"[\\/:*?\"<>|]", "_", grade)
        return config.resolve(config.service.copyDir) / f"{re.sub(r'[\\/:*?\"<>|]', '_', wf_name)}__{safe}.json"

    @staticmethod
    def _normalize_params(api: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
        """解决「上游宽高比」与「下游固定宽高」互相覆盖的问题。

        EmptyLatentImage 的 width/height 原本是连到 ResolutionSelector 的线。
        如果一档里同时把 13.aspect_ratio 改成 16:9、又把 459_456.width 写成
        768，后者会把连线掐断，宽高比就彻底失效（出图仍是方的）。
        规则：某字段原本是连线、且连线指向的节点也在本档被调过参数时，
        保留连线，让上游参数说了算。
        """
        tuned = set(params.keys())
        cleaned: dict[str, Any] = {}
        for node_id, fields in params.items():
            if node_id not in api or not isinstance(fields, dict):
                continue
            keep: dict[str, Any] = {}
            for field, value in fields.items():
                original = api[node_id]["inputs"].get(field)
                if is_link(original) and str(original[0]) in tuned:
                    continue
                keep[field] = value
            if keep:
                cleaned[node_id] = keep
        return cleaned

    def _copy_signature(self, wf_name: str, grade_def: dict[str, Any]) -> str:
        """档位定义 + 母版 + 配置文件三者任一变化都要重建拷贝。"""
        wf = config.workflow(wf_name) or {}
        master = config.resolve(wf.get("file", ""))
        try:
            master_mtime = master.stat().st_mtime_ns
        except OSError:
            master_mtime = 0
        payload = json.dumps(
            [grade_def, master_mtime, getattr(self, "_config_mtime", 0)],
            sort_keys=True, ensure_ascii=False, default=str,
        )
        return hashlib.md5(payload.encode("utf-8")).hexdigest()

    def _ensure_fresh_copy(self, wf_name: str, grade_def: dict[str, Any]) -> Path:
        """保证拷贝文件与当前档位定义完全一致；一致时直接复用。"""
        path = self.grade_copy_path(wf_name, grade_def["name"])
        key = f"{wf_name}|{grade_def['name']}"
        stamp = self._copy_signature(wf_name, grade_def)
        if self._copy_stamp.get(key) == stamp and path.exists():
            return path
        self.rebuild_copy(wf_name, grade_def)
        self._copy_stamp[key] = stamp
        self._effective_cache.pop(key, None)
        return path

    def rebuild_all_copies(self) -> None:
        """重读配置后强制重建所有档位拷贝（清掉旧参数残留）。"""
        self._copy_stamp.clear()
        self._effective_cache.clear()
        self.ensure_copies()

    def rebuild_copy(self, wf_name: str, grade_def: dict[str, Any]) -> Path:
        """一档 = 一份完整工作流拷贝：复制母版，把这档的参数写进对应节点。"""
        wf = config.workflow(wf_name)
        api = self._read(config.resolve(wf["file"]))
        params = self._normalize_params(api, grade_def.get("params", {}) or {})
        for node_id, fields in params.items():
            for field, value in fields.items():
                api[node_id]["inputs"][field] = value
        path = self.grade_copy_path(wf_name, grade_def["name"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(api, ensure_ascii=False, indent=2), encoding="utf-8")
        self.invalidate(path)
        self._effective_cache.pop(f"{wf_name}|{grade_def['name']}", None)
        return path

    def ensure_copies(self) -> None:
        """补齐/刷新所有非默认档的拷贝文件；原始母版一个字节都不改。"""
        for wf_name in (config.raw.get("workflows") or {}):
            wf = config.workflow(wf_name)
            if not wf:
                continue
            for grade in wf.get("grades", []):
                if grade.get("locked"):
                    continue
                self._ensure_fresh_copy(wf_name, grade)

    # ---------------------------------------------------------- 参数生效自检
    def grade_effective(
        self,
        wf_name: str,
        grade: str,
        params: dict[str, Any] | None = None,
        tag: str | None = None,
    ) -> dict[str, Any]:
        """核对这一档的每个配置值是否真的落到即将提交的 prompt 上。

        返回逐项 want/got 对照、是否全部生效、以及该档的出图尺寸说明。
        页面用它显示「参数已生效」，测试用它逐参数断言。

        Args:
            params: 显式参数覆盖（角色设定图组合档位用）；不给就按档位名查 config。
            tag: 缓存键，需与 `load()` 用的一致，否则会读到别的档的旧结论。
        """
        self._sync_external_config()
        wf = config.workflow(wf_name) or {}
        if params is None:
            params = self.grade_params(wf_name, grade)
            tag = tag or grade
        tag = tag or grade
        grade_def = {"name": tag, "params": params}
        key = f"{wf_name}|{tag}"
        stamp = self._copy_signature(wf_name, grade_def)
        cached = self._effective_cache.get(key)
        if cached and cached.get("stamp") == stamp:
            return copy.deepcopy(cached["data"])
        master = self._read(config.resolve(wf["file"]))
        params = self._normalize_params(master, params)
        api = self.load(wf_name, grade, params=params, tag=tag)
        bridge = self.bridge(wf_name)
        size_explicit = self._apply_size_gate(api, wf_name, params)
        rows: list[dict[str, Any]] = []
        applied: dict[str, Any] = {}
        for node_id, fields in params.items():
            cls = (api.get(node_id) or {}).get("class_type", "")
            inputs = (api.get(node_id) or {}).get("inputs", {})
            applied[node_id] = {}
            for field, want in fields.items():
                got = inputs.get(field, _MISSING)
                meta = self.field_meta(cls, field)
                reason = ""
                ok = got == want
                options = meta.get("options") or []
                if ok and options and got not in options:
                    ok, reason = False, "这个值不在工作流允许的选项里"
                if got is _MISSING:
                    ok, reason = False, "节点里没有这个参数"
                applied[node_id][field] = None if got is _MISSING else got
                rows.append({"node": node_id, "cls": cls, "field": field,
                             "want": want, "got": None if got is _MISSING else got,
                             "ok": ok, "reason": reason})
        size = resolve_size(api)
        # 参考生图的「原样」档实际出图尺寸跟随参考图，别显示成 502 的 1024 误导人
        if bridge.get("size_gate") and not size_explicit:
            size = {**size, "label": "原样（跟随参考图尺寸）", "source": "reference", "approx": None}
        data = {
            "wf": wf_name, "grade": grade, "stamp": stamp,
            "allOk": all(row["ok"] for row in rows), "rows": rows,
            "applied": applied, "size": size,
        }
        self._effective_cache[key] = {"stamp": stamp, "data": copy.deepcopy(data)}
        return data

    def _apply_size_gate(
        self, api: dict[str, Any], wf_name: str, params: dict[str, Any]
    ) -> bool:
        """参考生图的「尺寸门」：显式指定宽高比时，切到 EmptyLatentImage。

        缘由：参考工作流里 `459_468 ComfySwitchNode` 默认 switch=False，采样用
        TextEncodeQwenImage21 编码出的「参考图 latent」——它的宽高比等于参考图，
        所以档位里配了 16:9 也照样出竖屏（用户报的正是这个）。只有显式设置了
        aspect_ratio/width/height 时，才把开关切到 on_true（EmptyLatentImage，
        由 ResolutionSelector 按档位算出 16:9 等尺寸）。

        Args:
            params: 这一档归一化后的参数覆盖（按节点存）；不给就等于「原样出图」。
        """
        gate = self.bridge(wf_name).get("size_gate")
        if not gate:
            return False
        explicit = any(
            any(k in ("aspect_ratio", "width", "height") for k in (fields or {}))
            for fields in params.values()
        )
        if not explicit:
            return False
        node = api.get(gate["node"])
        if node and gate.get("field") in node.get("inputs", {}):
            node["inputs"][gate["field"]] = gate.get("value", True)
        return True

    def _apply_prompt_enhance(self, api: dict[str, Any], bridge: dict[str, Any], text: str = "") -> bool:
        """按 bridge 声明打开工作流自带的「多模态提示词改写器」，并排除会漂移的场景。

        这个改写器（TextGenerate）是多模态节点：它同时吃用户提示词和全部参考图，
        由 qwen3.5 看图后把含糊指令改写成精确指令。实测结论：
          · 跨图编辑 / 风格迁移 / 局部提取（"沿用<image1>风格""只留某物"等）→ 改写器
            看着参考图能正确理清每张图的角色，输出精准的迁移指令，效果远好于原话直进
            编码器（原话直进时模型无法自主把图2人物的配色/风格换成图1的）。
          · 设定图 / 多视图 / 版式类（"角色设定图""三视图""画布16:9"等）→ 该模型有根深
            蒂固的"设定图=2D矢量/卡通"先验，会把真人参考图改写成扁平矢量风，连加
            system prompt 约束都压不过。这类必须关掉改写器，让原话直进编码器。

        因此采用黑名单语义（通用、不依赖具体提示词）：
          · bridge 声明 unless_any 时，提示词命中任一排除词（设定图/版式类）→ 不开；
          · 未命中 → 开。when_any（白名单）保留兼容：若声明，则还须命中任一词才开。
        开关、字段、排除词都写在 bridge.json（和 size_gate 同一套路子），代码不认固定
        节点 id、不改母版，只在运行时档位拷贝上生效（X22 / X35）。
        """
        enh = bridge.get("prompt_enhance")
        if not isinstance(enh, dict):
            return False
        low = (text or "").lower()
        exclude = enh.get("unless_any")
        if isinstance(exclude, list) and exclude:
            if any(str(k).lower() in low for k in exclude):
                return False
        triggers = enh.get("when_any")
        if isinstance(triggers, list) and triggers:
            if not any(str(k).lower() in low for k in triggers):
                return False
        node_id, field = enh.get("node"), enh.get("field")
        node = api.get(node_id) if node_id else None
        if not node or field not in node.get("inputs", {}):
            return False
        node["inputs"][field] = enh.get("value", True)
        # 可选：给改写器的 system prompt 追加一段补充规则（声明式，不改母版）。
        # 用途：修正模型对「跨图配色/风格沿用」的 identity 判定——配色被明确要求
        # 迁移时属于编辑目标，不应作为 identity 保留，否则改写器会自相矛盾导致
        # 出图仍保留原配色。只在本次开启改写器时注入，运行时拷贝生效。
        spa = enh.get("system_prompt_append")
        if isinstance(spa, dict):
            sp_node = api.get(spa.get("node"))
            sp_field = spa.get("field", "value")
            text_add = spa.get("text", "")
            if sp_node and sp_field in sp_node.get("inputs", {}) and text_add:
                cur = str(sp_node["inputs"][sp_field] or "")
                if text_add not in cur:
                    before = spa.get("before")
                    if before and before in cur:
                        # 必须插在结尾标记之前：qwen3.5 把"规则段 + 用户输入标记"当作
                        # 结构，规则追加到标记之后会破坏结构、触发思维链重复循环（输出
                        # 同一段几十遍 + </think> 标签）。插在标记之前输出才稳定。
                        sp_node["inputs"][sp_field] = cur.replace(
                            before, text_add.strip() + "\n\n" + before, 1)
                    else:
                        sp_node["inputs"][sp_field] = cur + "\n" + text_add
        return True

    def build_prompt(
        self,
        wf_name: str,
        grade: str,
        text: str,
        refs: list[dict[str, Any]],
        seed: int | None,
        prefix: str,
        params: dict[str, Any] | None = None,
        tag: str | None = None,
        placeholder: str | None = None,
    ) -> dict[str, Any]:
        """把用户描述、参考图、种子、文件名前缀填进工作流，得到可提交的 prompt。

        Args:
            params: 参数覆盖（角色设定图的组合档位用）；不给就按档位名查 config。
            tag: 与 `load()` 一致的缓存键，保证「同一组合同一档」每次都读到同一份拷贝。
            placeholder: 0 张参考图时塞给 LoadImage 的占位图文件名。参考生图这类
                工作流必须有一张图输入，「一格参考图都没传」时节点会带着工作流里
                那个早已不存在的默认文件名出发，ComfyUI 直接拒收整条任务。
        """
        if params is None:
            params = self.grade_params(wf_name, grade)
            tag = tag or grade
        api = self.load(wf_name, grade, params=params, tag=tag)
        bridge = self.bridge(wf_name)
        self._apply_size_gate(api, wf_name, params)
        self._apply_prompt_enhance(api, bridge, text)
        for target in bridge.get("prompt_targets", []):
            node = api.get(target["node"])
            if node:
                node["inputs"][target["field"]] = text
        if seed is not None:
            for target in bridge.get("seed_targets", []):
                # sampling_mode.seed 是提示词改写器的种子，换它不影响画面构图；
                # 只固定出图用的 noise_seed / seed
                if target["field"] not in ("noise_seed", "seed"):
                    continue
                node = api.get(target["node"])
                if node:
                    node["inputs"][target["field"]] = seed
        for node_id in bridge.get("output_nodes", []):
            node = api.get(node_id)
            if node and "filename_prefix" in node["inputs"]:
                node["inputs"]["filename_prefix"] = prefix
        # 参考图：第 1 张改写工作流里已有的 LoadImage；额外的图动态插入节点
        root_loads = bridge.get("root_load_images", [])
        if refs:
            if root_loads and root_loads[0] in api:
                api[root_loads[0]]["inputs"]["image"] = refs[0]["comfyName"]
            slots = [s for s in bridge.get("ref_slots", []) if not s["name"].endswith("image_1")]
            for index, (slot, ref) in enumerate(zip(slots, refs[1:]), start=2):
                new_id = f"ref_image_{index}"
                api[new_id] = {"class_type": "LoadImage", "inputs": {"image": ref["comfyName"]}}
                for target in slot["targets"]:
                    if target["node"] in api:
                        api[target["node"]]["inputs"][target["field"]] = [new_id, 0]
        elif placeholder and root_loads and root_loads[0] in api:
            # 0 张参考图：把 LoadImage 指到一张纯白占位图，否则它会带着工作流里
            # 那个已经不存在的默认文件名出发，ComfyUI 拒收整条任务。
            api[root_loads[0]]["inputs"]["image"] = placeholder
        return api

    def node_list(self, wf_name: str) -> list[dict[str, Any]]:
        """档位表用的节点清单：按 class_type 分角色，参数节点才进可勾清单。"""
        api = self.load(wf_name)
        out: list[dict[str, Any]] = []
        # 需求4：视频生成工作流的参数全部由档位表（比例/分辨率/步数）统一管理，
        # 管理台档位表里「参数节点」必须是 0 个——所有节点都归到「不用设的节点」。
        video_wf = wf_name.startswith("视频生成-")
        for node_id, spec in api.items():
            cls = spec["class_type"]
            role = ("prompt" if cls in PROMPT_CLASSES else
                    "upload" if cls in UPLOAD_CLASSES else
                    "output" if cls in OUTPUT_CLASSES or cls.startswith("Save") else "param")
            if video_wf and role == "param":
                role = "fixed"   # 不进可勾参数清单，落到「不用设的节点」
            widgets = [k for k, v in spec["inputs"].items() if not is_link(v)]
            links = [k for k, v in spec["inputs"].items() if is_link(v)]
            out.append({"id": node_id, "cls": cls, "w": widgets, "c": links,
                        "out": role == "output", "role": role})
        return out

    def field_meta(self, cls: str, field: str) -> dict[str, Any]:
        """控件形态来自工作流自身声明的类型，表里没有的新字段按文字处理（X32）。"""
        base = field.split(".")[0]
        info = (self.schema.get(cls) or {}).get("fields", {}).get(base)
        if not info:
            return {"type": "text", "options": None, "min": None, "max": None, "step": None}
        return info

    def state_wf_lib(self) -> dict[str, dict[str, Any]]:
        """生成前端 WF_LIB 同构数据（节点/参数/连线来自真实工作流）。"""
        lib: dict[str, dict[str, Any]] = {}
        for wf_name, wf in (config.raw.get("workflows") or {}).items():
            lib[wf_name] = {"path": wf["file"], "list": self.node_list(wf_name)}
        return lib

    def state_capabilities(self) -> list[dict[str, Any]]:
        """生成前端 S.capabilities 同构数据（含 original 与档位扁平参数）。"""
        caps: list[dict[str, Any]] = []
        for cap in config.raw.get("capabilities", []):
            item = copy.deepcopy({k: v for k, v in cap.items() if k != "workflows"})
            wfs: list[dict[str, Any]] = []
            for name in cap.get("workflows", []) or []:
                wf = config.workflow(name)
                if not wf:
                    continue
                api = self.load(name)
                grades: list[dict[str, Any]] = []
                for grade in wf.get("grades", []):
                    g = {"name": grade["name"], "note": grade.get("note", ""),
                         "locked": bool(grade.get("locked"))}
                    if not g["locked"]:
                        g["params"] = flatten_params(grade.get("params", {}))
                        g["copyFile"] = str(self.grade_copy_path(name, grade["name"]))
                    # 每档都带一份「参数生效」说明，页面上能直接看到改了什么、出图多大
                    eff = self.grade_effective(name, grade["name"])
                    g["effective"] = {"size": eff["size"]["label"], "allOk": eff["allOk"],
                                      "changed": len(eff["rows"])}
                    grades.append(g)
                bridge = self.bridge(name)
                wfs.append({
                    "name": name, "path": wf["file"], "timeout": 180,
                    "prompt": {"node": None, "field": None},
                    "fields": wf.get("fields", []),
                    "original": flatten_widgets(api),
                    "grades": grades,
                    "output": {"node": (bridge.get("output_nodes") or [None])[0],
                               "kind": "图片", "multi": False},
                    "status": wf.get("status", "ok"),
                })
            item["workflows"] = wfs
            if cap.get("id") == "sheet.character":
                # 设定图的档位挂在「组合」上（组合才是母版），页面按组合渲染档位表。
                # 放在能力里而不是顶层：管理台的档位表本来就是「按能力」进去的。
                from . import combos as sheet_combos
                item["combos"] = sheet_combos.view()
            elif cap.get("id") in ("sheet.scene", "sheet.prop"):
                from . import extsheets as ext_sheets
                _kind = ext_sheets.SCENE if cap.get("id") == "sheet.scene" else ext_sheets.PROP
                item["combos"] = ext_sheets.view(_kind)
                item["extKind"] = _kind
            elif cap.get("id") == "board.story":
                from . import boardlib as story_boards
                item["combos"] = story_boards.view()
            caps.append(item)
        return caps


def flatten_widgets(api: dict[str, Any]) -> dict[str, Any]:
    """把工作流里的字面量参数摊平成 {字段名: 值}，供原型档位摘要显示。"""
    out: dict[str, Any] = {}
    for spec in api.values():
        for key, value in spec["inputs"].items():
            if is_link(value) or isinstance(value, (dict, list)):
                continue
            out.setdefault(key, value)
    return out


def flatten_params(params: dict[str, Any]) -> dict[str, Any]:
    """把按节点存的档位参数摊平成前端原型用的 {字段名: 值}。"""
    out: dict[str, Any] = {}
    for key, value in params.items():
        if isinstance(value, dict):
            for field, val in value.items():
                out.setdefault(field, val)
        else:
            out.setdefault(key, value)
    return out


def resolve_size(api: dict[str, Any]) -> dict[str, Any]:
    """从最终 prompt 算出这一档的出图尺寸说明。

    固定宽高能给精确值；连到 ResolutionSelector 时按它的公式估算并标明「约」，
    真正准确的尺寸以生成后回读的图片 metadata 为准。
    """
    latent = next((spec for spec in api.values()
                   if spec.get("class_type") in ("EmptyLatentImage", "EmptySD3LatentImage")), None)
    if not latent:
        return {"label": "由工作流决定", "width": None, "height": None, "source": "unknown"}
    inputs = latent.get("inputs", {})
    width, height = inputs.get("width"), inputs.get("height")
    if isinstance(width, (int, float)) and isinstance(height, (int, float)):
        return {"label": f"{int(width)} × {int(height)}", "width": int(width),
                "height": int(height), "source": "fixed"}
    if is_link(width) and is_link(height):
        src = api.get(str(width[0])) or {}
        if src.get("class_type") == "ResolutionSelector":
            src_in = src.get("inputs", {})
            ratio = str(src_in.get("aspect_ratio", ""))
            megapixels = float(src_in.get("megapixels", 1) or 1)
            multiple = int(src_in.get("multiple", 8) or 8) or 8
            wr, hr = ASPECT_MAP.get(ratio, (1, 1))
            scale = math.sqrt(megapixels * 1024 * 1024 / (wr * hr))
            approx_w = round(wr * scale / multiple) * multiple
            approx_h = round(hr * scale / multiple) * multiple
            return {"label": f"{ratio} · 约 {approx_w} × {approx_h}",
                    "width": None, "height": None, "source": "resolution_selector",
                    "approx": [approx_w, approx_h], "megapixels": megapixels}
    return {"label": "由工作流决定", "width": None, "height": None, "source": "linked"}


def pick_workflow(cap: dict[str, Any], ref_count: int) -> tuple[str | None, str]:
    """按输入自动挑工作流：条件与顺序都写在 config.yaml 里（页面不给改）。"""
    for rule in cap.get("rules", []) or []:
        text = str(rule.get("when", ""))
        match = re.search(r"图片\s*(=|≥|>|≤|<)\s*(\d+)", text)
        if not match:
            continue
        op, value = match.group(1), int(match.group(2))
        hit = {"=": ref_count == value, "≥": ref_count >= value, ">": ref_count > value,
               "≤": ref_count <= value, "<": ref_count < value}[op]
        if hit and rule.get("wf") in (cap.get("workflows") or []):
            return rule["wf"], f"命中「{text}」"
    if cap.get("fallback") == "auto" and cap.get("workflows"):
        return cap["workflows"][0], "条件都不匹配，用兜底"
    return None, "没有匹配的工作流"


registry = WorkflowRegistry()
