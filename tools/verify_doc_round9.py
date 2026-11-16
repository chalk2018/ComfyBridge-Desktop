"""校验 1.02 文档是否覆盖本轮 16 条改动、锚点完整、旧表述清除。"""
from __future__ import annotations

import re
from pathlib import Path

DOC = Path(__file__).resolve().parent.parent.parent / "ComfyBridge-需求与功能文档-1.02.html"
html = DOC.read_text(encoding="utf-8")

# 去掉内嵌图片后再做文本检查，避免 base64 干扰关键词计数
text = re.sub(r"data:image/jpeg;base64,[A-Za-z0-9+/=]+", "", html)
text = re.sub(r"<[^>]+>", " ", text)
text = re.sub(r"\s+", " ", text)

checks = {
    "1 结果卡 5 行 + 看完整提示词": ["最多 5 行", "看完整提示词"],
    "1 基于上一条完整提示词": ["完整提示词，含换行", "不是卡片上截断的那 5 行"],
    "2 预览放大接近全屏": ["⤢ 放大", "接近全屏"],
    "3 删除弹窗复制图片 + Ctrl+C": ["弹窗里 没有", "复制图片", "Ctrl/⌘ + C"],
    "4 设定图 @ 引用参考图": ["已引用", "把这张图加入描述框上方"],
    "5 基于上一条带回风格": ["自定义风格及填写内容"],
    "6 默认自定义风格": ["进入页面 / 每次生成后默认选"],
    "7 图生图仅自定义": ["图生图时预设全部禁用，只能选自定义"],
    "8 图生图空描述": ["图生图允许描述框为空", "允许直接生成"],
    "9 生成区内部滚动": ["滚动条在这个区域内侧", "不在整页最外面"],
    "10 右栏固定头": ["上半固定", "下半滚动"],
    "11 任务缩略图": ["每条任务底部带一行缩略图"],
    "12 档位拖拽排序": ["拖拽已有档位改变顺序"],
    "13 模板只读 + 预设": ["通用块 6 段， 只读", "线稿风格句 4 条， 只读", "重读提示词库"],
    "14 预设增删": ["新增预设", "删除预设"],
    "15 档位表去画法/参考": ["18 种选择", "6 组档位", "画法与参考已不在这里"],
    "16 模板模态框": ["模态框", "不再是管理台左栏的独立页面"],
}

ok = True
for name, kws in checks.items():
    miss = [k for k in kws if k not in text]
    flag = "PASS" if not miss else "FAIL"
    if miss:
        ok = False
    print(f"[{flag}] {name}" + (f" 缺: {miss}" if miss else ""))

# 锚点：所有 href="#xxx" 都得有对应 id
ids = set(re.findall(r'\bid="([^"]+)"', html))
hrefs = set(re.findall(r'href="#([^"]+)"', html))
missing = sorted(hrefs - ids)
print(f"[{'PASS' if not missing else 'FAIL'}] 锚点 {len(hrefs)} 个", f"缺: {missing}" if missing else "")
if missing:
    ok = False

# 过时口径：本轮明确改掉的「五页菜单」
for s in ["五页菜单", "五页 + 档位表", "页数统一成实际 5 页", "18 个组合怎么算出来的"]:
    n = text.count(s)
    print(f"[{'FAIL' if n else 'PASS'}] 过时表述 {s!r}: {n}")
    if n:
        ok = False

print("RESULT", "ALL PASS" if ok else "HAS FAIL")
raise SystemExit(0 if ok else 1)
