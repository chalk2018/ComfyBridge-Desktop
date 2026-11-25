"""生成角色设定图全量页面操作测试的用例清单（只生成数据，不触发生成）。"""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).parent
LIB = json.loads((HERE.parents[1] / "prompts" / "character_sheet.json").read_text(encoding="utf-8"))
STYLES = [s["name"] for s in LIB["styles"]]
LINES = [s["id"] for s in LIB["line_styles"]]
REF_IMAGES = [
    "D:/workspace/WorkBuddyProject/ComfyBridge-Desktop/data/uploads/ref_1849.png",
    "D:/ComfyUI-0.37.1/ComfyUI/output/ComfyBridgeOutput/角色设定图/ComfyBridge_job_1897_1899_00001.png",
]

IDENT = [
    "二十岁的古籍修复师", "三十五岁的灯塔守夜人", "十九岁的击剑选手", "四十二岁的星图测绘员",
    "二十六岁的街头魔术师", "二十三岁的潜水摄影师", "三十岁的钟表匠", "二十七岁的山地向导",
    "十八岁的天文社社长", "三十三岁的陶艺师", "二十一岁的邮差", "二十八岁的爵士乐手",
]
FACE = [
    "鹅蛋脸、细眉长眼", "方下颌、浓眉阔鼻", "圆脸、杏眼带浅梨涡", "长脸、丹凤眼高鼻梁",
    "瓜子脸、柳叶眉薄唇", "宽额、圆眼带雀斑", "窄脸、单眼皮薄唇", "娃娃脸、大眼短鼻",
    "颧骨偏高、眼尾上挑", "国字脸、眉骨突出", "心形脸、睫毛浓密", "瘦长脸、眼窝略深",
]
HAIR_C = [
    "黑发低束成髻", "齐耳短发微卷", "银灰长直发披肩", "栗色双麻花辫", "深棕寸头", "黑色高马尾",
    "亚麻色波浪短发", "发顶编一圈细辫", "后梳油头留一缕垂发", "齐刘海锁骨发", "蓬松羊毛卷短发", "长发半扎配木簪",
]
HAIR_L = [
    "深色长发低束成髻", "浅色齐耳短发微卷", "亮面长直发披肩", "哑光双麻花辫", "深色寸头", "深色高马尾",
    "浅色波浪短发", "发顶编一圈细辫", "后梳短发留一缕垂发", "齐刘海及肩发", "蓬松卷短发", "长发半扎配一支素簪",
]
CLOTH_C = [
    "米白交领长衫、深灰长裤、哑光布靴", "藏青连体工装、浅色围裙、亮面短靴",
    "白色击剑服、深色护腿、软底鞋", "深色呢子大衣、浅灰西裤、皮鞋",
    "黑色燕尾马甲、条纹长裤、漆皮鞋", "浅色冲锋衣、深色工装裤、登山靴",
    "深色棉麻短褂、同色阔腿裤、布鞋", "亮面防水外套、深色紧身裤、橡胶靴",
    "月白衬衫、深蓝百褶裙、帆布鞋", "赭色围裙、米色衬衫、软底便鞋",
    "墨绿制服外套、同色长裤、短靴", "酒红丝绒西装、黑长裤、尖头皮鞋",
]
CLOTH_L = [
    "浅色交领长衫、深色长裤、哑光布靴", "深色连体工装、浅色围裙、亮面短靴",
    "浅色立领上衣、深色护腿、软底鞋", "深色长款外套、浅色直筒裤、皮鞋",
    "深色短马甲、条纹长裤、亮面皮鞋", "浅色冲锋衣、深色工装裤、登山靴",
    "深色棉麻短褂、同色阔腿裤、布鞋", "亮面防水外套、深色紧身裤、橡胶靴",
    "浅色衬衫、深色百褶裙、帆布鞋", "深色围裙、浅色衬衫、软底便鞋",
    "深色制服外套、同色长裤、短靴", "浅色丝绒西装、深色长裤、尖头皮鞋",
]
MARK = [
    "左胸别一枚铜制罗盘", "腰间挂一串黄铜钥匙", "左手腕缠深色皮绳", "右肩别一枚星形徽章",
    "颈间挂一枚青玉吊坠", "腰侧别一把折扇", "右手戴一只露指手套", "左耳戴一枚银环",
    "胸前挂一只小怀表", "腰间系一条编织腰带", "左臂缝一块编号布章", "发间别一枚金属发夹",
]
DETAIL = [
    "袖口压一道细缝线", "领口缀一枚小圆扣", "衣摆开一道短衩", "鞋面有一道拼接线",
    "外套内衬露出窄边", "肩线处有一枚小铆钉", "裤脚收一道窄边", "腰后别一小片皮标",
]


def character(i: int, ink: str) -> str:
    hair = HAIR_L if ink == "line" else HAIR_C
    cloth = CLOTH_L if ink == "line" else CLOTH_C
    return (f"{IDENT[i % 12]}，{FACE[(i * 5) % 12]}，{hair[(i * 7) % 12]}，"
            f"穿{cloth[(i * 3) % 12]}，{MARK[(i * 11) % 12]}，{DETAIL[(i + i // 12) % 8]}")


COMBOS = [(l, k, r) for l in ("A", "B", "C") for k in ("color", "line") for r in ("none", "lock", "style")]
LAYOUT_NAME = {"A": "四视图横排", "B": "四宫格", "C": "单张全身"}
INK_NAME = {"color": "彩色", "line": "线稿"}
REF_NAME = {"none": "文生图", "lock": "锁定同角色", "style": "只借画风"}


def gen_case(cid: str, i: int, layout: str, ink: str, ref: str, grade: str,
             style: str, line: str, char: str | None = None,
             style_text: str = "", title: str = "") -> dict:
    return {
        "id": cid, "kind": "gen",
        "title": title or f"{REF_NAME[ref]}·{LAYOUT_NAME[layout]}·{INK_NAME[ink]}·{style or style_text}·{grade}",
        "mode": "t2i" if ref == "none" else "i2i",
        "layout": layout, "ink": ink, "lineStyle": line, "ref": ref,
        "grade": grade, "style": style, "styleText": style_text,
        "character": char if char is not None else character(i, ink),
        "refImage": REF_IMAGES[i % 2],
    }


r1: list[dict] = []
for i, (l, k, r) in enumerate(COMBOS):
    style = STYLES[i % 12]
    line = LINES[i % 4] if k == "line" else LINES[0]
    r1.append(gen_case(f"R1-{l}.{k}.{r}-默认", i, l, k, r, "默认", style, line))

r2: list[dict] = []
for gi, grade in enumerate(("快速", "精细")):
    for i, (l, k, r) in enumerate(COMBOS):
        idx = 18 + gi * 18 + i
        style = STYLES[(idx + gi * 5) % 12]
        line = LINES[(i + gi) % 4] if k == "line" else LINES[0]
        r2.append(gen_case(f"R2-{l}.{k}.{r}-{grade}", idx, l, k, r, grade, style, line))

r3: list[dict] = []
for j, style in enumerate(STYLES):
    r3.append(gen_case(f"R3-风格-{style}", 54 + j, "A", "color", "none", "默认", style, LINES[0],
                       title=f"专项·12 类画风·{style}"))
for j, line in enumerate(LINES):
    lname = next(x["name"] for x in LIB["line_styles"] if x["id"] == line)
    r3.append(gen_case(f"R3-线稿-{line}", 66 + j, "A", "line", "none", "默认", "3D 动画", line,
                       title=f"专项·4 种线稿句·{lname}"))
r3.append(gen_case("R3-自定义风格-彩色", 70, "A", "color", "none", "默认", "", LINES[0],
                   style_text="北欧童话绘本风，柔和干笔触，低饱和暖灰调，边缘留手绘毛边",
                   title="专项·自定义风格（彩色）"))
r3.append(gen_case("R3-自定义风格-线稿", 71, "A", "line", "none", "默认", "", LINES[3],
                   style_text="建筑速写式的干净线条，结构线略重于轮廓线",
                   title="专项·自定义风格（线稿）"))
SAME_CHAR = character(72, "color") + "，身形挺拔、站姿端正"
for j, grade in enumerate(("默认", "快速", "精细")):
    r3.append(gen_case(f"R3-档位同提示词-{grade}", 72 + j, "A", "color", "none", grade,
                       STYLES[3], LINES[0], char=SAME_CHAR,
                       title=f"专项·档位只改参数不改提示词·{grade}"))

# 「档位同提示词」三条是刻意用同一句做对照，其余每条都必须不同
chars = [c["character"] for c in r1 + r2 + r3
         if not c["id"].startswith("R3-档位同提示词")]
assert len(set(chars)) == len(chars), f"角色描述有重复：{len(chars) - len(set(chars))} 条"

r4: list[dict] = []
base_ids = ["R1-A.color.none-默认", "R1-B.line.none-默认", "R1-A.line.style-默认"]
for bi, base in enumerate(base_ids):
    for grade in ("默认", "快速", "精细"):
        r4.append({"id": f"R4-Q{bi + 1}-{grade}", "kind": "queue", "from_case": base,
                   "grade": grade, "title": f"加入队列生成·第 {bi + 1} 组·{grade} 档"})
    r4.append({"id": f"R4-A{bi + 1}-1", "kind": "again", "from_case": base,
               "title": f"再来一张·第 {bi + 1} 组·第 1 次"})
    r4.append({"id": f"R4-A{bi + 1}-2", "kind": "again", "from_case": f"R4-A{bi + 1}-1",
               "title": f"再来一张·第 {bi + 1} 组·第 2 次"})

for name, data in (("cases_r1.json", r1), ("cases_r2.json", r2),
                   ("cases_r3.json", r3), ("cases_r4.json", r4)):
    (HERE / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(name, len(data))
print("唯一角色描述", len(set(chars)), "/", len(chars))
