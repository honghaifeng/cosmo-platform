# -*- coding: utf-8 -*-
"""笔记三/四/五 配图 · 抗糖 / 医美 / 睡眠节律 · 各 3 张 3:4"""
from PIL import Image, ImageDraw, ImageFont
import os

W, H = 1242, 1656
BG = (250, 246, 240)
INK = (43, 43, 43)
INK2 = (110, 105, 98)
DAY = (232, 134, 46)
NIGHT = (62, 76, 122)
WHITE = (255, 255, 255)
LINE = (228, 220, 210)
TINT = (255, 243, 230)

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

def card(d, y, h, title, rows, tsize=50, rsize=34, gap=64):
    d.rounded_rectangle([80, y, W-80, y+h], radius=28, fill=WHITE, outline=LINE, width=2)
    d.text((130, y+34), title, font=F(tsize, bold=True), fill=INK)
    yy = y + 34 + tsize + 26
    for r in rows:
        d.ellipse([140, yy+15, 156, yy+31], fill=DAY)
        d.text((182, yy), r, font=F(rsize), fill=INK2)
        yy += gap
    return yy

OUT = "exports/xhs_notes345_2026-10-24/"
os.makedirs(OUT, exist_ok=True)

# ================= 笔记三 · 抗糖 =================
# 图1 封面
img, d = canvas()
d.text((80, 150), "花几百块买抗糖丸之前，", font=F(52), fill=INK2)
d.text((80, 230), "先把这篇看完", font=F(52), fill=INK2)
d.text((80, 380), "抗糖丸才是", font=F(128, bold=True), fill=INK)
d.text((80, 540), "智商税，", font=F(128, bold=True), fill=INK)
d.text((80, 700), "但抗糖是真的", font=F(128, bold=True), fill=DAY)
d.rounded_rectangle([80, 920, W-80, 1250], radius=28, fill=WHITE, outline=LINE, width=2)
d.text((130, 960), "皮肤发黄、变硬、没弹性", font=F(46, bold=True), fill=INK)
d.text((130, 1030), "糖和蛋白质抱团，会「交联」胶原蛋白——", font=F(36), fill=INK2)
d.text((130, 1090), "这个过程叫糖化，机制是成立的", font=F(36), fill=INK2)
d.text((130, 1165), "所以问题不在要不要抗糖，", font=F(36), fill=INK2)
d.text((130, 1205), "在于该怎么抗", font=F(36), fill=INK2)
d.text((80, 1330), "答案就一句：核心在盘子，不在瓶子", font=F(44, bold=True), fill=DAY)
footer(d, "1")
img.save(OUT + "n3_01_cover.png")

# 图2 糖怎么让人变老
img, d = canvas()
d.text((80, 96), "糖是怎么让你变老的", font=F(76, bold=True), fill=INK)
d.text((80, 208), "机制讲清楚，智商税就收不到你", font=F(38), fill=INK2)
card(d, 300, 380, "① 糖化（AGEs）", [
    "糖+蛋白质结合 → 交联胶原纤维",
    "表现：皮肤发黄、弹性变差",
    "这个过程不可逆，只能减缓",
], tsize=48)
card(d, 720, 380, "② 糖从哪来（最容易被忽略）", [
    "一杯奶茶 ≈ 30–60g 糖，一口超标",
    "番茄酱、沙拉酱、速冲麦片都有糖",
    "「不甜」不等于没糖",
], tsize=48)
d.text((80, 1180), "参考线：每日添加糖 ≤25g（世卫建议量级）", font=F(38, bold=True), fill=DAY)
d.text((80, 1250), "不是不吃碳水，是控「加进去的糖」", font=F(34), fill=INK2)
footer(d, "2")
img.save(OUT + "n3_02_why.png")

# 图3 正确姿势
img, d = canvas()
d.text((80, 96), "抗糖的正确姿势", font=F(76, bold=True), fill=INK)
d.text((80, 208), "就三条，不花什么钱", font=F(40), fill=INK2)
items = [
    ("1", "控添加糖，≤25g/天", ["含饮料、奶茶、酱料、甜品", "先从「下午那杯」下手"]),
    ("2", "主食换一半全谷杂豆", ["不是戒碳水，是换来源", "餐后血糖更稳，不容易犯困"]),
    ("3", "别买抗糖丸", ["口服抗糖产品证据薄弱", "外用护肤在真皮层浓度不够"]),
]
y = 330
for num, t, descs in items:
    d.rounded_rectangle([80, y, W-80, y+250], radius=28, fill=WHITE, outline=LINE, width=2)
    d.ellipse([130, y+60, 220, y+150], fill=DAY if num != "3" else NIGHT)
    tw = d.textlength(num, font=F(56, bold=True))
    d.text((175-tw/2, y+78), num, font=F(56, bold=True), fill=WHITE)
    d.text((270, y+52), t, font=F(50, bold=True), fill=INK)
    for j, s in enumerate(descs):
        d.text((270, y+124 + j*50), s, font=F(33), fill=INK2)
    y += 290
d.rounded_rectangle([80, 1300, W-80, 1440], radius=24, fill=TINT)
d.text((120, 1332), "苏有朋 40 岁后低碳抗糖（他自己说的）——", font=F(34, bold=True), fill=INK)
d.text((120, 1385), "人家同时每天健身 2 小时，别只学一半", font=F(32), fill=INK2)
footer(d, "3")
img.save(OUT + "n3_03_how.png")

# ================= 笔记四 · 医美 =================
# 图1 封面
img, d = canvas()
d.text((80, 150), "种草帖只告诉你项目名，", font=F(52), fill=INK2)
d.text((80, 230), "没人告诉你代价。这篇补上", font=F(52), fill=INK2)
d.text((80, 380), "医美避坑：", font=F(120, bold=True), fill=INK)
d.text((80, 530), "四类项目", font=F(120, bold=True), fill=INK)
d.text((80, 680), "的原理和代价", font=F(120, bold=True), fill=DAY)
rows = [("热玛吉", "真皮层"), ("超声炮", "筋膜层"), ("水光针", "浅层补水"), ("填充", "容量补充")]
y = 950
for t, sub in rows:
    d.rounded_rectangle([80, y, W-80, y+96], radius=22, fill=WHITE, outline=LINE, width=2)
    d.text((140, y+22), t, font=F(42, bold=True), fill=INK)
    d.text((320, y+28), sub, font=F(34), fill=INK2)
    y += 112
d.text((80, 1460), "不推荐任何项目，只讲公开常识", font=F(38, bold=True), fill=DAY)
footer(d, "1")
img.save(OUT + "n4_01_cover.png")

# 图2 四类项目表
img, d = canvas()
d.text((80, 96), "四类项目，各自要付的代价", font=F(70, bold=True), fill=INK)
d.text((80, 200), "维持期和风险都来自公开医学常识", font=F(36), fill=INK2)
rows = [
    ("热玛吉", "真皮加热刺激胶原 · 维持约1年", "痛感明显，效果看操作者"),
    ("超声炮", "作用到筋膜层 · 维持约1年", "凹陷、神经损伤与操作强相关"),
    ("水光针", "浅层补水 · 维持3–6个月", "满脸针孔、3–7天恢复期"),
    ("填充", "补容量 · 维持6–12个月", "过度填充=「橡皮人」；血管栓塞是严重并发症"),
]
y = 300
for t, a, b in rows:
    d.rounded_rectangle([80, y, W-80, y+230], radius=28, fill=WHITE, outline=LINE, width=2)
    d.text((130, y+30), t, font=F(50, bold=True), fill=INK)
    d.text((130, y+100), a, font=F(34), fill=INK2)
    d.text((130, y+156), "代价：" + b, font=F(34), fill=NIGHT)
    y += 262
footer(d, "2")
img.save(OUT + "n4_02_table.png")

# 图3 三条纪律
img, d = canvas()
d.text((80, 96), "真要做，三条纪律", font=F(76, bold=True), fill=INK)
d.text((80, 208), "比选哪个项目重要", font=F(40), fill=INK2)
items = [
    ("1", "正规机构 + 执业医师", ["资质决定风险上限", "工作室、酒店房间再便宜也别去"]),
    ("2", "一次别叠多个项目", ["叠加=分不清哪个出的问题", "恢复期也错不开"]),
    ("3", "求自然，不求无纹", ["过度填充比不做更显老", "苏有朋收敛后网友反说更自然"]),
]
y = 330
for num, t, descs in items:
    d.rounded_rectangle([80, y, W-80, y+260], radius=28, fill=WHITE, outline=LINE, width=2)
    c = DAY if num != "3" else NIGHT
    d.ellipse([130, y+70, 220, y+160], fill=c)
    tw = d.textlength(num, font=F(56, bold=True))
    d.text((175-tw/2, y+88), num, font=F(56, bold=True), fill=WHITE)
    d.text((270, y+56), t, font=F(50, bold=True), fill=INK)
    for j, s in enumerate(descs):
        d.text((270, y+128 + j*52), s, font=F(33), fill=INK2)
    y += 300
d.text((80, 1360), "伊能静公开过满脸针孔的恢复期——", font=F(34), fill=INK2)
d.text((80, 1412), "她的公开是坦诚，不是推荐", font=F(34), fill=INK2)
footer(d, "3")
img.save(OUT + "n4_03_rules.png")

# ================= 笔记五 · 睡眠节律 =================
# 图1 封面
img, d = canvas()
d.text((80, 150), "「早睡」喊了十年没做到，", font=F(52), fill=INK2)
d.text((80, 230), "换个抓手：你能控制的是起床", font=F(52), fill=INK2)
d.text((80, 380), "早睡做不到，", font=F(112, bold=True), fill=INK)
d.text((80, 520), "先把起床点", font=F(112, bold=True), fill=INK)
d.text((80, 660), "钉死", font=F(112, bold=True), fill=DAY)
d.rounded_rectangle([80, 900, W-80, 1230], radius=28, fill=WHITE, outline=LINE, width=2)
d.text((130, 940), "为什么？", font=F(46, bold=True), fill=INK)
d.text((130, 1010), "几点睡着不由你（越焦虑越睡不着），", font=F(36), fill=INK2)
d.text((130, 1060), "几点起床完全由你", font=F(36), fill=INK2)
d.text((130, 1135), "起床点固定了，困意会自己找上门——", font=F(36), fill=INK2)
d.text((130, 1180), "这是节律的玩法，靠系统不靠意志力", font=F(36), fill=INK2)
d.text((80, 1330), "2017 年诺贝尔奖研究：什么时候做，和做什么一样重要", font=F(36, bold=True), fill=DAY)
footer(d, "1")
img.save(OUT + "n5_01_cover.png")

# 图2 怎么做
img, d = canvas()
d.text((80, 96), "三个动作，今晚就能开始", font=F(72, bold=True), fill=INK)
d.text((80, 208), "按顺序做，别跳步", font=F(40), fill=INK2)
items = [
    ("1", "定死起床点，周末也守", ["误差 30 分钟内", "先钉 7 天，再谈别的"]),
    ("2", "起床半小时内见自然光", ["拉开窗帘/出门走一圈", "光照是校准生物钟最强的信号"]),
    ("3", "睡前1小时降蓝光降室温", ["手机放远点，屋里调凉一点", "给身体一个「要检修了」的信号"]),
]
y = 330
for num, t, descs in items:
    d.rounded_rectangle([80, y, W-80, y+280], radius=28, fill=WHITE, outline=LINE, width=2)
    c = DAY if num != "2" else NIGHT
    d.ellipse([130, y+80, 220, y+170], fill=c)
    tw = d.textlength(num, font=F(56, bold=True))
    d.text((175-tw/2, y+98), num, font=F(56, bold=True), fill=WHITE)
    d.text((270, y+62), t, font=F(48, bold=True), fill=INK)
    for j, s in enumerate(descs):
        d.text((270, y+136 + j*52), s, font=F(33), fill=INK2)
    y += 320
footer(d, "2")
img.save(OUT + "n5_02_how.png")

# 图3 熬夜的账单
img, d = canvas()
d.text((80, 96), "熬夜的账单，不止黑眼圈", font=F(72, bold=True), fill=INK)
d.text((80, 208), "这笔账是怎么滚起来的", font=F(40), fill=INK2)
steps = [
    ("长期睡眠不足", "身体进入「应激」状态"),
    ("皮质醇升高", "压力激素持续在线"),
    ("胶原分解加快", "皮肤修复被按暂停"),
    ("食欲与血糖失控", "第二天更想吃高糖高油"),
]
y = 310
for i, (t, s) in enumerate(steps):
    d.rounded_rectangle([80, y, W-80, y+180], radius=28, fill=WHITE, outline=LINE, width=2)
    d.ellipse([130, y+66, 186, y+122], fill=NIGHT if i % 2 else DAY)
    d.text((230, y+34), t, font=F(46, bold=True), fill=INK)
    d.text((230, y+100), s, font=F(34), fill=INK2)
    if i < 3:
        d.text((148, y+180), "↓", font=F(40, bold=True), fill=INK2)
    y += 230
d.text((80, 1290), "目标 7–8 小时。刘德华晚 10 点睡早 6 点起四十年——", font=F(32), fill=INK2)
d.text((80, 1340), "你先学他的睡觉时间，再学他的其他", font=F(32), fill=INK2)
footer(d, "3")
img.save(OUT + "n5_03_bill.png")

print("notes 3/4/5 images done")
