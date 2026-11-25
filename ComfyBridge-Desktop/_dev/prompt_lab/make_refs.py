"""生成测试参考图：布局草图（带汉字标注的简陋手绘）与彩色布局图。"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

REF = Path(__file__).resolve().parent / "refs"
REF.mkdir(exist_ok=True)


def font(size):
    for p in ["C:/Windows/Fonts/simhei.ttf", "C:/Windows/Fonts/msyh.ttc"]:
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


# 1) 简陋布局草图：白底、方框、箭头、汉字标注 —— 用于验证“只取相对位置、不搬文字/像素”
im = Image.new("RGB", (1024, 576), "white")
d = ImageDraw.Draw(im)
f = font(30)
fs = font(22)
# 房间外框
d.rectangle([60, 60, 964, 516], outline="black", width=4)
# 门（下边中间开口）
d.rectangle([470, 500, 560, 520], fill="white", outline="black", width=3)
d.text((482, 470), "门", font=fs, fill="black")
# 窗（上边）
d.line([120, 60, 420, 60], fill="blue", width=8)
d.text((230, 20), "窗", font=fs, fill="blue")
# 桌子居中
d.rectangle([420, 260, 620, 360], outline="black", width=3)
d.text((468, 292), "桌", font=f, fill="black")
# 椅子四个
for (x, y) in [(380, 260), (640, 260), (380, 340), (640, 340)]:
    d.rectangle([x, y, x+34, y+34], outline="black", width=2)
# 左侧沙发
d.rectangle([90, 200, 200, 400], outline="black", width=3)
d.text((120, 290), "沙发", font=fs, fill="black")
# 右侧柜子
d.rectangle([820, 180, 940, 260], outline="black", width=3)
d.text((850, 205), "柜", font=fs, fill="black")
# 左上植物
d.ellipse([100, 90, 170, 160], outline="green", width=3)
d.text((105, 165), "植物", font=fs, fill="green")
# 箭头指示朝向
d.line([300, 450, 300, 380], fill="red", width=3)
d.polygon([(290, 390), (300, 375), (310, 390)], fill="red")
d.text((250, 455), "主视方向", font=fs, fill="red")
im.save(REF / "floorplan_sketch.png")

# 2) 彩色布局图（俯视色块版）
im2 = Image.new("RGB", (1024, 576), "#f5f0e6")
d2 = ImageDraw.Draw(im2)
d2.rectangle([40, 40, 984, 536], outline="#333", width=4)
# 地面对分色块：一半木色一半瓷砖
d2.rectangle([42, 42, 512, 534], fill="#d9b382")
d2.rectangle([514, 42, 982, 534], fill="#dfe3e6")
# 中央桌
d2.rounded_rectangle([430, 250, 610, 350], radius=12, fill="#8b5a2b", outline="#5a3a1b", width=3)
# 沙发
d2.rounded_rectangle([80, 180, 230, 420], radius=20, fill="#6d8b74")
# 柜
d2.rectangle([820, 160, 960, 280], fill="#a0522d")
# 植物
d2.ellipse([100, 80, 190, 170], fill="#4caf50")
# 窗
d2.line([120, 42, 430, 42], fill="#29b6f6", width=12)
im2.save(REF / "floorplan_color.png")

print("saved:", [str(p) for p in REF.iterdir()])
