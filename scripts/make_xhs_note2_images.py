# -*- coding: utf-8 -*-
"""笔记二配图 · 明星不老拆解 · 3 张 3:4"""
from PIL import Image, ImageDraw, ImageFont
import os, math

W, H = 1242, 1656
BG = (250, 246, 240)
INK = (43, 43, 43)
INK2 = (110, 105, 98)
DAY = (232, 134, 46)
NIGHT = (62, 76, 122)
WHITE = (255, 255, 255)
LINE = (228, 220, 210)

FB = "/System/Library/Fonts/Hiragino Sans GB.ttc"
def F(size, bold=False):
    return ImageFont.truetype(FB, size, index=1 if bold else 0)

def canvas():
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 14], fill=DAY)
    d.rectangle([W*2//3, 0, W, 14], fill=NIGHT)
    return img, d

def footer(d, page, total="3"):
    d.line([80, H-110, W-80, H-110], fill=LINE, width=2)
    d.text((80, H-84), "冻龄观察 · 只讲公开信息", font=F(30), fill=INK2)
    page = f"{page} / {total}"
    tw = d.textlength(page, font=F(30))
    d.text((W-80-tw, H-84), page, font=F(30), fill=INK2)

OUT = "exports/xhs_note2_2026-10-24/"
os.makedirs(OUT, exist_ok=True)

# ---------- 图1 封面：四层拆解 ----------
img, d = canvas()
d.text((80, 130), "扒了刘德华、撒贝宁、苏有朋的", font=F(52), fill=INK2)
d.text((80, 210), "公开作息和采访", font=F(52), fill=INK2)
d.text((80, 360), "明星不老，", font=F(140, bold=True), fill=INK)
d.text((80, 540), "拆开就四层", font=F(140, bold=True), fill=INK)
layers = [("骨相天赋", "学不了", INK2), ("自律", "人人学得起", DAY),
          ("医美", "要钱要恢复期", INK2), ("包装", "妆造灯光修图", INK2)]
y = 800
for i, (t, note, c) in enumerate(layers):
    d.rounded_rectangle([80, y, W-80, y+130], radius=24, fill=WHITE, outline=LINE, width=2)
    d.rounded_rectangle([80, y, 96, y+130], radius=8, fill=DAY if i == 1 else LINE)
    d.text((140, y+28), t, font=F(56, bold=(i == 1)), fill=c if i == 1 else INK)
    tw = d.textlength(note, font=F(36))
    d.text((W-120-tw, y+44), note, font=F(36), fill=INK2)
    y += 156
d.text((80, 1452), "先说结论：能学的就一层，还最省钱", font=F(42, bold=True), fill=DAY)
footer(d, "1")
img.save(OUT + "01_cover.png")

# ---------- 图2 公开作息表 ----------
img, d = canvas()
d.text((80, 96), "他们的公开作息", font=F(76, bold=True), fill=INK)
d.text((80, 208), "都是访谈和报道里能查到的，听说的一律没放", font=F(34), fill=INK2)
people = [
    ("刘德华", ["30 多年不喝冰水（他自己说的）", "2013 年起跟着家里吃素", "晚 7 点后不进食，等于天天 16:8", "晚 10 点睡早 6 点起", "体重常年 63kg，浮动不到 1kg"]),
    ("撒贝宁", ["每天 5:30 起床晨跑加力量", "爬山旅行带娃，一个不落", "医美的事他没提过，咱不瞎猜"]),
    ("苏有朋", ["每天健身 2 小时", "40 岁后低碳抗糖，包里揣着低卡便当", "2019 年被质疑填充过度（没实锤）", "后来收着做了，网友反而说更自然"]),
]
y = 300
for name, rows in people:
    n = len(rows)
    hgt = 108 + n * 62 + 26
    d.rounded_rectangle([80, y, W-80, y+hgt], radius=28, fill=WHITE, outline=LINE, width=2)
    d.text((130, y+30), name, font=F(50, bold=True), fill=INK)
    yy = y + 104
    for r in rows:
        d.ellipse([140, yy+15, 156, yy+31], fill=DAY)
        d.text((182, yy), r, font=F(34), fill=INK2)
        yy += 62
    y += hgt + 30
footer(d, "2")
img.save(OUT + "02_routines.png")

# ---------- 图3 能学的三件事 ----------
img, d = canvas()
d.text((80, 96), "能学的就三件", font=F(76, bold=True), fill=INK)
d.text((80, 208), "按收益排序，不花钱的那种", font=F(40), fill=INK2)
items = [
    ("1", "防晒", ["紫外线是晒老的大头，占八成", "帽子伞墨镜先上，防晒霜排后面"], DAY),
    ("2", "起床点定死", ["几点睡不好控，几点起你能控", "起床半小时内出门晒晒太阳"], NIGHT),
    ("3", "晚饭提前", ["睡前三四小时吃完，先把夜宵删了", "不用一上来就 16:8，先做到这条"], DAY),
]
y = 330
for num, t, descs, c in items:
    d.rounded_rectangle([80, y, W-80, y+260], radius=28, fill=WHITE, outline=LINE, width=2)
    d.ellipse([130, y+70, 220, y+160], fill=c)
    tw = d.textlength(num, font=F(56, bold=True))
    d.text((175-tw/2, y+88), num, font=F(56, bold=True), fill=WHITE)
    d.text((270, y+50), t, font=F(54, bold=True), fill=INK)
    for j, desc in enumerate(descs):
        d.text((270, y+128 + j*52), desc, font=F(33), fill=INK2)
    y += 300
d.rounded_rectangle([80, 1290, W-80, 1440], radius=24, fill=(255, 243, 230))
d.text((120, 1322), "至于「李嘉诚吃 NMN」——", font=F(38, bold=True), fill=INK)
d.text((120, 1378), "没一个实锤，全是卖货蹭出来的，别当真", font=F(36), fill=INK2)
footer(d, "3")
img.save(OUT + "03_actions.png")
print("note2 images done")
