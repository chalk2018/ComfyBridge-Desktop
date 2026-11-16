"""生成修复后的复测 / 回归用例清单。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from make_cases import character, gen_case, STYLES, LINES, REF_IMAGES  # noqa: E402

EXTRA = ["肩背挺直", "重心略偏左脚", "双手自然垂放", "下巴微抬", "袖口整齐",
         "衣领平整", "站姿放松", "目光平视前方"]


def ch(j: int, ink: str) -> str:
    return character(100 + j, ink) + "，" + EXTRA[j % len(EXTRA)] + "，复测标记" + str(j)


fix: list[dict] = []
j = 0

# 1) 用户举例的组合：图生图 · 四视图横排 · 线稿 · 铅笔线稿 · 3D 动画
for grade in ("默认", "快速", "精细"):
    fix.append(gen_case(f"F-举例-{grade}", 100 + j, "A", "line", "style", grade,
                        "3D 动画", "W4", ch(j, "line"),
                        title=f"复测举例组合·图生图·四视图横排·线稿·铅笔线稿·3D 动画·{grade}"))
    j += 1

# 2) 4 种线稿句 × 多组合 × 多档位
line_plan = [
    ("W1", "A", "none", "默认", "3D 动画"),
    ("W2", "A", "none", "默认", "游戏概念"),
    ("W3", "A", "none", "默认", "水墨国风"),
    ("W4", "A", "none", "默认", "日系赛璐璐"),
    ("W1", "A", "style", "精细", "Q 版"),
    ("W4", "A", "style", "快速", "绘本"),
    ("W2", "B", "lock", "快速", "扁平矢量"),
    ("W3", "C", "style", "默认", "暗黑哥特"),
]
for line, layout, ref, grade, style in line_plan:
    fix.append(gen_case(f"F-线稿-{line}-{layout}.{ref}-{grade}", 100 + j,
                        layout, "line", ref, grade, style, line, ch(j, "line"),
                        title=f"复测线稿·{line}·{layout}·{ref}·{grade}·{style}"))
    j += 1

# 3) 彩色回归（提示词应保持原样） + 自定义风格
for k, (grade, style) in enumerate((("默认", "3D 动画"), ("快速", "水墨国风"), ("精细", "Q 版"))):
    fix.append(gen_case(f"F-彩色-{grade}", 100 + j, "A", "color", "none", grade,
                        style, LINES[0], ch(j, "color"),
                        title=f"回归彩色·{grade}·{style}"))
    j += 1
fix.append(gen_case("F-自定义风格-线稿", 100 + j, "A", "line", "none", "默认", "", "W4",
                    ch(j, "line"), style_text="建筑速写式的干净线条，结构线略重于轮廓线",
                    title="复测自定义画风（线稿）"))
j += 1

# 4) 档位不改提示词对照（同一句、三档）
SAME = ch(999, "color")
for k, grade in enumerate(("默认", "快速", "精细")):
    fix.append(gen_case(f"F-档位同提示词-{grade}", 100 + j, "A", "color", "none", grade,
                        "写实摄影", LINES[0], SAME,
                        title=f"回归·档位只改参数不改提示词·{grade}"))
    j += 1

chars = [c["character"] for c in fix if not c["id"].startswith("F-档位同提示词")]
assert len(set(chars)) == len(chars), "复测用例角色描述重复"

# 5) 队列 / 再来一张回归（依赖 F-举例-默认）
reg: list[dict] = []
for grade in ("默认", "快速", "精细"):
    reg.append({"id": f"F-Q-{grade}", "kind": "queue", "from_case": "F-举例-默认",
                "grade": grade, "title": f"回归·加入队列生成·{grade}"})
reg.append({"id": "F-A-1", "kind": "again", "from_case": "F-举例-默认",
            "title": "回归·再来一张·第 1 次"})
reg.append({"id": "F-A-2", "kind": "again", "from_case": "F-A-1",
            "title": "回归·再来一张·第 2 次"})

(HERE / "cases_f1.json").write_text(json.dumps(fix, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
(HERE / "cases_f2.json").write_text(json.dumps(reg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print("cases_f1.json", len(fix), "cases_f2.json", len(reg))
