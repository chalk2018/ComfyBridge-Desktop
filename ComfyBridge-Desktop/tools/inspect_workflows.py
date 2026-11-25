"""检查 ComfyUI 工作流 JSON 的格式与节点结构（只读 ComfyUI 文件，结果写入项目目录）。

Usage:
    python tools/inspect_workflows.py
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WF_DIR = Path(
    r"D:\ComfyUI-Desktop\Comfy-Desktop\ComfyUI-Installs\ComfyUI (1)\ComfyUI\user\default\workflows"
)
FILES = (
    "[图][文生图]image_qwen_image_2_1_turbo_t2i.json",
    "[图][编辑]image_qwen_image_2_1_turbo_image_edit.json",
)
OUT = ROOT / "_reference" / "workflows_inspect.txt"


def describe_node(node: dict) -> str:
    """把单个节点压缩成一行摘要。"""
    parts = [
        f"id={node.get('id')}",
        f"type={node.get('type') or node.get('class_type')}",
        f"mode={node.get('mode', '-')}",
        f"title={node.get('title', '')}",
    ]
    widgets = node.get("widgets_values")
    if widgets is not None:
        text = json.dumps(widgets, ensure_ascii=False)
        parts.append(f"widgets={text[:260]}")
    inputs = node.get("inputs")
    if isinstance(inputs, dict):
        parts.append(f"api_inputs={json.dumps(inputs, ensure_ascii=False)[:260]}")
    elif isinstance(inputs, list):
        parts.append(
            "ui_inputs="
            + json.dumps(
                [{k: i.get(k) for k in ("name", "type", "link", "widget") if k in i} for i in inputs],
                ensure_ascii=False,
            )[:300]
        )
    return " | ".join(parts)


def main() -> None:
    """输出两个工作流的结构摘要。"""
    lines: list[str] = []
    for name in FILES:
        path = WF_DIR / name
        lines.append(f"===== {name} =====")
        if not path.exists():
            lines.append("NOT FOUND")
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        lines.append(f"size={path.stat().st_size} bytes; top keys={list(data)[:12]}")
        nodes = data.get("nodes")
        if isinstance(nodes, list):
            lines.append(f"UI FORMAT: nodes={len(nodes)}, links={len(data.get('links', []))}")
            lines.append(f"extra keys: {list(data.keys())}")
            for node in nodes:
                lines.append("  " + describe_node(node))
            lines.append("--- links ---")
            for link in data.get("links", [])[:80]:
                lines.append("  " + json.dumps(link, ensure_ascii=False))
        else:
            lines.append(f"API FORMAT: {len(data)} nodes")
            for key, node in data.items():
                lines.append(f"  {key}: " + describe_node(node))
        lines.append("")
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"written: {OUT} ({len(lines)} lines)")


if __name__ == "__main__":
    main()
