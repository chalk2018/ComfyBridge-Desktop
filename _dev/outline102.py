import re

s = open("doc102_stripped.html", encoding="utf-8").read()
for i, l in enumerate(s.split("\n"), 1):
    t = re.sub(r"\s+", " ", l).strip()
    if re.search(r"<h1|<h2|<h3|<nav|<section|<aside|<figure|class=\"card|\s id=", l):
        print(i, t[:160])
