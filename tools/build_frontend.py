"""从开发原型提取全部 CSS 与页面骨架，生成 web/index.html。

界面以原型为主：样式一行不改，body 结构保持 <div id="root"></div>，
只把演示用的 <script> 换成真正对接服务端的 app.js；末尾补一小段样式，
覆盖原型在小屏隐藏右栏的行为（需求：右栏执行队列在手机上不能消失）。

⚠️ 别直接跑：web/index.html 后来被手工改过（成品图 object-fit / 档位拖拽 / 抽屉显示 /
成品尺寸标签等），这些改动没有回填到本脚本，跑一次会把它们冲掉。
要重建请先用 `diff` 把 index.html 里多出来的样式补进来，再执行。
本脚本新增的 SHEET_CSS（角色设定图：创作台表单 + 管理台模板）已经同步过，
重建时不会丢。

Usage:
    python tools/build_frontend.py
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "_reference" / "prototype.html"
TARGET = ROOT / "web" / "index.html"

EXTRA_CSS = """
/* ---- 真实成品与手机适配（在既有样式之上补的最小集合） ---- */
.tile img{display:block;border:0}
.tile-label{position:absolute;left:8px;bottom:8px;z-index:2;background:rgba(15,23,35,.66);color:#fff;
  font-size:11px;line-height:1.5;padding:2px 8px;border-radius:6px;pointer-events:none;
  max-width:calc(100% - 16px);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.gallery .card{position:relative}
.gcheck{position:absolute;right:10px;top:10px;z-index:4;background:#fff;border-radius:7px;padding:4px 7px;
  box-shadow:0 1px 6px rgba(20,30,50,.2)}
.gcheck input{width:16px;height:16px;cursor:pointer}
.ref-thumb img{display:block}
.sendbtn:disabled{opacity:.45;cursor:not-allowed}
/* 真实工作流的节点 id 是 459_451 这类长串；原型给 .id 只留 20px（示例是 4/6/9 一位数），
   文字会溢出压到 class 名上，看起来像重影。这里给足宽度并裁切。 */
.node-row{flex-wrap:wrap;row-gap:2px;overflow:hidden}
.node-row .id{width:66px;min-width:66px;flex:0 0 66px;text-align:left;white-space:nowrap;
  overflow:hidden;text-overflow:ellipsis;font-size:10px}
.node-row .cls{flex:1 1 auto;min-width:0;white-space:nowrap}
/* 参数摘要挪到第二行，第一行才放得下完整的 class 名 */
.node-row .nf{flex:1 1 100%;max-width:none;margin-left:21px;text-align:left;
  font-size:9.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.node-row .role{flex:0 0 auto;margin-left:auto}
/* 成品块两行：第一行成品 N，第二行这一档的尺寸（需求 §1.6） */
.tile-label{white-space:normal;line-height:1.35;display:block}
.tile-label b{font-weight:600}
.tile-label span{display:block;font-size:10px;opacity:.85}
/* 配置损坏时的顶部红条（需求 04 章） */
.configerr{background:#fdefec;border-bottom:1px solid #f0c9bd;color:#a83a20;padding:9px 18px;
  font-size:13px;display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.configerr .msg{flex:1;min-width:240px}
/* 档位表：逐参数生效自检 + 每档出图尺寸 */
.geff{font-size:10px;color:#5b6575;background:#f2f5fa;border:1px solid transparent;border-radius:5px;
  padding:2px 7px;margin-left:8px;white-space:nowrap;max-width:200px;overflow:hidden;text-overflow:ellipsis}
.geff.ok{border-color:#cfe8d8;color:#26734a}
.geff.bad{border-color:#f0c9bd;color:#a83a20}
.effbox{background:#f7faff;border:1px solid #dfe8fb;border-radius:8px;padding:9px 11px;margin-bottom:10px;font-size:11px}
.effhd{font-weight:650;color:#1e6b3a;margin-bottom:5px;display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.effhd.bad{color:#a83a20}
.effrow{display:grid;grid-template-columns:120px minmax(0,1fr) auto;gap:8px;padding:3px 0;
  border-top:1px dashed #e3e9f4;align-items:center;word-break:break-all}
.effrow .mono{color:#445}
.effrow b{color:#1e6b3a;font-size:10px;white-space:nowrap}
.effrow.bad b{color:#a83a20}
/* 历史记录：全选、单张删除 */
.gall{display:inline-flex;align-items:center;gap:6px;font-size:12px;color:#45505f;background:#fff;
  border:1px solid var(--line);border-radius:8px;padding:0 10px;height:34px;cursor:pointer;white-space:nowrap}
.gall input{width:15px;height:15px;cursor:pointer}
.gdelone{position:absolute;right:10px;top:44px;z-index:4;width:26px;height:26px;border:1px solid #e6d3cd;
  background:#fff;color:#a83a20;border-radius:7px;cursor:pointer;font-size:12px;box-shadow:0 1px 6px rgba(20,30,50,.14)}
.gdelone:hover{background:#fdefec}
/* 会话任务 / 对话卡片删除 */
.jobwrap{position:relative}
.jobwrap .job .head{padding-right:36px}
.jobdel{position:absolute;right:14px;top:11px;z-index:5;width:24px;height:24px;border:1px solid #e6eaf1;
  background:#fff;color:#7d8798;border-radius:6px;cursor:pointer;font-size:12px}
.jobdel:hover{background:#fdefec;color:#a83a20;border-color:#f0c9bd}
.task-item{position:relative;padding-right:30px}
.tdel{position:absolute;right:4px;top:50%;transform:translateY(-50%);width:22px;height:22px;
  border:0;background:transparent;color:#8a94a5;cursor:pointer;border-radius:6px;font-size:11px}
.tdel:hover{background:#fdefec;color:#a83a20}
@media (max-width:1180px){
  /* 小屏下把右栏改成整页底部的一块，手机上依然看得到进度 */
  .studio{grid-template-columns:172px minmax(0,1fr)}
  .drawer{display:block;grid-column:1 / -1;position:static;max-height:none;border-top:1px solid var(--line)}
}
@media (max-width:640px){
  .studio{display:block}
  .rail{position:static;max-height:none;display:flex;flex-wrap:wrap;gap:6px;padding:8px}
  .rail .group{width:100%}
  .rail .item{flex:1 1 44%}
  .drawer{background:#fff;border-top:1px solid var(--line)}
  .gallery{grid-template-columns:repeat(2,minmax(0,1fr))}
  button,.btn,.sendbtn,.grade,.item{min-height:44px}
  .composer{padding:8px 10px 10px}
  .admin-body{display:block}
  .admin-nav{display:flex;flex-wrap:wrap}
}
"""


SHEET_CSS = """\
/* ================= 角色设定图：创作台表单 + 管理台模板 ================= */

/* 表单卡片：沿用 .inputbox 的强调边框，内部按「版式 / 画法 / 参考 / 角色 / 风格」分行 */
.sheetbox{padding:6px 10px 8px}
.sheetbar{display:flex;align-items:center;gap:8px;flex-wrap:wrap;padding:5px 2px;min-height:34px}
.sheetbar + .sheetbar{border-top:1px dashed var(--line2)}
.sheetbar .slb{font-size:11.5px;color:var(--ink3);flex:0 0 auto;min-width:26px;letter-spacing:.02em}
/* 分段按钮：比画质档更小一档，正好放进输入区 */
.seg{display:inline-flex;align-items:center;gap:2px;padding:2px;border-radius:8px;background:#eef1f7;border:1px solid #e6eaf2}
.segb{height:24px;padding:0 9px;border:0;border-radius:6px;background:transparent;color:var(--ink2);
  font:inherit;font-size:12px;cursor:pointer;white-space:nowrap;display:inline-flex;align-items:center;transition:.12s}
.segb:hover{color:var(--ink)}
.segb.on{background:#fff;color:var(--accent);font-weight:600;box-shadow:0 1px 3px rgba(20,30,50,.1)}
.segx{font-style:normal;font-size:9.5px;color:var(--ink4);margin-left:5px}
.segb.on .segx{color:var(--accent)}
.segsel{height:26px;width:auto;min-width:132px;max-width:260px;font-size:12px;padding:0 8px;flex:0 0 auto}
.segtext{height:26px;font-size:12px;padding:0 8px;flex:1 1 200px;min-width:140px;width:auto}
.sheetchar{width:100%;border:0;outline:none;resize:vertical;font:inherit;font-size:13.5px;line-height:1.65;
  min-height:68px;max-height:200px;padding:7px 3px;background:transparent;color:var(--ink)}
.sheetchar::placeholder{color:var(--ink4)}
.warnhint{font-size:10.5px;color:#8a5a00;background:#fff8ec;border:1px solid #f0dcb8;border-radius:5px;padding:1px 6px}
/* 「看完整提示词」面板 */
.pvbox{margin-top:4px;border-top:1px dashed var(--line2);padding-top:8px}
.pvhead{display:flex;align-items:baseline;gap:8px;font-size:11.5px;margin-bottom:6px;color:var(--ink2)}
.pvhead.bad{color:#a83a20}
.pvbox pre{margin:0;max-height:210px;overflow:auto;background:#f7f9fd;border:1px solid var(--line);border-radius:8px;
  padding:10px 12px;font-family:inherit;font-size:11.5px;line-height:1.8;white-space:pre-wrap;word-break:break-word;color:#39445a}
/* 卡片上的摘要行 */
.promptmeta{display:flex;align-items:center;gap:8px;margin:-4px 0 10px;flex-wrap:wrap}
.linkbtn{font-size:11.5px;color:var(--accent);background:none;border:0;padding:0;cursor:pointer;text-decoration:underline;text-underline-offset:2px}
.linkbtn:hover{color:#2359e0}
.promptfull{margin:0;max-height:56vh;overflow:auto;background:#f7f9fd;border:1px solid var(--line);border-radius:9px;
  padding:12px 14px;font-family:inherit;font-size:12px;line-height:1.85;white-space:pre-wrap;word-break:break-word;color:#39445a}

/* 管理台「设定图模板」 */
.sheet-cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:12px}
.lcard{border:1px solid var(--line);border-radius:10px;padding:12px 14px;background:#fcfdff}
.lcard .chip{height:20px;font-size:10.5px}
.lmeta{display:flex;align-items:center;gap:7px;margin-top:5px}
.sheet-block{border:1px solid var(--line);border-radius:9px;padding:9px 11px;margin-bottom:8px;background:#fcfdff}
.sheet-block .bh{display:flex;align-items:center;gap:8px;font-size:12px;font-weight:600;color:var(--ink2);margin-bottom:6px}
.sheet-block .bh .mono{font-weight:400;color:var(--ink4);font-size:10px}
.sheet-block textarea{width:100%;resize:vertical;border:1px solid var(--line);border-radius:7px;padding:8px 10px;
  font:inherit;font-size:12px;line-height:1.8;color:#2b3444;background:#fff;outline:none}
.sheet-block textarea:focus{border-color:var(--accent);box-shadow:0 0 0 3px rgba(47,107,255,.1)}
.style-row{display:flex;gap:8px;margin-bottom:6px;align-items:center}
.style-row .sname{width:120px;flex:0 0 auto}
.style-row .input{height:30px;font-size:12px}

@media (max-width:820px){
  .sheetbar{gap:6px}
  .segsel,.segtext{height:28px}
  .sheetchar{min-height:60px}
  .style-row{flex-wrap:wrap}
  .style-row .sname{width:100%}
  .sheet-cards{grid-template-columns:1fr}
}
"""

def main() -> None:
    """生成 index.html。"""
    source = SOURCE.read_text(encoding="utf-8")
    head = source.split("</head>", 1)[0]
    head = head.replace("<title>ComfyBridge · 交互原型</title>", "<title>ComfyBridge</title>")
    # 成品界面不出现"原型/演示"字样（CSS 注释也一并清掉，敏感词扫描更干净）
    head = head.replace("ComfyBridge 原型样式", "ComfyBridge 页面样式")
    head = head.replace("原型", "页面").replace("演示", "成品")
    page = (
        head
        + "<style>\n" + EXTRA_CSS + "\n" + SHEET_CSS + "</style>\n"
        + "</head>\n<body>\n<div id=\"root\"></div>\n"
        + '<script src="/static/app.js"></script>\n'
        + "</body>\n</html>\n"
    )
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(page, encoding="utf-8")
    print(f"written {TARGET} ({len(page)} chars)")


if __name__ == "__main__":
    main()
