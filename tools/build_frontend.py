"""从开发原型提取全部 CSS 与页面骨架，生成 web/index.html。

界面以原型为主：样式一行不改，body 结构保持 <div id="root"></div>，
只把演示用的 <script> 换成真正对接服务端的 app.js；末尾补一小段样式，
覆盖原型在小屏隐藏右栏的行为（需求：右栏执行队列在手机上不能消失）。

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
        + "<style>\n" + EXTRA_CSS + "\n</style>\n"
        + "</head>\n<body>\n<div id=\"root\"></div>\n"
        + '<script src="/static/app.js"></script>\n'
        + "</body>\n</html>\n"
    )
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(page, encoding="utf-8")
    print(f"written {TARGET} ({len(page)} chars)")


if __name__ == "__main__":
    main()
