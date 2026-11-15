"""把 1.02 文档装配成「和当前画面一致」的版本。

做的事：
1. 整章替换第 03 章（功能规格）与第 08 章（手机端）——这两章的旧内容是按原型写的，
   和现在的程序差得最多，逐条改不如重写。
2. 内嵌真实界面截图（_reference/docs/web/*.jpg → base64，占位符 {{img:name}}）。
3. 补 CSS：figure img / figcaption / .phone 竖屏图。
4. 修导航：第 03 章从 5 节变 7 节，手机端那节改名。
5. 改掉保留章节里两处已经不存在的功能（「复制种子」）。
6. 重写「1.02 改了什么」，补上程序侧这一轮的真实改动。

用法：.venv/Scripts/python.exe tools/build_doc_102.py
"""
from __future__ import annotations

import base64
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT.parent / "ComfyBridge-需求与功能文档-1.02.html"
SRC_DIR = ROOT / "_reference" / "doc_src"
IMG_DIR = ROOT / "_reference" / "docs" / "web"
BACKUP = SRC_DIR / "_backup_1.02.html"

CSS_EXTRA = """
/* ---------- 1.02 新增：真实界面截图 ---------- */
figure.shot{max-width:1000px}
figure.phone{max-width:300px;margin-left:auto;margin-right:auto}
figure img{width:100%;height:auto;display:block;border-radius:10px;border:1px solid var(--line);
  background:#fff;box-shadow:0 3px 18px rgba(16,24,40,.09)}
figure.phone img{border-radius:16px;border:1px solid #d5dbe6;box-shadow:0 6px 26px rgba(16,24,40,.14)}
figcaption{margin-top:8px;font-size:12.5px;line-height:1.7;color:var(--ink3);text-align:center}
figure.phone figcaption{font-size:12px}
.tag{display:inline-block;padding:1px 7px;border-radius:20px;background:#f1f4f9;border:1px solid var(--line);
  font-size:11.5px;color:var(--ink2);white-space:nowrap}
.chip{display:inline-block;padding:1px 8px;border-radius:20px;font-size:11.5px;font-weight:600}
.chip.green{background:#eaffe9;border:1px solid #b7e3cc;color:#0f6b45}
.chip.gray{background:#f3f5f9;border:1px solid var(--line);color:var(--ink3)}
code{background:#f4f6fa;border:1px solid var(--line);border-radius:4px;padding:0 4px;
  font:13px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;color:#2b3444}
table code{font-size:12.2px}
"""

NAV_CH03 = (
    '<div class="chap"><a href="#ch-03">03　功能规格（以当前画面为准）</a><div class="subs">'
    '<a href="#sec-12">一、创作台：图片生成</a>'
    '<a href="#sec-12b">二、创作台：角色设定图</a>'
    '<a href="#sec-12c">三、生成历史</a>'
    '<a href="#sec-13">四、管理台</a>'
    '<a href="#sec-14">五、能力库与状态</a>'
    '<a href="#sec-15">六、外部接口（给 AI 用）</a>'
    '<a href="#sec-16">七、技能（给 AI 的说明书）</a></div></div>'
)

NAV_CH08 = (
    '<div class="chap"><a href="#ch-08">08　手机端（同一套功能，另一种摆法）</a><div class="subs">'
    '<a href="#sec-44">一、原则：只是摆法不同</a>'
    '<a href="#sec-45">二、屏幕上的四块</a>'
    '<a href="#sec-46">三、逐条验收</a>'
    '<a href="#sec-47">四、手机上明确不做的事</a></div></div>'
)

CHANGELOG = """<h2 style="border-top:none;padding-top:6px">1.02 改了什么</h2>
<p>1.02 做了一件事：<b>把文档拉回和程序一致</b>。程序这一版也真的改了不少界面，所以两边一起改。下面两张表，一张说程序，一张说文档。</p>

<h3>一、程序这一版动了哪些界面</h3>
<table>
<thead><tr><th>#</th><th>位置</th><th>改成什么了</th></tr></thead>
<tbody>
<tr><td>1</td><td>管理台 · 能力与工作流</td><td>能力卡片上的<b>复制（⧉）和删除（✕）两个按钮整个去掉</b>。能力清单来自 <code>config.yaml</code>，页面不给增删</td></tr>
<tr><td>2</td><td>管理台 · 左侧菜单</td><td>「2 档位表」下面<b>加了子菜单</b>：🖼️ 图片生成 / 👤 角色设定图，点一下直接跳到对应能力的档位表</td></tr>
<tr><td>3</td><td>管理台 · 能力与工作流</td><td><b>去掉了「重读配置」</b>。要重读档位就进对应能力的档位表，用那条 <code>↻ 重读档位</code></td></tr>
<tr><td>4</td><td>管理台 · 档位表</td><td>图片生成的档位表也换成角色设定图那一套<b>白框切换带</b>：当前子工作流 + 档位 + 画幅，右边 <code>↻ 重读档位</code> 和 <code>去创作台试这个组合</code></td></tr>
<tr><td>5</td><td>全站文案</td><td>母版档的名字从「母版」统一改成<b>「默认」</b>。用户自建同名档位会被拦下</td></tr>
<tr><td>6</td><td>手机端 · 顶栏</td><td>菜单按钮移到<b>最左</b>，ComfyUI 状态<b>移到正中</b>；状态只报「已连接 / 未连接 + 空闲 / 生成中」，不再写长句</td></tr>
<tr><td>7</td><td>手机端 · 左抽屉</td><td>抽屉<b>占满整个竖屏</b>（高度写死 <code>100dvh</code>）。以前只按内容高度撑开，下半屏是空的，看着像菜单缺了一块</td></tr>
<tr><td>8</td><td>手机端 · 右抽屉</td><td>从右侧滑出，占屏宽约 1/3；「全选」「批量删除」换成<b>图标按钮</b>，批量删除带红色数字角标</td></tr>
<tr><td>9</td><td>手机端 · 输入区</td><td>最高<b>半个屏幕</b>，超出的部分在中间那块内容区里滚，底栏（上传 / 完整提示词 / 发送）永远露在外面</td></tr>
<tr><td>10</td><td>手机端 · 管理台</td><td>菜单也改成左抽屉并占满竖屏；能力卡片一行到底（开关和「配置」在右）；档位名不再被挤成竖排字；「保存」靠右；「已实测组合」表格竖排成卡片</td></tr>
</tbody>
</table>

<h3>二、文档这一版动了什么</h3>
<table>
<thead><tr><th>类别</th><th>位置</th><th>具体</th></tr></thead>
<tbody>
<tr><td rowspan="2"><b>重写</b></td><td>第 03 章</td><td>按「屏幕上有什么」重写：创作台（图片生成）→ 创作台（角色设定图）→ 生成历史 → 管理台五页，逐控件交代，配 14 张真实截图</td></tr>
<tr><td>第 08 章</td><td>按手机端真实样式重写：顶栏三段布局、两个抽屉、输入区半屏限高、16 条验收</td></tr>
<tr><td rowspan="5"><b>删掉</b></td><td>创作台</td><td>「复制种子」——界面上没有这个按钮，复现改用卡底的「🕒 加入队列生成」</td></tr>
<tr><td>创作台</td><td>「降一档重试」——失败卡片上只有「重试一次」</td></tr>
<tr><td>能力与工作流</td><td>「复制能力」「删除能力」「重读配置」三处</td></tr>
<tr><td>档位表</td><td>「四步」改成实际的<b>五步</b>：① 工作流文件 ② 参数节点 ③ 不用设的节点 ④ 新增档位 ⑤ 已有档位</td></tr>
<tr><td>界面示意图</td><td>8 张手绘的页面示意图全部换成程序真实截图，不再靠"想象画一张"</td></tr>
<tr><td rowspan="4"><b>补上</b></td><td>创作台</td><td>任务卡三个动作按钮的确切分工、<code>@</code> 引用与 <code>&lt;image1&gt;</code> 令牌规则、右栏批量删除只移出会话</td></tr>
<tr><td>角色设定图</td><td>五行输入区逐行说明、18 个组合怎么算出来的、提示词预览走同一个函数</td></tr>
<tr><td>生成历史</td><td>两个筛选、多选、批量下载打 zip、删除与「移出会话」的区别</td></tr>
<tr><td>管理台</td><td>档位表子菜单、设定图模板六块（3 版式 / 6 通用块 / 4 线稿句 / 12 风格 / 15 条实测组合）、服务设置四块字段、自检两项</td></tr>
<tr><td><b>改对</b></td><td>管理台</td><td>页数统一成实际 <b>5 页</b>；配置文件说明里「日志级别」不存在，换成配置版本号</td></tr>
</tbody>
</table>
<blockquote>
<p>原型文件（<code>ComfyBridge-交互原型-单文件.html</code>）这一版没有动，仍然停留在 1.01 的样子。要看当前界面以程序为准，以本文档为准。</p>
</blockquote>
"""


def rep(text: str, old: str, new: str, count: int = 1) -> str:
    """替换并断言命中次数，防止改了源码却静默没生效。"""
    got = text.count(old)
    if got != count:
        raise SystemExit(f"断言失败：期望命中 {count} 次，实际 {got} 次 → {old[:70]!r}")
    return text.replace(old, new)


def replace_chapter(html: str, chap_id: str, nxt_id: str, fragment: str) -> str:
    """把整章（<section id="chap_id"> 到下一章之前）换成新内容。"""
    a = html.find(f'<section id="{chap_id}"')
    b = html.find(f'<section id="{nxt_id}"')
    if a < 0 or b < 0 or b <= a:
        raise SystemExit(f"找不到章节边界：{chap_id} → {nxt_id}")
    return html[:a] + fragment.strip() + "\n\n" + html[b:]


def inline_images(html: str) -> str:
    """{{img:name}} → data URI。缺图直接报错，不要静默留占位符。"""
    used: set[str] = set()

    def one(m: re.Match[str]) -> str:
        name = m.group(1)
        src = IMG_DIR / f"{name}.jpg"
        if not src.exists():
            raise SystemExit(f"缺图：{src}")
        used.add(name)
        b64 = base64.b64encode(src.read_bytes()).decode("ascii")
        # 不加 loading="lazy"：懒加载会让「打印/导出 PDF」时还留在视口外的图是空白，
        # 19 张图一次性解码对桌面浏览器没压力，换来文档拿去打印也不缺图。
        return f'<img src="data:image/jpeg;base64,{b64}" alt="{name}">'

    out = re.sub(r"\{\{img:([a-z0-9_]+)\}\}", one, html)
    if "{{img:" in out:
        raise SystemExit("还有没替换掉的图片占位符")
    print(f"内嵌截图 {len(used)} 张：{', '.join(sorted(used))}")
    return out


def main() -> int:
    if not DOC.exists():
        raise SystemExit(f"找不到源文档：{DOC}")
    # 永远从「改之前」的原始 1.02 重建，这样脚本可以反复跑、结果一致；
    # 直接拿已经改过的产物当输入，rep() 的命中次数断言就对不上了。
    if not BACKUP.exists():
        shutil.copy2(DOC, BACKUP)
        print(f"已备份原始 1.02 → {BACKUP.name}")
    html = BACKUP.read_text(encoding="utf-8")

    # 1) 整章替换。顺手把 <p><figure>…</figure></p> 拆开：figure 是块级元素，
    # 套在 p 里浏览器会自己把 p 断开，留一堆空段落标签，不如直接写成兄弟节点。
    ch03 = (SRC_DIR / "ch03.html").read_text(encoding="utf-8")
    ch08 = (SRC_DIR / "ch08.html").read_text(encoding="utf-8")
    ch03 = ch03.replace("<p><figure", "<figure").replace("</figure></p>", "</figure>")
    ch08 = ch08.replace("<p><figure", "<figure").replace("</figure></p>", "</figure>")
    html = replace_chapter(html, "ch-03", "ch-04", ch03)
    html = replace_chapter(html, "ch-08", "ch-faq", ch08)

    # 2) 导航
    html = rep(
        html,
        '<div class="chap"><a href="#ch-03">03　功能规格</a><div class="subs"><a href="#sec-12">一、创作台（日常使用的那一页）</a>'
        '<a href="#sec-13">二、管理台（配置那几页）</a><a href="#sec-14">三、能力库与状态</a>'
        '<a href="#sec-15">四、外部接口（给 AI 用）</a><a href="#sec-16">五、技能（给 AI 的说明书）</a></div></div>',
        NAV_CH03,
    )
    html = rep(
        html,
        '<div class="chap"><a href="#ch-08">08　手机端（同一套功能，另一种摆法）</a><div class="subs"><a href="#sec-44">一、原则：只是摆法不同</a>'
        '<a href="#sec-45">二、屏幕上的四块</a><a href="#sec-46">三、11 条验收</a><a href="#sec-47">四、手机上明确不做的事</a></div></div>',
        NAV_CH08,
    )

    # 3) 版本说明整块替换（从「1.02 相对 1.01 改了什么」到第 01 章之前）
    a = html.find('<h2 style="border-top:none;padding-top:6px">1.02 相对 1.01 改了什么</h2>')
    b = html.find('<section id="ch-01"')
    if a < 0 or b <= a:
        raise SystemExit("找不到版本说明块的边界")
    html = html[:a] + CHANGELOG + "\n\n" + html[b:]

    # 4) 保留章节里两处已经不存在的功能
    html = rep(
        html,
        '"再来一张"和"复制种子"',
        '"再来一张"和"加入队列生成"',
    )
    html = rep(
        html,
        '要复现就在结果卡上点"复制种子"',
        '要复现就点卡底的「🕒 加入队列生成」',
    )
    # 第 06 章引用的「文生图.sdxl.turbo」是原型里的工作流，现在程序里没有这个文件。
    # 换成当前真实存在的两个子工作流做对比（参数节点数确实不一样）。
    html = rep(
        html,
        "演示原型里「图片生成」下面就有这么一个工作流（文生图.sdxl.turbo），可以直接点开看差别。",
        "现在程序里就能看到这种差别：管理台「档位表」→「图片生成」，切上方的"
        "「文生图 / 参考生图」两个子功能，② 参数节点的数量完全不同——同一个能力下，"
        "不同子工作流该勾的节点本来就不一样。",
    )

    # 5) 封面与标题带版本号
    html = rep(html, "<title>ComfyBridge · 需求与功能文档 v2</title>",
               "<title>ComfyBridge · 需求与功能文档 1.02</title>")

    # 5b) 「这份文档怎么读」里的指向：第 03 章重排了，档位表从 §2.2 挪到 §4.3
    html = rep(
        html,
        "<tr><td>页面长什么样、能点哪些</td><td>第 03 章</td><td>创作台 1 页 + 管理台 5 页，逐页画出来</td></tr>",
        "<tr><td>页面长什么样、能点哪些</td><td>第 03 章</td><td>创作台（图片生成 / 角色设定图）+ 生成历史 + 管理台 5 页，"
        "逐页配真实截图</td></tr>",
    )
    html = rep(
        html,
        "<tr><td>一个能力怎么配上一个工作流</td><td>第 03 章 §2.2</td>",
        "<tr><td>一个能力怎么配上一个工作流</td><td>第 03 章 §4.3</td>",
    )

    # 6) CSS（幂等：已经加过就不再重复）
    if "1.02 新增：真实界面截图" not in html:
        html = rep(html, "</style>", CSS_EXTRA + "</style>")

    # 7) 截图内嵌
    html = inline_images(html)

    DOC.write_text(html, encoding="utf-8")
    n_img = html.count("<img ")
    print(f"完成：{DOC.name}  {len(html)/1024:.0f} KB（字符）/ {DOC.stat().st_size/1024/1024:.2f} MB（字节）")
    print(f"图片 {n_img} 张，figure {html.count('<figure')} 个，h2 {html.count('<h2')} 个")
    return 0


if __name__ == "__main__":
    sys.exit(main())
