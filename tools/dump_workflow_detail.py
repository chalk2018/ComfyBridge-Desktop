"""导出 subgraph 工作流的完整结构，用于编写 UI→API 转换器。

只读 ComfyUI 目录，输出写到项目 _reference 下。

Usage:
    python tools/dump_workflow_detail.py
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
OUT = ROOT / "_reference" / "workflow_detail.txt"


def brief(value: object, limit: int = 500) -> str:
    """把任意值压缩成一行 JSON 文本。"""
    text = json.dumps(value, ensure_ascii=False)
    return text if len(text) <= limit else text[:limit] + f" ...({len(text)} chars)"


def dump_node(node: dict, indent: str = "  ") -> list[str]:
    lines = [
        f"{indent}NODE id={node.get('id')} type={node.get('type')} mode={node.get('mode')} title={node.get('title','')}"
    ]
    for key in ("order", "pos", "size", "properties", "flags", "color", "bgcolor"):
        if key in node:
            lines.append(f"{indent}  {key} = {brief(node[key], 300)}")
    if "widgets_values" in node:
        lines.append(f"{indent}  widgets_values = {brief(node['widgets_values'], 900)}")
    if "inputs" in node:
        lines.append(f"{indent}  inputs = {brief(node['inputs'], 1600)}")
    if "outputs" in node:
        lines.append(f"{indent}  outputs = {brief(node['outputs'], 900)}")
    return lines


def main() -> None:
    """逐文件导出结构。"""
    lines: list[str] = []
    for name in FILES:
        data = json.loads((WF_DIR / name).read_text(encoding="utf-8"))
        lines.append(f"########## {name} ##########")
        lines.append(f"meta: id={data.get('id')} revision={data.get('revision')} version={data.get('version')}")
        lines.append(f"config = {brief(data.get('config'), 400)}")
        lines.append(f"extra = {brief(data.get('extra'), 400)}")
        lines.append(f"--- ROOT nodes ({len(data.get('nodes', []))}) ---")
        for node in data.get("nodes", []):
            lines.extend(dump_node(node))
        lines.append(f"--- ROOT links ({len(data.get('links', []))}) ---")
        for link in data.get("links", []):
            lines.append("  " + brief(link, 300))
        defs = data.get("definitions", {})
        lines.append(f"--- definitions keys: {list(defs)} ---")
        subgraphs = defs.get("subgraphs", [])
        lines.append(f"--- subgraphs ({len(subgraphs)}) ---")
        for sg in subgraphs:
            lines.append(f"  SUBGRAPH id={sg.get('id')} name={sg.get('name')} keys={list(sg)}")
            for key in ("inputs", "outputs", "external", "widgets", "config"):
                if key in sg:
                    lines.append(f"    {key} = {brief(sg[key], 1200)}")
            lines.append(f"    nodes ({len(sg.get('nodes', []))})")
            for node in sg.get("nodes", []):
                lines.extend(dump_node(node, "      "))
            lines.append(f"    links ({len(sg.get('links', []))})")
            for link in sg.get("links", []):
                lines.append("      " + brief(link, 300))
        lines.append("")
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"written {OUT}: {len(lines)} lines, {OUT.stat().st_size} bytes")


if __name__ == "__main__":
    main()
