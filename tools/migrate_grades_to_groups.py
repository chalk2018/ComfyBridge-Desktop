"""把 config.yaml 的 sheetGrades 从 18 组合 key 迁到 6 档位组 key。

背景：画法(color/line)与参考(none/lock/style)只改提示词、不改工作流参数，
档位按「版式.子功能」6 组共享即可。创作台的 18 种选择保持不动。

只重写文件末尾的 sheetGrades 段落（保留前面所有手工格式/注释原样），幂等。
"""
from __future__ import annotations

import copy
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CFG = ROOT / "config.yaml"
LAYOUTS = ("A", "B", "C")
INKS = ("color", "line")
REFS = ("none", "lock", "style")


def main() -> int:
    text = CFG.read_text(encoding="utf-8")
    raw = yaml.safe_load(text)
    table = raw.get("sheetGrades") or {}

    new: dict[str, object] = {}
    for layout in LAYOUTS:
        for group, refs in (("t2i", ("none",)), ("i2i", ("lock", "style"))):
            key = f"{layout}.{group}"
            picked = table.get(key)  # 已是新 key：幂等直接沿用
            if not picked:
                for ink in INKS:
                    for ref in refs:
                        rows = table.get(f"{layout}.{ink}.{ref}")
                        if rows:
                            picked = rows
                            break
                    if picked:
                        break
            if picked:
                new[key] = copy.deepcopy(picked)

    # 生成与原文一致风格的 YAML 片段：2 空格缩进一组名，2 空格 - name。
    # 标量自己格式化：safe_dump 会附加文档结束符「...」，不能直接用。
    def scalar(value: object) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float)):
            return str(value)
        # 字符串：含 YAML 特殊字符或空白时加单引号（内部单引号翻倍）
        text = str(value if value is not None else "")
        if text == "" or any(c in text for c in ":#,{}[]&*!|>'\"%@`\n") or text.strip() != text:
            return "'" + text.replace("'", "''") + "'"
        return text

    lines = ["sheetGrades:"]
    for key, rows in new.items():
        lines.append(f"  {key}:")
        for row in rows or []:
            lines.append(f"  - name: {scalar(row.get('name', ''))}")
            lines.append(f"    note: {scalar(row.get('note') or '')}")
            lines.append("    params:")
            for node, fields in (row.get("params") or {}).items():
                lines.append(f"      '{node}':")
                for field, value in fields.items():
                    lines.append(f"        {field}: {scalar(value)}")
    block = "\n".join(lines) + "\n"

    head = text.split("sheetGrades:", 1)[0]
    CFG.write_text(head + block, encoding="utf-8")

    # 自检：写回后能被 yaml 正常解析，且恰好 6 组
    check = yaml.safe_load(CFG.read_text(encoding="utf-8"))
    got = check.get("sheetGrades") or {}
    assert len(got) == 6, f"迁移后应 6 组，实际 {len(got)}"
    print(f"已迁移 sheetGrades：{len(table)} 组 -> {len(got)} 组（前面配置原样保留）")
    for key, rows in got.items():
        print(f"  {key}: {[r.get('name') for r in rows]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
