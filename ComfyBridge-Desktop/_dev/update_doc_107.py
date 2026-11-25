"""把视频生成扩展（含参考模式）写进需求文档，升级到 1.07。

只做精确字符串替换 + 断言，避免误改 6.5MB 文档里的其他内容（含大量 base64 截图）。
"""
from __future__ import annotations

from pathlib import Path

DOC = Path(r"D:\workspace\WorkBuddyProject\ComfyBridge-需求与功能文档-1.06.html")


def replace_once(s: str, old: str, new: str, label: str) -> str:
    n = s.count(old)
    assert n == 1, f"{label}: 期望命中 1 次，实际 {n} 次"
    return s.replace(old, new, 1)


CHANGELOG_107 = """<h2 style="border-top:none;padding-top:6px">1.07 改了什么</h2>
<p>1.07 只新增了一件事：<b>视频生成扩展</b>（能力 id <code>video.ext</code>）。它是第 9 个生成能力，放在左栏「视频生成」下方，界面仿「参考生视频」，但机制完全不同：<b>一档 = 一份独立的工作流文件</b>——切档就是换整条工作流，不是改几个参数。本版又给它加了「参考模式」单选（首尾帧生成 / 参考生成），按<b>「档位 × 模式」</b>切换 UNET / LoRA 模型；20 采的加速链没有 LoRA，只换 UNET。</p>

<h3>一、程序这一版动了哪些界面</h3>
<table>
<thead><tr><th>#</th><th>位置</th><th>改成什么了</th></tr></thead>
<tbody>
<tr><td>1</td><td>左栏「视频」分组</td><td>「视频生成」下方新增「<b>视频生成扩展</b>」入口；它是独立能力，不影响原视频生成的四个子功能</td></tr>
<tr><td>2</td><td>创作台 · 主区</td><td>标题「视频生成扩展」，副标题「多参极速生视频：图 / 视频 / 音频参考 + 宽高比，每一档为独立工作流」，下方是可聚焦的描述框</td></tr>
<tr><td>3</td><td>创作台 · 子功能条</td><td>「多参极速生视频」子功能按钮；配置里以后加子功能，这里自动多一个按钮（单选切换）</td></tr>
<tr><td>4</td><td>创作台 · 档位</td><td>五个固定档位：<span class="tag">4采480P</span> <span class="tag">8采480P</span> <span class="tag">8采768P</span> <span class="tag">20采480P</span> <span class="tag">20采768P</span>；默认 <b>8采480P</b>。每档对应一份独立工作流文件，切档沿用同一提示词 / 种子 / 素材 / 宽高比 / 模式</td></tr>
<tr><td>5</td><td>创作台 · 宽高比右侧</td><td>新增「<b>参考模式</b>」单选：<span class="tag">首尾帧生成</span> / <span class="tag">参考生成</span>，默认首尾帧生成。切换只换模型参数，档位工作流文件不变</td></tr>
<tr><td>6</td><td>创作台 · 参考与时长</td><td>最多 <b>6 张图 / 1 段视频 / 3 段音频</b>，按实际上传数量动态连线，未用的槽不接；时长 4～15 秒（默认 5.20），行尾实时显示「档位 · 参考模式 · 宽高比 · 独立工作流」</td></tr>
<tr><td>7</td><td>服务端 · 模型注入</td><td>按「<b>档位 × 参考模式</b>」在内存里换生效链的 <code>unet_name</code> / <code>lora_name</code>，只改真正接到采样器的那条链；备用链与加速链结构不动，20 采不碰 LoRA。<b>磁盘上的工作流文件一个字节都不改</b></td></tr>
<tr><td>8</td><td>管理台 · 档位表</td><td>新增只读档位表：黄底、只显示「① 工作流文件」与「② 已有挡位」两块，没有参数节点 / 不用设的节点 / 新增挡位；顶部有子功能单选，供以后扩展</td></tr>
<tr><td>9</td><td>生成队列</td><td>「加入队列生成」按档位切换：新档位换对应工作流与模型，提示词 / 种子 / 素材 / 宽高比 / 参考模式全部沿用</td></tr>
</tbody>
</table>

<h3>二、文档这一版动了什么</h3>
<table>
<thead><tr><th>类别</th><th>位置</th><th>具体</th></tr></thead>
<tbody>
<tr><td rowspan="4"><b>新增</b></td><td>第 03 章 §13</td><td>整节新增「视频生成扩展」：入口定位、与「视频生成」的机制差别、档位 / 宽高比 / 参考模式 / 参考上限、模型对应表、队列切档、管理台只读档位表、边界与不变量</td></tr>
<tr><td>第 03 章 §13.3</td><td>新增参考模式模型对应表：4 采 / 8 采 / 20 采 × 首尾帧 / 参考，各自的 UNET 与 LoRA；20 采无 LoRA</td></tr>
<tr><td>1.07 改了什么 · 表一 / 表二</td><td>本版 9 处界面 / 服务端改动，以及文档新增清单</td></tr>
<tr><td>本版组合测试结论</td><td>构建层 100 组全组合通过；真实出片覆盖五档 / 双比例 / 双模式 / 五种参考场景 / 队列切档，工作流文件哈希未变</td></tr>
<tr><td><b>改</b></td><td>封面能力数</td><td>能力数量由 8 个更新为 <b>9 个</b>（新增视频生成扩展）；提示词模板库仍不参与生成</td></tr>
<tr><td><b>没动</b></td><td>第 01 / 02 / 04 / 05 / 06 / 07 章</td><td>产品边界、挑工作流、配置表结构、加工作流五步、禁止事项：规则本身没变，1.07 没有新增规则</td></tr>
</tbody>
</table>
<blockquote>
<p>一句话记住这一版：<b>1.06 把"提示词"收进库，1.07 把"档位"变成工作流文件</b>——创作台多了一个会按模式换模型的视频能力，但它一行工作流都没改。</p>
</blockquote>

"""

SECTION_13 = """<h2 id="sec-videoext">十三、视频生成扩展（1.07 新增）</h2>
<p>一个<b>独立生成能力</b>：左栏「视频生成」下方，点进去是熟悉的「参考生视频」布局，但底下多了一整排档位、宽高比右侧多了「参考模式」。它和原来的「视频生成」<b>只是长得像</b>，内部完全是两套机制。</p>

<h3>13.1 和「视频生成」的差别</h3>
<table>
<thead><tr><th></th><th>视频生成</th><th>视频生成扩展</th></tr></thead>
<tbody>
<tr><td>一档是什么</td><td>一组参数（比例 / 分辨率 / 步数）</td><td><b>一份完整的工作流文件</b></td></tr>
<tr><td>切档时发生什么</td><td>同一条工作流，运行时改几个参数</td><td>整条工作流换掉，连加速链都不同</td></tr>
<tr><td>参考模式</td><td>文生 / 首尾帧 / 参考生 / 故事板，各有自己的工作流</td><td>同一档里用「参考模式」单选换 UNET / LoRA 模型</td></tr>
<tr><td>界面</td><td>顶部模式条 + 档位</td><td>子功能条 + 档位 + 宽高比 + 参考模式 + 时长 + 参考</td></tr>
</tbody>
</table>
<div class="callout a"><p><b>为什么不分两个工作流</b>：用户给的 5 份工作流已经把 4 / 8 / 20 步的加速链写死了，一档一份才是它们的原样。本能力只做「换文件 + 换模型名」，不做「拆开来重拼」。</p></div>

<h3>13.2 界面与默认值</h3>
<table>
<thead><tr><th>控件</th><th>选项 / 范围</th><th>默认</th><th>作用</th></tr></thead>
<tbody>
<tr><td>子功能</td><td>多参极速生视频（配置里可继续加）</td><td>多参极速生视频</td><td>以后扩展新子功能时自动多一个按钮</td></tr>
<tr><td>档位</td><td>4采480P / 8采480P / 8采768P / 20采480P / 20采768P</td><td><b>8采480P</b></td><td>选哪档就提交哪档的工作流文件</td></tr>
<tr><td>宽高比</td><td>16:9 / 9:16</td><td>16:9</td><td>写进 ResolutionSelector，不改它的分辨率连线</td></tr>
<tr><td><b>参考模式</b></td><td>首尾帧生成 / 参考生成</td><td>首尾帧生成</td><td>按「档位 × 模式」换 UNET（和 LoRA）模型</td></tr>
<tr><td>时长</td><td>4～15 秒</td><td>5.20 秒</td><td>写进时长开关，帧数由工作流自己折算</td></tr>
<tr><td>参考素材</td><td>最多 6 图 / 1 视频 / 3 音频</td><td>空</td><td>按实际数量接槽，没传的槽整只不连；可以只写提示词</td></tr>
</tbody>
</table>
<p>行尾说明会跟着变，例如「8采480P · 首尾帧生成 · 16:9 · 独立工作流」，一眼看清这次是哪套配方。</p>

<h3>13.3 参考模式与模型对应</h3>
<p>这份对应表是<b>按「档位 × 模式」查的</b>，不是按宽高比：480P / 768P 共用同一组模型，差别在工作流文件本身。运行时只改内存副本里两个模型字段，磁盘上的工作流保持原样。</p>
<table>
<thead><tr><th>档位</th><th>模式</th><th>UNET 加载器</th><th>LoRA 加载器</th></tr></thead>
<tbody>
<tr><td rowspan="2">4采480P</td><td>首尾帧生成</td><td><code>minimax_h3_fl2va_pruned_int8_convrot.safetensors</code></td><td><code>minimax_h3_fl2v_turbo_4step_v1.0_768p_comfyui_bf16.safetensors</code></td></tr>
<tr><td>参考生成</td><td><code>minimax_h3_ref2va_pruned_int8_convrot.safetensors</code></td><td><code>minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors</code></td></tr>
<tr><td rowspan="2">8采480P<br>8采768P</td><td>首尾帧生成</td><td><code>minimax_h3_fl2va_pruned_int8_convrot.safetensors</code></td><td><code>minimax_h3_fl2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors</code></td></tr>
<tr><td>参考生成</td><td><code>minimax_h3_ref2va_pruned_int8_convrot.safetensors</code></td><td><code>minimax_h3_ref2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors</code></td></tr>
<tr><td rowspan="2">20采480P<br>20采768P</td><td>首尾帧生成</td><td><code>minimax_h3_fl2va_pruned_int8_convrot.safetensors</code></td><td><b>不挂 LoRA</b></td></tr>
<tr><td>参考生成</td><td><code>minimax_h3_ref2va_pruned_int8_convrot.safetensors</code></td><td><b>不挂 LoRA</b></td></tr>
</tbody>
</table>
<p>20 采走的是没有 LoRA 加速的那条链，所以换模式时<b>只换 UNET</b>；4 / 8 采才同时换 LoRA。每条工作流里还有一条备用模型链，它不接到采样器，切换时也不会被改动。</p>

<h3>13.4 加入队列生成</h3>
<p>点「加入队列生成」时，会在这张任务卡后面接着跑一次：<b>换到所选档位对应的工作流与模型</b>，其余原样沿用——同一个种子、同一段提示词、同一批图 / 视频 / 音频参考、同一个宽高比、同一个参考模式。任务卡里两行各自显示自己的档位与模式。</p>

<h3>13.5 管理台档位表</h3>
<p>管理台里这个能力的档位表是<b>只读</b>的（黄底）：只保留「① 工作流文件」和「② 已有挡位」两块，没有参数节点、不用设的节点，也没有「新增挡位」。五档各自一行，显示对应的工作流文件名。顶部有子功能单选按钮，以后扩展新子功能时用它切换查看。</p>

<h3>13.6 边界与不变量</h3>
<ul>
<li><b>工作流文件是只读的</b>：模型替换发生在提交前的内存副本里，不写回磁盘；5 份工作流文件的哈希在测试中保持不变。</li>
<li><b>只动两个字段</b>：生效链的 <code>unet_name</code> / <code>lora_name</code>；提示词、种子、宽高比、时长、参考素材按用户输入注入。</li>
<li><b>加速链不拆</b>：ImpactSwitch / EasyCache / TESpeed 等节点结构与参数属于档位文件本身，运行时不改。</li>
<li><b>备用链不碰</b>：工作流里没接到采样器的那条模型链保持原样。</li>
<li><b>独立性</b>：这是独立能力与独立模块，只在原有文件加了极小挂载钩子；图片、角色、场景、道具、故事板、原视频生成、音频、画布、历史都不受影响。</li>
</ul>
<blockquote>
<p>一句话：<b>档位选工作流，模式选模型，其余照旧</b>——换档不动提示词和素材，换模式不动工作流文件。</p>
</blockquote>

"""


def main() -> int:
    s = DOC.read_text(encoding="utf-8")
    original_len = len(s)

    # 1. 版本号 / 日期 / 封面能力数
    s = replace_once(s, "<title>ComfyBridge · 需求与功能文档 1.06</title>",
                     "<title>ComfyBridge · 需求与功能文档 1.07</title>", "title")
    s = replace_once(s, '<div class="meta">需求与功能文档 · v1.06</div>',
                     '<div class="meta">需求与功能文档 · v1.07</div>', "侧栏 meta")
    s = replace_once(s, '<span class="badge a">v1.06 · 与当前实现逐项对齐</span>',
                     '<span class="badge a">v1.07 · 与当前实现逐项对齐</span>', "封面 badge 版本")
    s = replace_once(s, '<span class="badge">2026-11-22</span>',
                     '<span class="badge">2026-11-25</span>', "封面日期")
    s = replace_once(s, '<span class="badge">8 个能力全部可用</span>',
                     '<span class="badge">9 个能力全部可用</span>', "封面能力数")
    s = replace_once(s, '<span class="badge b">本版新增：提示词模板库 + 提示词速取小窗</span>',
                     '<span class="badge b">本版新增：视频生成扩展 + 参考模式</span>', "封面本版新增")

    # 2. 目录新增 §13
    s = replace_once(s,
        '<a href="#sec-ptlib">十二、提示词模板库（1.06 新增）</a></div></div>',
        '<a href="#sec-ptlib">十二、提示词模板库（1.06 新增）</a>'
        '<a href="#sec-videoext">十三、视频生成扩展（1.07 新增）</a></div></div>',
        "目录 §13")

    # 3. 封面导航表补一行
    s = replace_once(s,
        '<tr><td>我上次问的那些问题</td><td>附录</td><td>逐条回答，含"这次砍掉了什么"</td></tr>\n</tbody>',
        '<tr><td>我上次问的那些问题</td><td>附录</td><td>逐条回答，含"这次砍掉了什么"</td></tr>\n'
        '<tr><td><b>视频生成扩展怎么用</b></td><td><b>第 03 章 §13</b></td>'
        '<td><b>第 9 个生成能力：一档一份工作流；宽高比右侧的参考模式按档位换模型；队列切档沿用其余输入</b></td></tr>\n</tbody>',
        "封面导航表")

    # 4. 修正 1.06 里"不是第 9 个生成能力"的说法（现在确实有第 9 个了）
    s = replace_once(s,
        "<b>它是独立功能，不是第 9 个生成能力</b>——不调用工作流、不产生任务，不碰原有的 8 个能力、画布与历史。",
        "<b>它是独立功能，不参与生成</b>——不调用工作流、不产生任务，不碰原有的 8 个能力、画布与历史；"
        "1.07 新增的第 9 个能力是「视频生成扩展」，与模板库互不干涉。",
        "1.06 里第 9 个能力的说法")

    # 5. 插入 1.07 变更记录（放在 1.06 之前）
    s = replace_once(s,
        '<h2 style="border-top:none;padding-top:6px">1.06 改了什么</h2>',
        CHANGELOG_107 + '<h2 style="border-top:none;padding-top:6px">1.06 改了什么</h2>',
        "1.07 变更记录")

    # 6. 插入 §13（放在第 04 章之前）
    s = replace_once(s,
        '<h1 class="chapter"><em>第 04 章</em>架构与配置</h1>',
        SECTION_13 + '<h1 class="chapter"><em>第 04 章</em>架构与配置</h1>',
        "第 03 章 §13")

    DOC.write_text(s, encoding="utf-8")
    print(f"OK 文档已更新：{original_len} -> {len(s)} 字符")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
