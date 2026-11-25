import io
import re
from html.parser import HTMLParser

VOID = {"br", "img", "hr", "meta", "link", "input", "area", "base", "col", "embed",
        "source", "track", "wbr"}

s = open("../../ComfyBridge-需求与功能文档-1.03.html", encoding="utf-8").read()
s2 = re.sub(r'data:image/jpeg;base64,[A-Za-z0-9+/=]+', "IMG", s)


class P(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.errs = []

    def handle_starttag(self, tag, attrs):
        if tag in VOID:
            return
        self.stack.append((tag, self.getpos()))

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if not self.stack:
            self.errs.append(f"多余的 </{tag}> @ {self.getpos()}")
            return
        if self.stack[-1][0] == tag:
            self.stack.pop()
            return
        # 找最近的同名开标签
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                bad = self.stack[i + 1:]
                self.errs.append(
                    f"</{tag}> @ {self.getpos()} 关掉的是 {tag}，但里面还有未闭合的 "
                    + ", ".join(f"{t}@{pos}" for t, pos in bad)
                )
                del self.stack[i:]
                return
        self.errs.append(f"没有对应开标签的 </{tag}> @ {self.getpos()}")


p = P()
p.feed(s2)
print("未闭合:", [(t, pos) for t, pos in p.stack])
print("错误条数:", len(p.errs))
for e in p.errs[:12]:
    print("  ", e)

print("figure:", s2.count("<figure"), s2.count("</figure>"))
print("img:", s2.count("<img"), " 真图:", s.count("data:image/jpeg;base64"))
print("残留占位:", len(re.findall(r"\[\[IMG:", s2)), "残 src=IMG:", s2.count('src="IMG"'))
print("h2:", len(re.findall(r"<h2", s2)), "h3:", len(re.findall(r"<h3", s2)))
for m in re.finditer(r"<h2[^>]*>(.*?)</h2>", s2, re.S):
    print("  H2", re.sub(r"<[^>]+>", "", m.group(1))[:60])

# 常见口径残留检查
for kw in ["17 个", "17 个能力", "2 个能用", "可用的是 2 个", "4/6/9", "6 格故事板", "2026-11-15"]:
    n = s2.count(kw)
    if n:
        print("残留", kw, n)
