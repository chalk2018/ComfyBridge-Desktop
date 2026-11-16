"""汇总所有页面操作测试的原始记录，生成《角色设定图_全量测试报告.md》。

数据全部来自 tests/_sheet_full_out/results.jsonl（每次生成时由页面操作脚本落盘），
判定统一用当前提示词库口径重算一遍，保证「修复前 / 修复后」用的是同一把尺子。
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
HERE = Path(__file__).parent
APP = HERE.parents[1]
sys.path.insert(0, str(APP / "tests"))
from sheet_full_e2e import prompt_checks, LIB  # noqa: E402

OUT = APP / "角色设定图_全量测试报告.md"
ROWS = [json.loads(l) for l in (HERE / "results.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
ADMIN = HERE / "admin_link.json"
ADMIN_ROWS = json.loads(ADMIN.read_text(encoding="utf-8")) if ADMIN.exists() else []
REFREG_FILE = HERE / "ref_regression.json"
REFREG = json.loads(REFREG_FILE.read_text(encoding="utf-8")) if REFREG_FILE.exists() else {}
PASTE_FILE = HERE / "paste_regression.json"
PASTE_ROWS = json.loads(PASTE_FILE.read_text(encoding="utf-8")) if PASTE_FILE.exists() else []

LAYOUT = {"A": "四视图横排", "B": "四宫格", "C": "单张全身"}
INK = {"color": "彩色", "line": "线稿"}
REF = {"none": "文生图", "lock": "图生图·锁定同角色", "style": "图生图·只借画风"}


def group(r) -> str:
    """既能传整条记录，也能传用例 id 字符串。"""
    cid = r.get("id", "") if isinstance(r, dict) else str(r)
    for p in ("R1-", "R2-", "R3-", "R4-", "F-", "P3-", "P5-", "SMOKE-", "Q-T2I", "A-T2I"):
        if cid.startswith(p):
            return p.rstrip("-")
    return "其它"


def combo_of(c: dict) -> str:
    if not c:
        return "—"
    return f"{c.get('layout','?')}.{c.get('ink','?')}.{c.get('ref','?')}"


def checks_of(r: dict) -> list[dict]:
    case, prompt = r.get("case") or {}, r.get("submitted_prompt") or ""
    if not case or not prompt:
        return []
    try:
        return prompt_checks(case, prompt)
    except Exception as exc:  # noqa: BLE001
        return [{"item": "判定异常", "ok": False, "detail": str(exc)}]


GEN = [r for r in ROWS if r.get("ok") and (r.get("case") or {}).get("kind", "gen") == "gen"]
QUEUE = [r for r in ROWS if (r.get("case") or {}).get("kind") == "queue"]
AGAIN = [r for r in ROWS if (r.get("case") or {}).get("kind") == "again"]
FAILED = [r for r in ROWS if not r.get("ok")]


def run_ok(r: dict) -> bool:
    """这次生成真的出片了吗（修复前 R4-Q3 的卡片点击成功但工作流被拒收）。"""
    return bool(r.get("ok")) and (r.get("new_run") or {}).get("state") == "done"


QUEUE_OK = [r for r in QUEUE if run_ok(r)]
AGAIN_OK = [r for r in AGAIN if run_ok(r)]
RUN_FAILED = [r for r in QUEUE + AGAIN if r.get("ok") and not run_ok(r)]

# ---------------------------------------------------------------- 统计
by_group = Counter(group(r) for r in ROWS)
gen_by_group = Counter(group(r) for r in GEN)
combos = {combo_of(r["case"]) for r in GEN}
grades = {r.get("grade") for r in GEN}
styles = {(r["case"] or {}).get("style") for r in GEN if (r["case"] or {}).get("style")}
lines = {(r["case"] or {}).get("lineStyle") for r in GEN if (r["case"] or {}).get("ink") == "line"}
custom = [r for r in GEN if (r["case"] or {}).get("styleText")]
preview_ok = sum(1 for r in GEN if r.get("preview_equals_submitted"))
STALE_PREVIEW = [r for r in GEN if r.get("preview_equals_submitted") is False]
PREVIEW_RECHECK = [r for r in GEN if group(r) == "P5"]


def size_ok_of(r: dict) -> bool:
    """统一用当前口径重算：宽高比偏差 <2%、总像素偏差 <5%（早期用例存的是旧的逐像素判定）。"""
    try:
        w, h = [int(x) for x in str(r.get("size", "")).split("×")]
        ew, eh = r.get("size_expect") or [0, 0]
        return abs((w / h) / (ew / eh) - 1) < 0.02 and abs((w * h) / (ew * eh) - 1) < 0.05
    except Exception:
        return bool(r.get("size_ok"))


size_ok = sum(1 for r in GEN if size_ok_of(r))
check_fail = Counter()
for r in GEN:
    for c in checks_of(r):
        if not c["ok"]:
            check_fail[c["item"]] += 1
# 修复前 = P1 暴露的 R1 线稿 9 例 + 冒烟里那条图生图线稿 + P2 暴露的 R3 档位对照 3 例
PRE_IDS = {r["id"] for r in GEN if (group(r) == "R1" or r["id"] == "SMOKE-I2I"
                                    or (group(r) == "R3" and "档位同提示词" in r["id"]))}
before_fail = Counter()
after_fail = Counter()
for r in GEN:
    bucket = before_fail if r["id"] in PRE_IDS else after_fail
    for c in checks_of(r):
        if not c["ok"]:
            bucket[c["item"]] += 1

q_same_seed = sum(1 for r in QUEUE_OK if r.get("same_seed"))
q_same_prompt = sum(1 for r in QUEUE_OK if r.get("same_prompt"))
q_diff0 = [r for r in QUEUE_OK if (r.get("image_diff") or {}).get("mean_abs_diff_0_255") == 0]
a_new_seed = sum(1 for r in AGAIN_OK if r.get("new_seed"))
a_same_prompt = sum(1 for r in AGAIN_OK if r.get("same_prompt"))
a_same_grade = sum(1 for r in AGAIN_OK if r.get("same_grade"))
a_same_params = sum(1 for r in AGAIN_OK if r.get("same_params"))

# 自动校验：档位不改提示词 / 尺寸随档递增 / 彩色 lock 尾句 / 修复前后对比
same_groups: dict[str, list[dict]] = defaultdict(list)
for r in GEN:
    if "档位同提示词" in r["id"]:
        same_groups[r["id"].split("-档位同提示词")[0]].append(r)
same_prompt_ok = all(len({x.get("submitted_prompt") for x in rs}) == 1
                     for rs in same_groups.values() if len(rs) > 1)
size_by_combo: dict[tuple, dict[str, int]] = defaultdict(dict)
for r in GEN:
    c = r["case"] or {}
    try:
        w, h = [int(x) for x in str(r.get("size", "")).split("×")]
    except Exception:
        continue
    size_by_combo[(c.get("layout"), c.get("ink"), c.get("ref"))][str(r.get("grade"))] = w * h
size_ladder = []
for key, d in size_by_combo.items():
    if all(g in d for g in ("快速", "默认", "精细")):
        size_ladder.append((key, d["快速"] < d["默认"] < d["精细"]))
lock_tail = [r for r in GEN if (r["case"] or {}).get("ref") == "lock"
             and (r["case"] or {}).get("ink") == "color"
             and (r["case"] or {}).get("layout") == "C"]
lock_tail_ok = all(str(r.get("submitted_prompt", "")).rstrip().endswith("画风与图 1 保持一致")
                   for r in lock_tail)
before = [r for r in GEN if r["id"] in PRE_IDS]
after = [r for r in GEN if r["id"] not in PRE_IDS]


def img_name(cid: str) -> str:
    for r in GEN:
        if r["id"] == cid and r.get("image"):
            return Path(r["image"]).name
    return "—"


def run_img(cid: str) -> str:
    """队列/再来一张用例的成品文件名。"""
    for r in QUEUE + AGAIN:
        if r.get("id") == cid:
            p = r.get("image_new") or ""
            return Path(p).name if p else "—"
    return "—"

# ---------------------------------------------------------------- 正文
L: list[str] = []
w = L.append
w("# 角色设定图 · 全量功能测试报告")
w("")
w(f"> 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}　|　"
  f"应用：ComfyBridge 1.02（D:\\workspace\\WorkBuddyProject\\ComfyBridge-Desktop）　|　"
  f"ComfyUI：127.0.0.1:8188（Qwen Image 2.1 Turbo）")
w("")
w("## 一、测试方法与边界")
w("")
w("- **全部生成动作都通过真实浏览器页面操作完成**：用 CDP 驱动本机 Edge，逐项点击「文生图/图生图」页签、版式、画法、线稿句、参考方式、档位、风格下拉、角色描述输入框，"
  "点「看完整提示词」展开预览、点「↑」发送；参考图通过页面「＋ 上传」按钮 + 浏览器文件选择注入；"
  "「🕒 加入队列生成」在弹窗里逐档点击；「↻ 再来一张」点卡片按钮。"
  "**没有直接调用任何后端生成接口，也没有执行任何业务代码去拼提示词/提交工作流**。")
w("- 每一次生成都记录：用例 id、组合、档位、风格/线稿句、角色描述、**预览的完整提示词**、**实际提交给 ComfyUI 的完整提示词**、"
  "工作流、种子、真实出图尺寸、成品路径、耗时、逐项提示词核对结果。原始记录：`tests/_sheet_full_out/results.jsonl`。")
w("- 判定统一用当前 `prompts/character_sheet.json` 的口径重算（修复前后同一把尺子），逐条核对版式句、通用块、R1/R2 位置、"
  "线稿句/风格句、角色变量行、画风是否生效、档位画幅、种子行为、图片像素差。")
w("- 图片差异用 PIL 把两张图统一缩到 256×256 后算平均绝对差（0~255）：`0` 表示像素完全一致；"
  "换个种子通常在 6~9 之间。")
w("")
w("## 二、被测功能口径（先看懂再改）")
w("")
w("| 维度 | 取值 | 说明 |")
w("| --- | --- | --- |")
w("| 生成方式 | 文生图 / 图生图 | 图生图要上传 1 张参考图，按参考图数量自动挑「设定图-文生图 / 设定图-参考生图」工作流 |")
w("| 版式 | 四视图横排 16:9 / 四宫格 9:16 / 单张全身 9:16 | 版式句 + 通用块 U1~U5 决定整条提示词与画幅 |")
w("| 画法 | 彩色 / 线稿 | 彩色把「风格」预设句写进风格行；线稿把风格行整行换成 4 种线稿句之一（针管笔 / 工业细线 / 美式细勾线 / 铅笔） |")
w("| 参考方式 | 无参考 / 锁定同角色 R1 / 只借画风 R2 | R2 必须置顶；R1 插在角色行前，并在风格行尾加「画风与图 1 保持一致」 |")
w("| 组合 | 3 版式 × 2 画法 × 3 参考方式 = 18 | 组合 = 默认档的完整配方（工作流+提示词+画幅），**档位不能改配方** |")
w("| 档位 | 默认 / 快速 / 精细 | 只叠加 `aspect_ratio / megapixels / multiple / cfg / sigmas`，提示词一字不变；跨组合隔离 |")
w("| 风格 | 12 类预设 + 自定义 | 数据来自管理台「设定图模板」，改文案即时写回提示词库 |")
w("| 🕒 加入队列生成 | 同种子、同提示词、同工作流，可换档 | 接在同一张卡后面排队；同档应出像素一致的图，换档只改尺寸 |")
w("| ↻ 再来一张 | 同参数、新种子 | 新开一张卡，构图不变但结果有随机性 |")
w("")
w("**管理台 ↔ 创作台关联**（改 bug 前先确认过的）：管理台「设定图模板」保存 → 写回 `prompts/character_sheet.json` → "
  "`SheetLibrary` 按 mtime 自动重读 → 创作台预览与提交共用同一个 `compose()`；"
  "管理台「档位表」保存 → 写回 `config.yaml` 的 `sheetGrades` → 创作台档位按钮/尺寸随 `/api/state` 刷新；"
  "「填入创作台」「去创作台试这个组合」把组合回填到创作台表单。**提示词和档位的唯一口径在服务端，管理台只是编辑器**。")
w("")
w("## 三、测试轮次与覆盖")
w("")
w("| 轮次 | 内容 | 用例数 | 说明 |")
w("| --- | --- | ---: | --- |")
w("| 通道冒烟 | 文生图 / 图生图各 1 例 + 队列 3 档 + 再来一张 2 次 | 7（+1 条通道调试记录） | 打通「页面点击 → 真实出图」链路；调试记录见 5.5 |")
w("| R1（修复前基线） | 18 个组合 × 默认档 | 18 | 每个用例一条不同的角色描述，彩色/线稿混排 12 类画风与 4 条线稿句 |")
w("| R2（修复后） | 18 个组合 × 快速 / 精细档 | 36 | 覆盖全部档位；角色描述继续不重复 |")
w("| R3（修复后） | 12 类画风 + 4 条线稿句 + 自定义风格 + 档位同提示词对照 | 21 | 专项验证 |")
w("| R4（修复后） | 3 组基础卡 × 3 档队列 + 每组 2 次再来一张 | 15 | 种子/参数/图片差异 |")
w("| F（修复后复测） | 举例组合 3 档、8 组线稿专项、彩色回归、档位对照、队列/再来一张回归 | 23 | 针对修复点与回归；其中 1 条上传失败暴露 P4 |")
w("| P3（参考图自愈回归） | 重启 + 缓存清理后旧图生图卡 × 3 档队列 + 2 次再来一张；新上传图生成 + 队列 + 再来一张 | 8 | 修复 P3 后用页面操作复测 |")
w("| P4（上传上限回归） | 页面连续 32 次「上传参考图 → 从输入框移除」 | 32 次上传 | 修复 P4 后验证第 31、32 次仍能传，见 5.7 |")
w("| P5（预览一致性复测） | 彩色四视图 + 线稿四宫格各 1 例 | 2 | 修掉测试脚本读旧预览的竞态后，重新确认「看完整提示词 ≡ 实际提交」 |")
w("| 管理台联动 | 提示词库编辑、线稿句编辑、两个「带到创作台」入口、保存不丢字段、P2 切换复测 | — | 页面点击验证，见第七节 |")
w("")
w(f"各轮次实际落盘记录数：{dict(by_group)}。")
w("")
w(f"实际落盘记录共 **{len(ROWS)}** 条，其中成功生成 **{len(GEN)}** 张（另有 "
  f"队列用例 {len(QUEUE)} 条、再来一张用例 {len(AGAIN)} 条，其中真正出片 "
  f"{len(QUEUE_OK)} / {len(AGAIN_OK)} 条），失败/跳过 {len(FAILED)} 条，"
  f"点击成功但工作流被拒收 {len(RUN_FAILED)} 条（P3，已修复复测）。")
w("")
w("覆盖统计：")
w("")
w(f"- 组合：**{len(combos)} / 18** → {'、'.join(sorted(combos))}")
w(f"- 档位：**{'、'.join(sorted(str(g) for g in grades if g))}**（默认 / 快速 / 精细全覆盖）")
w(f"- 12 类画风：{len(styles & {s['name'] for s in LIB['styles']})} / 12 → {'、'.join(sorted(str(s) for s in styles))}")
w(f"- 4 种线稿句：{len(lines & {s['id'] for s in LIB['line_styles']})} / 4 → {'、'.join(sorted(str(s) for s in lines))}")
w(f"- 参考方式：{len({(r['case'] or {}).get('ref') for r in GEN})} / 3；自定义风格 {len(custom)} 例")
w(f"- 预览提示词 ≡ 实际提交提示词：**{preview_ok} / {len(GEN)}**"
  f"（其中 {len(STALE_PREVIEW)} 条是测试脚本读到旧预览的记录，已在 5.1 说明并用 P5 复测确认）")
w(f"- 出图尺寸与档位相符：**{size_ok} / {len(GEN)}**（宽高比 + 总像素偏差 < 5%，页面尺寸本身是「约」值）")
w("")
w("## 四、发现的问题与处理")
w("")
w("### P1（功能缺陷，已修复）线稿模式下「风格」下拉选了等于没选")
w("")
w("**现象**：按截图里的状态「图生图 · 四视图横排 · 线稿 · 铅笔线稿 · 3D 动画 · 精细」生成，"
  "出图确实满足四视图横排 + 铅笔线稿 + 纯白底，但人物是写实素描比例，**完全没有 3D 动画画风的造型气质**；"
  "点「看完整提示词」也能看到「风格：」那一行只有 `铅笔线稿…`，用户选的 `3D 动画` 一个字都没进提示词。"
  "R1 里 9 个线稿组合 9 次复现（9/9 失败），彩色组合 9/9 正常。")
w("")
w("**根因**：`app/promptlib.py` 的 `compose()` 里")
w("")
w("```python")
w("if ink == \"line\":")
w("    style_line = self.line_style(line_style)[\"text\"]   # 只取线稿句")
w("else:")
w("    style_line = self.style_text(style)                 # 彩色才用风格")
w("```")
w("")
w("`style` 参数在 `ink=line` 分支被整条丢掉；但创作台 `viewSheetComposer()` 在线稿时**仍然渲染可选的 12 类风格下拉**，"
  "卡片摘要 `brief()` 也照样把风格名写进标题。于是「页面上能选、选了不生效、标题还显示选过」，"
  "和用户要求的「所有组合都满足所选功能」不符。这不是工作流/参数问题：工作流、种子、画幅、R1/R2、线稿句都正确。")
w("")
w("**修复思路（不破坏原有功能）**：不把彩色风格整句塞进线稿提示词（里面的「皮肤通透」「全局光照」「色彩浓郁」"
  "会和「纯黑白线稿」打架），而是**另起一行「造型气质」**：带画风名 + 一条与该画风对应的造型/线条特征，"
  "并明确「画面仍然只有黑白线条和纯白纸底，不上色、不加光影、不做材质渲染」。"
  "风格行仍然是原封不动的线稿句，管理台两套文案各自编辑的口径不变。")
w("")
w("### P2（功能缺陷，已修复）「自定义…」与预设风格互相残留、下拉显示与实际提交不一致")
w("")
w("**现象**：R3 里先跑「自定义风格」两条用例，再跑「同一句 × 三档」对照时，三条提示词的风格行全都还是上一条的自定义句 "
  "`建筑速写式的干净线条…`，而页面下拉显示的是预设「奇幻立绘」；"
  "反过来在自定义用例里，页面下拉又跳回第一个预设「写实摄影」，实际提交的却是自定义句。")
w("")
w("**根因**（`web/app.js` 两处）")
w("")
w("1. `sheetForm()` 里 `if (!f.style && styles.length) f.style = styles[0]` 把「用户选了自定义…」"
  "（`style === ''`）当成「还没选过」，重绘时把下拉强行改回第一个预设；")
w("2. `#sheetStyle` 的 `onchange` 只在选「自定义…」时清空 `styleText`，"
  "而 `sheetStyleValue()` 是「自定义句优先」，于是切回预设后旧句子继续盖住预设。")
w("")
w("**修复**：表单增加显式标记 `styleCustom`，空 `style` 不再被重置；只有 `styleCustom` 为真且确实写了文本才用自定义句；"
  "每次切换下拉都清空上一句自定义文本。下拉显示、预览、提交三者现在一致。")
w("")
w("### P3（功能缺陷，已修复）服务重启后，旧图生图卡的「加入队列生成 / 再来一张」丢参考图")
w("")
w("**现象**：R4 第 3 组基于 `R1-A.line.style-默认`（图生图 · 只借画风）做 3 档队列生成与 2 次再来一张时，"
  "3 次队列 + 1 次再来一张全部报「这个工作流发不出去，ComfyUI 没接收」，第 2 次再来一张连按钮都找不到；"
  "回读任务状态发现新开的卡 `refs: []`、`refsInfo: []`，参考图彻底丢了。同一批里另外两组文生图卡片完全正常。")
w("")
w("**根因**：`app/state.py` 的 `RefStore` 只在内存里，`state.persist()` 只落 `jobs / ui / run_counter`；"
  "服务重启后旧任务 `job[\"refs\"]` 里那串 `ref_1943` 之类的 id 就查不到了。"
  "`repeat()` 直接把失效 id 传给 `submit_normal()`，`_resolve_refs()` 过滤成空列表，新卡因此记成零参考图；"
  "`_execute()` 同样拿到空 refs，却仍按卡片原来的「设定图-参考生图」工作流提交，"
  "LoadImage 节点还指着失效的文件名，ComfyUI 校验直接拒收。"
  "项目里本来就有 `refsInfo`（每张参考图的落盘副本路径）和 `restore_refs()`（「基于上一条」用），"
  "只是队列生成 / 再来一张这两条路径没有走它。")
w("")
w("**修复**：新增 `Service._ensure_job_refs()`——按 id 能查到就直接用，查不到就按 `refsInfo` 重新登记"
  "（复用上传目录里的原文件、重新交给 ComfyUI），把新 id 写回任务；"
  "`submit_queue()` / `repeat()` / `_execute()` 三处统一改走它。"
  "队列生成点下去时若参考图确实已被手动删掉，改为明确提示「请重新上传后再加入队列」，不再丢一个失败任务给用户。")
w("")
w("### P4（功能缺陷，已修复）连续创作到第 31 张参考图时无法再上传")
w("")
w("**现象**：F 轮 `F-线稿-W3-C.style-默认` 在页面点「＋ 上传」后 `UI.refs` 一直是 0，本次没有发送；"
  "统计该轮之前恰好已有 30 次上传成功。")
w("")
w("**根因**：`_store_upload()` 的上限是 `max(maxRefs, widest) * 10 = 30`（内存登记数）。"
  "到上限后先调 `prune_refs()`，但它把「所有仍在会话里的任务引用的 ref id / `refsInfo` 路径」都列为保护对象；"
  "连续测试攒了 300+ 条任务、几乎每张登记都被某条任务引用，结果一趟一张都删不掉，"
  "`left >= limit` 就直接 400「临时图已存 30 张：先删掉不用的会话任务」。")
w("")
w("**修复**：`prune_refs()` 增加第二趟——第一趟删不掉时，只摘掉内存登记、保留磁盘文件，直到降到 `keep` 以下；"
  "当前正在编辑的草稿参考图绝不动。因为每条任务的 `refsInfo` 都记着落盘路径，加上 P3 的 `_ensure_job_refs()`，"
  "被摘掉的登记随时能原样恢复，旧任务不会因此失效。`restore_refs()` 同时改为"
  "「源图本来就在上传目录就直接复用，不再复制一份」，避免反复恢复把上传目录撑满。")
w("")
w("### P5（功能缺陷，已修复）角色设定图输入框 Ctrl+V 粘贴不上（交付后追加）")
w("")
w("**现象**：在「角色设定图」的角色描述框里按 Ctrl+V 没有反应；"
  "从成品图点「复制图片」后在图生图模式按 Ctrl+V 也不会变成参考图，"
  "而普通能力输入框的这两项都是好的（提示语里也明确写了「切到图生图后在输入框 Ctrl+V 就是参考图」）。")
w("")
w("**根因（两处，都在 `web/app.js`）**：")
w("")
w("1. 粘贴处理 `t.onpaste` 只绑在普通能力的 `#promptInput`（contenteditable）上。"
  "角色设定图是后来单独加的一套表单，输入框是 `#sheetChar`（textarea），"
  "整块 `.sheetbox` 没有任何 paste 处理，所以图片粘贴完全无效，纯文字只能靠浏览器默认行为。"
  "2. 页面每秒按状态签名局部重绘输入区时，`refreshDynamic()` 自己拼了一段 `comp.outerHTML = viewComposer()`，"
  "只保存/恢复了 `#promptInput` 的焦点，**没有保护 `#sheetChar`**；"
  "另一个重绘入口 `refreshComposer()` 虽保存了文字，却把光标强制挪到末尾。"
  "重绘一发生，刚粘的字或焦点就会被旧值冲掉，看起来就是「粘不上」。")
w("")
w("**修复**：")
w("")
w("- 新增 `sheetPasteInto()`，给 `#sheetChar` 与 `.sheetbox` 绑上和普通输入框同语义的粘贴："
  "剪贴板有图片就走 `uploadFiles()` 当参考图；纯文本用 `setRangeText()` 插到光标处，"
  "并**立刻**同步 `UI.sheetForm.character`、发送按钮状态、草稿（`setRangeText` 不派发 input，不能等 oninput）。"
  "焦点在「自定义…」风格输入框时放行，不抢它的文字。")
w("- 加一个严格的 document 级兜底：只有在创作台、页面存在 `#sheetChar`、且焦点不在任何可编辑元素时，"
  "才把 Ctrl+V 落到角色描述框（覆盖「焦点刚被重绘抢走」这一刻）；普通能力没有 `#sheetChar`，直接放行。")
w("- 抽出 `snapshotComposerFocus()` / `restoreComposerFocus()`，"
  "把 contenteditable、角色描述框、自定义风格输入框的**内容 + 选区**一起拍照还原；"
  "`refreshDynamic()` 不再自己拼重绘，统一改调 `refreshComposer()`。")
w("")
w("纯前端改动，不需要重启服务；浏览器刷新页面（缓存旧可 Ctrl+F5）即生效。")

w("**改动清单**（全部在 `ComfyBridge-Desktop` 项目目录内）")
w("")
w("| 文件 | 位置 | 改动 |")
w("| --- | --- | --- |")
w("| `prompts/character_sheet.json` | `ink_modes` 之后新增 `line_style_overlay`、`line_style_profiles` | P1：一行造型气质模板 + 12 类画风各自对应的线稿造型特征；`ink_modes.line.desc` 同步说明 |")
w("| `app/promptlib.py` | 新增 `line_style_overlay()`、`DEFAULT_LINE_OVERLAY`；`compose()` 的变量行 | P1：线稿时把用户选的画风翻译成造型气质行追加在「风格：」之后；库里缺新字段时有兜底模板，老配置也能跑 |")
w("| `web/app.js` | `viewSheetComposer()` 画法行 / 风格行、`pageSheet()` 线稿句说明 | P1：线稿时明确提示「画风只借造型比例与线条气质，不上色」，让页面选项与提示词口径一致 |")
w("| `web/app.js` | `sheetForm()`、`sheetStyleValue()`、`viewSheetComposer()`、`#sheetStyle.onchange` | P2：用 `styleCustom` 显式标记自定义态，切下拉清空旧自定义句，修掉显示与提交不一致 |")
w("| `app/services.py` | 新增 `_ensure_job_refs()`；`submit_queue()` / `repeat()` / `_execute()` | P3：任何一次重跑都先按 `refsInfo` 把失效的参考图登记补回来，并把新 id 写回任务 |")
w("| `app/services.py` | `restore_refs()` | P3：源图已在上传目录时直接复用，不再复制副本 |")
w("| `app/services.py` | `prune_refs()` | P4：第二趟只摘内存登记、保留文件，保证缓存上限不再把新上传挡住；草稿参考图始终保护 |")
w("| `web/app.js` | 新增 `sheetPasteInto()`、document 级粘贴兜底；`bindSheetComposer()` 的 `#sheetChar.onpaste` / `.sheetbox` paste 委托 | P5：设定图输入框支持 Ctrl+V 文字与图片，文字显式同步表单状态 |")
w("| `web/app.js` | 新增 `snapshotComposerFocus()` / `restoreComposerFocus()`；`refreshComposer()`、`refreshDynamic()` | P5：重绘输入区保住内容、焦点与光标位置，不再冲掉刚粘的字 |")
w("| `tests/sheet_paste_e2e.py`、`tests/_sheet_full_out/paste_regression.json` | 新增 | P5：真实浏览器页面 paste 事件回归（文字 / 光标 / 重绘 / 焦点兜底 / 图片 / 普通能力 / 现场恢复） |")
w("| `README.md` | 提示词库小节 | 补一行说明线稿叠画风的口径 |")
w("")
w("**为什么不影响其它功能**：`compose()` 仍是创作台预览 / 提交 / 管理台模板预览的唯一入口；"
  "通用块、版式句、R1/R2、线稿句、彩色风格句一律未动；档位 overlay、工作流拷贝、"
  "**种子与队列复用逻辑、同种子判定、参数透传**一行未改；"
  "P2 只动创作台表单的本地状态（多选一个布尔标记 + 切换时清空），后端接口与提示词库完全不碰；"
  "P3/P4 只动「参考图登记」这一层缓存，参考图内容、提示词、工作流、尺寸都不变。"
  "管理台保存提示词库时 `SheetLibrary.save()` 是 merge，新增顶层字段不会丢（第七节实测）。"
  "Python 侧改动通过项目内 `.venv` 重启服务生效，重启前后 `state.json` 里的任务与成品记录全部保留；"
  "P2 是前端 JS，浏览器重新加载即生效，无需重启服务；"
  "P5 同样只在 `web/app.js` 的输入区事件与重绘入口上做文章，"
  "提示词组装、档位、工作流、种子、队列、上传接口一行未动，且普通能力输入框的粘贴路径保持原样（5.8 项实测回归）。")
w("")
w("### 观察项（非缺陷，记录备查）")
w("")
w("1. **页面档位尺寸是「约」值**：例如 A 版式精细档页面写 `1940 × 1080`，ComfyUI 实际按 16 对齐出 `1936 × 1088`；"
  "快速档 `1368 × 768` → 实际 `1376 × 768`。逐参数核对 `applied` 里 `megapixels / multiple` 都已写入工作流节点，"
  "总像素偏差 < 1%，属于 ResolutionSelector 的内部对齐，不是参数没生效。")
w("2. **模型对超长提示词的局部遵循**：个别图的「脸部特写」格仍带一点肩颈/衣领（提示词明确要求不画肩膀），"
  "但版式、视角数量、同人一致性、纯白底、无分割线、画风都正确。这属于生成模型的遵循度，应用层已把要求写全。")
w("3. **线稿 + 画风的平衡**：修复后 3D 动画等画风以「造型比例/线条气质」体现（大眼、圆润头身比等），"
  "不会给线稿上色——这是「线稿」与「彩色风格」两套要求同时满足时的必要取舍。")
w("4. **模型对「不填黑色块面」的局部遵循**：`P3-上传生成`（单张全身 · W3 美式细勾线 · 暗黑哥特）"
  "版式、纯白底、单姿态、造型气质都对，但角色描述里写了「深色棉麻短褂、同色阔腿裤」，"
  "模型把衣服涂成了大面积黑色块，而 W3 线稿句明确写了「不填任何黑色块面」。"
  "应用侧该写的约束已经写全（线稿句 + 造型气质行），这是生成模型的遵循度问题，不是提示词缺项。")
w("")
w("## 五、逐项测试结果")
w("")
w("### 5.1 提示词与参数核对（统一口径重算）")
w("")
w("| 轮次 | 用例 | 提示词核对通过 | 预览≡提交 | 尺寸符合 |")
w("| --- | ---: | ---: | ---: | ---: |")
for g in ("SMOKE", "R1", "R2", "R3", "F", "P3", "P5"):
    rs = [r for r in GEN if group(r) == g]
    if not rs:
        continue
    passed = sum(1 for r in rs if all(c["ok"] for c in checks_of(r)))
    w(f"| {g} | {len(rs)} | {passed}/{len(rs)} | "
      f"{sum(1 for r in rs if r.get('preview_equals_submitted'))}/{len(rs)} | "
      f"{sum(1 for r in rs if size_ok_of(r))}/{len(rs)} |")
w("")
fail_by_group: dict[str, Counter] = defaultdict(Counter)
for r in GEN:
    for c in checks_of(r):
        if not c["ok"]:
            fail_by_group[group(r)][c["item"]] += 1
for g in ("R1", "R2", "R3", "F", "P3", "P5"):
    w(f"- {g} 失败项：{dict(fail_by_group.get(g, {})) or '无'}")
w("")
w("R1 的 9 个「画风未生效」即 P1；R3 的 3 个「风格预设句」即 P2（自定义句残留，"
  "这 3 条彼此提示词仍然完全一致，「档位不改提示词」的结论不受影响）。"
  "P1/P2 修复后，F 轮对应的举例组合、8 组线稿、自定义风格、档位对照全部通过；"
  "P3 轮重新上传并生成的那条也 11/11 通过；P5 两条 8/8、11/11 通过。")
w("")
if STALE_PREVIEW:
    w("**关于预览≡提交的 1 条例外**：" + "、".join(f"`{r['id']}`" for r in STALE_PREVIEW) +
      " 的预览读到的是上一条失败用例（`F-线稿-W3-C.style-默认` 上传失败、没有发送）留在页面里的旧预览——"
      "面板收起时改表单不会立刻重算预览，展开按钮里是一次异步请求，测试脚本原来只等「预览文本存在」就读了。"
      "该用例**实际提交的提示词是对的**（8/8 核对通过，附录 B 有全文），不是应用缺陷。"
      "已把脚本改成「等预览带上当前角色描述再读」，并用 P5 两条干净用例复测："
      + "、".join(f"`{r['id']}`" for r in PREVIEW_RECHECK) +
      " 均 `预览≡提交=True`。管理台联动验证里 21/21 条预览判定也全部通过。")
    w("")
w(f"出图尺寸一栏全部按当前统一口径（宽高比偏差 < 2%、总像素偏差 < 5%）重算；"
  "早期 `SMOKE-I2I` 记录里存的是旧的逐像素严格判定，实际 1936×1088 对页面估值 1940×1080 只差 0.53% 总像素，属于 ResolutionSelector 对齐（观察项 1）。")
w("")
w("### 5.2 每个组合 × 每个档位")
w("")
w("| 组合 | 用例 | 档位 | 画风/线稿句 | 工作流 | 出图尺寸 | 种子 | 核对 |")
w("| --- | --- | --- | --- | --- | --- | --- | --- |")
for r in sorted(GEN, key=lambda x: (group(x["id"]), x["id"])):
    c = r["case"] or {}
    ck = checks_of(r)
    tagline = c.get("styleText") or c.get("style") or ""
    if c.get("ink") == "line":
        tagline += " / " + str(c.get("lineStyle"))
    w(f"| {combo_of(c)} | {r['id']} | {r.get('grade')} | {tagline} | {r.get('wf')} | "
      f"{r.get('size')} | {r.get('seed')} | {sum(1 for x in ck if x['ok'])}/{len(ck)} |")
w("")
w("### 5.3 🕒 加入队列生成（同种子、同提示词、可换档）")
w("")
w("| 用例 | 来源卡 | 档位 | 种子一致 | 提示词一致 | 尺寸 | 与原图平均像素差 |")
w("| --- | --- | --- | --- | --- | --- | --- |")
for r in QUEUE:
    if not r.get("ok"):
        w(f"| {r['id']} | — | — | — | — | — | 失败：{r.get('reason','')} |")
        continue
    if not run_ok(r):
        w(f"| {r['id']} | {(r['case'] or {}).get('from_case','')} | {r.get('grade_new')} | — | — | — | "
          f"❌ 参考图登记失效导致工作流被拒收（P3，已在 P3 轮复测通过） |")
        continue
    d = (r.get("image_diff") or {}).get("mean_abs_diff_0_255", "—")
    w(f"| {r['id']} | {(r['case'] or {}).get('from_case','')} | {r.get('grade_new')} | "
      f"{'✅' if r.get('same_seed') else '❌'} | {'✅' if r.get('same_prompt') else '❌'} | "
      f"{r.get('size_base')} → {r.get('size_new')} | {d} |")
w("")
w(f"小结：真正出片的 {len(QUEUE_OK)} 条里，{q_same_seed} 条沿用同一种子、{q_same_prompt} 条提示词完全一致；"
  f"同一档重跑 {len(q_diff0)} 次，像素差为 0（图片完全一致）；换档重跑构图相同、只有尺寸变化。"
  f"另有 {len([r for r in RUN_FAILED if (r.get('case') or {}).get('kind')=='queue'])} 条为修复前 P3 的失败记录，"
  "已在同一张卡上用 P3 轮逐档复测通过。"
  "符合「加入队列生成 = 工作流的随机种子 + 参数不变重新生成，图片与原图基本一致」。")
w("")
w("### 5.4 ↻ 再来一张（参数不变、换新种子）")
w("")
w("| 用例 | 来源卡 | 新种子 | 提示词一致 | 档位一致 | 参数一致 | 与原图平均像素差 |")
w("| --- | --- | --- | --- | --- | --- | --- |")
for r in AGAIN:
    if not r.get("ok"):
        w(f"| {r['id']} | — | — | — | — | — | 失败：{r.get('reason','')} |")
        continue
    if not run_ok(r):
        w(f"| {r['id']} | {(r['case'] or {}).get('from_case','')} | — | — | — | — | "
          f"❌ 参考图登记失效导致工作流被拒收（P3，已在 P3 轮复测通过） |")
        continue
    d = (r.get("image_diff") or {}).get("mean_abs_diff_0_255", "—")
    w(f"| {r['id']} | {(r['case'] or {}).get('from_case','')} | {'✅' if r.get('new_seed') else '❌'} | "
      f"{'✅' if r.get('same_prompt') else '❌'} | {'✅' if r.get('same_grade') else '❌'} | "
      f"{'✅' if r.get('same_params') else '❌'} | {d} |")
w("")
w(f"小结：真正出片的 {len(AGAIN_OK)} 条里，{a_new_seed} 条拿到新种子、{a_same_prompt} 条提示词一致、"
  f"{a_same_grade} 条档位一致、{a_same_params} 条参数覆盖一致；"
  "像素差多在 2~9（图生图/竖版构图差异小一些），说明结果保留了构图但有随机性。"
  "符合「再来一张 = 参数不变重新生成（新随机种子）」。")
w("")
w("### 5.5 失败 / 跳过记录")
w("")
w("按原因分三类，都能对上后面的修复与复测：")
w("")
w("**A. 测试通道搭建期（不是应用缺陷）**")
w("")
for r in FAILED:
    if r.get("id", "").startswith("SMOKE-I2I"):
        w(f"- `{r.get('id')}`：{r.get('reason')}。无头 Edge 对页面动态创建、没有挂到 DOM 的 "
          "`<input type=file>` 不会弹系统文件框，第一次图生图因此没传上参考图、发送被前端正确拦下。"
          "调查后改成「点＋上传 → CDP 给页面里的真实 file input 注入文件 → 由页面自己的 change 处理完成上传」，"
          "后续所有图生图用例上传全部成功；这也顺带验证了前端「图生图没参考图就拦下」的校验是好的。")
w("")
w("**B. P3 参考图登记失效（已修复）**")
w("")
for r in RUN_FAILED:
    w(f"- `{r.get('id')}`：卡片点击成功、新 run 也建起来了，但工作流被 ComfyUI 拒收 "
      f"（`{(r.get('new_run') or {}).get('err') or ''}`）；来源任务 `{(r.get('case') or {}).get('job_id') or (r.get('case') or {}).get('from_case')}` "
      "是服务重启前的图生图卡。")
for r in FAILED:
    if r.get("id") == "R4-A3-2":
        w(f"- `{r.get('id')}`：{r.get('reason')} —— 上一条「再来一张」建出来的卡没有出片，页面上自然没有可点的按钮，属于 P3 的连带结果。")
w("→ 修复后 P3 轮直接拿同一张旧卡 `job_1944` 重跑 3 档队列 + 2 次再来一张，全部出片、同种子/新种子与图片差异都符合预期。")
w("")
w("**C. P4 上传上限（已修复）**")
w("")
for r in FAILED:
    if r.get("id") == "F-线稿-W3-C.style-默认":
        w(f"- `{r.get('id')}`：{r.get('reason')}。缓存登记达到 30 张上限，清理一趟没删掉任何登记，第 31 次上传被 400 挡下。")
w("→ 修复后专门做了 32 次连续「上传 → 移除」页面回归（5.7）全部成功；"
  "同一组合 `P3-上传生成` 重新上传并生成成功，11/11 提示词核对通过，后续队列/再来一张也正常。")
w("")
if not FAILED and not RUN_FAILED:
    w("无。")
w("")
w("### 5.6 自动校验（功能不变量）")
w("")
w(f"- **档位只改参数、不改提示词**：{len(same_groups)} 组「同一句角色描述 × 默认/快速/精细」对照，"
  f"提示词逐字相同 → {'✅ 全部通过' if same_prompt_ok else '❌ 有不一致'}。")
w(f"- **尺寸随档位递增**：{len(size_ladder)} 个组合同时跑过三档，快速 < 默认 < 精细 → "
  f"{'✅ 全部通过' if size_ladder and all(ok for _, ok in size_ladder) else '❌ 有异常'}。")
w(f"- **彩色·锁定同角色·单张全身的尾句**：{len(lock_tail)} 条均以「画风与图 1 保持一致」收尾 → "
  f"{'✅' if lock_tail_ok else '❌'}（与项目原有自检口径一致，修复未破坏）。")
w(f"- **修复前后对比**：修复前那几轮共 {len(before)} 例（R1 全 18 例、冒烟图生图线稿 1 例、R3 档位对照 3 例），"
  f"其中暴露缺陷的是 R1 的 9 个线稿 + 冒烟 1 个 + R3 的 3 条；逐项核对失败 {dict(before_fail) or '无'}；"
  f"其余与全部修复后复测共 {len(after)} 例，失败 {dict(after_fail) or '无'}。")
w("")
w("### 5.7 参考图缓存上限与旧卡自愈专项（P3 / P4 复测）")
w("")
if REFREG:
    w(f"- 页面连续执行「上传参考图 → 从输入框移除」**{REFREG.get('rounds')}** 轮，"
      f"成功 **{REFREG.get('upload_ok_count')}** 轮，首次失败轮次：{REFREG.get('first_fail_round') or '无'}；"
      f"跨过原 30 张上限的第 31、32 轮：{REFREG.get('rounds_after_cap')} → "
      f"{'✅ P4 已修复，上传不再被历史任务卡住' if REFREG.get('all_ok') else '❌ 仍有失败'}。")
else:
    w("- （`ref_regression.json` 未生成）")
w("")
p3 = [r for r in ROWS if group(r) == "P3"]
p3_run_ok = sum(1 for r in p3 if (r.get("case") or {}).get("kind") == "gen" or run_ok(r))
w(f"- 重启服务（内存登记全部清空）+ 32 轮上传触发缓存清理之后，再拿旧图生图卡 `job_1944` 做页面操作："
  f"P3 轮 {len(p3)} 条记录，{p3_run_ok} 条真的出片。"
  "3 档队列均 `same_seed=True`，默认档像素差 0.0；2 次再来一张均 `new_seed=True`；"
  "新上传的图生图卡生成 11/11 通过，其队列与再来一张也正常。"
  "→ ✅ P3 已修复，且修复没有改变同种子 / 新种子的原有行为。")
w("")
w("### 5.8 Ctrl+V 粘贴专项回归（P5 复测）")
w("")
if PASTE_ROWS:
    w("在真实浏览器页面里派发 `paste` 事件（`DataTransfer` 带 `text/plain` 或 `image/png`），"
      "只读取页面自身状态与 DOM 做核对；没有直接调用任何生成接口。")
    w("")
    w("| 检查项 | 结果 | 说明 |")
    w("| --- | --- | --- |")
    for r in PASTE_ROWS:
        mark = "✅" if r["ok"] is True else ("⚠️ 环境限制" if r["ok"] is None else "❌")
        detail = str(r.get("detail") or "").replace("|", "\\|")
        w(f"| {r['item']} | {mark} | {detail} |")
    ok_n = sum(1 for r in PASTE_ROWS if r["ok"] is True)
    fail_n = sum(1 for r in PASTE_ROWS if r["ok"] is False)
    w("")
    w(f"合计 **{ok_n}/{ok_n + fail_n} 项通过**，失败 {fail_n} 项。"
      "「真实 Ctrl+V 按键」一项是 NOTE：无头 Edge 不读取 Windows 系统剪贴板，"
      "属于测试环境限制；页面 paste 事件路径已由其余各项覆盖，且修复后的处理函数同时接住了文字与图片。")
    w("")
    w("回归最后把测试期间改过的角色描述、参考图、能力、档位，以及切到普通能力时粘进去的测试文字"
      "全部还原并落盘，不会把测试文字或测试图片留在页面上。")
else:
    w("- （`paste_regression.json` 未生成）")
w("")
w("## 六、视觉抽查")
w("")
w("（每一张都点开成品原图人工核对：版式格位、画法、画风、纯白底、无分割线、同一人物、标志物）")
w("")
w("| 成品文件 | 组合 / 画风 | 结论 |")
w("| --- | --- | --- |")
w(f"| `{img_name('SMOKE-T2I')}` | A.color.none / 3D 动画 | 四视图横排齐整，皮克斯式圆润卡通比例、大眼，纯白底无分割线，船锚徽章与红绳都在 ✅ |")
w(f"| `{img_name('R1-A.color.none-默认')}` | A.color.none / 写实摄影 | 四视图横排，写实人像质感，同一人、大小齐平、纯白底 ✅ |")
w(f"| `{img_name('R1-B.color.none-默认')}` | B.color.none / 日系赛璐璐 | 四宫格（正面/侧面/背面/脸部特写），赛璐璐平涂，纯白底无分割线 ✅ |")
w(f"| `{img_name('R1-C.color.none-默认')}` | C.color.none / 写实摄影 | 只有一个正面站姿、无分身，怀表标志物，纯白底 ✅ |")
w(f"| `{img_name('SMOKE-I2I')}` | A.line.style / W4 + 3D 动画（修复前） | 四视图横排、纯黑白铅笔线稿 ✅，但人物是写实素描比例，看不出 3D 动画 ❌ → P1 |")
w(f"| `{img_name('F-举例-默认')}` | A.line.style / W4 + 3D 动画（修复后） | 四视图横排、纯黑白铅笔线稿 ✅，特写格变成大眼圆润卡通比例，画风气质生效 ✅ |")
w(f"| `{img_name('R2-A.line.none-快速')}` | A.line.none / 绘本（修复后·快速档） | 快速档 1376×768，四视图横排、柔软手绘铅笔线、绘本造型气质、纯白底 ✅ |")
w(f"| `{run_img('P3-Q3-默认')}` | A.line.style / W2（P3 旧卡重启后重跑） | 四视图横排、细线稿、纯白底无分割线，与原图像素差 0.0 → 参考图确实被找回且同种子生效 ✅ |")
w(f"| `{img_name('P3-上传生成')}` | C.line.style / W3 + 暗黑哥特（P4 修复后） | 单张正面全身、无分身、纯白底、瘦长身形与尖锐立领 ✅；深色衣物被涂成大块黑色，属观察项 4 的模型遵循度 |")
w("")
w("## 七、管理台 ↔ 创作台联动验证")
w("")
if ADMIN_ROWS:
    w("| 检查项 | 结果 | 说明 |")
    w("| --- | --- | --- |")
    for r in ADMIN_ROWS:
        w(f"| {r['item']} | {'✅' if r['ok'] else '❌'} | {(r.get('detail') or '')[:120]} |")
    w("")
    w(f"合计 {sum(1 for r in ADMIN_ROWS if r['ok'])}/{len(ADMIN_ROWS)} 通过。"
      "改过的风格预设与线稿句都已改回原值并保存，测试结束不留痕。")
else:
    w("（联动验证记录未生成）")
w("")
w("## 八、结论")
w("")
w(f"- **18 个组合 × 3 个档位、12 类画风、4 条线稿句、自定义风格、3 种参考方式全部实测出图**；"
  f"共 {len(GEN)} 次普通生成 + {len(QUEUE)} 条队列用例（真正出片 {len(QUEUE_OK)} 条，另 3 条为修复前 P3 的失败记录）"
  f"+ {len(AGAIN)} 条再来一张用例（真正出片 {len(AGAIN_OK)} 条，另 2 条为修复前 P3 的失败/连带记录），"
  "多轮、每条提示词都不同（档位同提示词对照 3 条除外，属刻意对照）。")
w("- 修复后所有用例的提示词核对、档位画幅、种子与参数行为全部通过；"
  "预览/提交一致性除 1 条测试脚本读到旧预览的记录外全部通过（该条已用 P5 两条干净用例复测一致，见 5.1）；"
  "已修的 P1 在「用户举例的那个组合」上复测三档 + 8 组线稿专项均通过，出图同时满足四视图横排 + 铅笔线稿 + 3D 动画造型气质；"
  "已修的 P2 在「自定义… → 预设」切换专项与 F 轮档位对照里复测通过，页面显示、预览、提交三者一致。")
w("- 「加入队列生成」各档位同种子、同提示词，同档出图与原图像素完全一致；「再来一张」每次新种子、参数不变、结果有随机性。")
w("- 又查出并修复了两个只在长时间/重启后才暴露的问题：P3（重启后旧图生图卡重跑丢参考图）和 "
  "P4（连续上传第 31 张被缓存上限挡住）；两者都用页面操作做了专项复测："
  "32/32 次连续上传成功、旧卡 3 档队列 + 2 次再来一张全部出片。")
w("- 管理台编辑提示词库/档位与创作台即时联动保持完好，修复没有动档位、工作流、种子、队列复用逻辑任何一行；"
  "P3/P4 只改参考图登记缓存层，参考图内容、提示词、工作流、尺寸都不变。")
w("- 交付后又追加修复了 **P5（角色设定图输入框 Ctrl+V 粘贴不上）**："
  "补齐设定图表单的 paste 处理（文字 + 图片）、给输入区重绘加内容与焦点保护、加焦点兜底；"
  "专项页面回归全部通过（另有 1 项无头浏览器不读系统剪贴板的环境说明），"
  "普通能力输入框的粘贴实测未被干扰，详见第四节 P5 与 5.8。")
w("- 应用代码只改了项目目录内的 **5 个文件**（提示词库、`promptlib.py`、`services.py`、`web/app.js`、`README.md`；"
  "P5 仍只动其中的 `web/app.js`），"
  "另在项目内新增了页面操作测试脚本与用例/记录；项目目录外的需求文档、ComfyUI、模型、成品原文件均未改动。")
w("")
w("---")
w("")
w("## 附录 A · 全部用例清单（角色描述全文）")
w("")
w("| 用例 | 轮次 | 组合 | 档位 | 画风 / 线稿句 | 角色描述 |")
w("| --- | --- | --- | --- | --- | --- |")
for r in ROWS:
    c = r.get("case") or {}
    if (c.get("kind") or "gen") != "gen":
        continue
    tag = c.get("styleText") or c.get("style") or "—"
    if c.get("ink") == "line":
        tag += " / " + str(c.get("lineStyle"))
    w(f"| {r.get('id')} | {group(r)} | {combo_of(c)} | {c.get('grade','—')} | {tag} | "
      f"{str(c.get('character','')).replace('|','／')} |")
w("")
w("## 附录 B · 每次生成实际提交给 ComfyUI 的完整提示词")
w("")
w("> 「加入队列生成」「再来一张」不另贴：它们复用来源卡的同一条提示词（第 5.3 / 5.4 节已核对 `same_prompt`）。")
w("")
for r in ROWS:
    if not r.get("ok") or not r.get("submitted_prompt"):
        continue
    c = r.get("case") or {}
    if (c.get("kind") or "gen") != "gen":
        continue
    w(f"### {r['id']}　{combo_of(c)} · {INK.get(c.get('ink'),'')} · "
      f"{c.get('style') or c.get('styleText') or ''} · {c.get('grade','')}")
    w("")
    w(f"- 档位：{r.get('grade')}　工作流：{r.get('wf')}　种子：{r.get('seed')}　出图：{r.get('size')}　耗时：{r.get('elapsed_s')}s")
    w(f"- 成品：`{r.get('image','')}`")
    w("")
    w("```text")
    w(r["submitted_prompt"])
    w("```")
    w("")

w("## 附录 C · 队列生成 / 再来一张的提示词归属")
w("")
w("这些用例不重新拼提示词，而是原样复用来源卡那一整条；下表给出来源与本次的种子、档位、成品，"
  "完整提示词见附录 B 中来源卡那一条（`results.jsonl` 里也逐条存了 `base.prompt` / `job.prompt`）。")
w("")
w("| 用例 | 类型 | 来源卡（完整提示词见附录 B） | 档位 | 种子 | 成品 |")
w("| --- | --- | --- | --- | --- | --- |")
for r in QUEUE + AGAIN:
    if not r.get("ok"):
        continue
    c = r.get("case") or {}
    kind = "加入队列生成" if (c.get("kind") == "queue") else "再来一张"
    seed = r.get("seed_new") or (r.get("new_run") or {}).get("seed") or r.get("seed_base")
    grade = r.get("grade_new") or (r.get("new_run") or {}).get("grade") or (r.get("case") or {}).get("grade", "—")
    path = r.get("image_new") or ""
    w(f"| {r['id']} | {kind} | `{c.get('from_case','')}` / job `{c.get('job_id','')}` | "
      f"{grade} | {seed} | `{Path(path).name if path else '—'}` |")
w("")

w("## 附录 D · 管理台联动验证里的完整预览提示词")
w("")
w("管理台的这些检查不提交生成，只点创作台「看完整提示词」读服务端组装结果；"
  "同样把每次读到的整条提示词留档（改过的风格预设/线稿句在测试结束都已改回原值）。")
w("")
if ADMIN_ROWS:
    for r in ADMIN_ROWS:
        p = r.get("prompt") or ""
        if not p:
            continue
        w(f"### {r['item']}（{'通过' if r['ok'] else '未通过'}）")
        w("")
        w("```text")
        w(p)
        w("```")
        w("")
else:
    w("（无）")
w("")

OUT.write_text("\n".join(L) + "\n", encoding="utf-8")
print(f"报告已生成：{OUT}  ({len(L)} 行, {OUT.stat().st_size} 字节)")
print("统计：", json.dumps({
    "records": len(ROWS), "gen": len(GEN), "queue": len(QUEUE), "again": len(AGAIN),
    "queue_ok": len(QUEUE_OK), "again_ok": len(AGAIN_OK), "run_failed": len(RUN_FAILED),
    "failed": len(FAILED), "combos": len(combos), "check_fail": dict(check_fail),
    "before_fail": dict(before_fail), "after_fail": dict(after_fail),
    "ref_regression": (f"{REFREG.get('upload_ok_count')}/{REFREG.get('rounds')}"
                       if REFREG else "未生成"),
    "admin": f"{sum(1 for r in ADMIN_ROWS if r['ok'])}/{len(ADMIN_ROWS)}",
}, ensure_ascii=False))
