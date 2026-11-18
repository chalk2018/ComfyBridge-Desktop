import base64
import io
import re
from pathlib import Path

import PIL.Image as Image

s = Path("../../ComfyBridge-需求与功能文档-1.02.html").read_text(encoding="utf-8")
for m in re.finditer(r'(alt=")([^"]*)(".*?)(data:image/(\w+);base64,)([A-Za-z0-9+/=]+)', s, re.S):
    name = m.group(2)
    raw = base64.b64decode(m.group(6))
    im = Image.open(io.BytesIO(raw))
    print(f"{name:22s} {im.size} {len(raw)//1024}KB {m.group(5)}")
