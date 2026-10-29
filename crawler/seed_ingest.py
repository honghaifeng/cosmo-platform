#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
种子数据入库：把第一轮公开检索到的 25 条内容写入 raw 层 + SQLite。
数据来源：2026-10-21 通过搜索索引抓取的公开网页快照（标题/摘要/链接）。
"""
import hashlib, json, os, sqlite3, datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "db", "cosmobounty.db")
RAW = os.path.join(ROOT, "data", "raw", "search")
NOW = datetime.datetime.now().isoformat(timespec="seconds")

ITEMS = [
  # ---- MitoQ ----
  dict(brand="MitoQ", platform="web", ctype="article", sentiment="pos",
       tags="姜黄素,肠胃,进口保健品", title="【评测】MitoQ官方姜黄素免疫胶囊养护胃肠道防御自护力60粒进口保健品",
       url="https://www.youfanerbuy.com/pingce/62821", body="种草向评测：姜黄素胶囊改善胀气、肠胃舒适度；强调MitoQ线粒体靶向技术提升吸收。"),
  dict(brand="MitoQ", platform="web", ctype="article", sentiment="pos",
       tags="护眼,公众号种草,回购", title="mitoq评价口碑好不好? mitoq在新西兰优缺点反馈测评（公众号）",
       url="http://mp.weixin.qq.com/s?__biz=MzkyNzE3OTk3OQ==&mid=2247486558&idx=2&sn=d04ae948cd524c5b475e8d3994f42fc4", body="公众号代购渠道内容：护眼胶囊两年回购叙事，眼压缓解体验。渠道：微信私域代购。"),
  dict(brand="MitoQ", platform="web", ctype="article", sentiment="pos",
       tags="辅酶Q10,横评榜单,技术叙事", title="2026年还原型辅酶Q10十大品牌选购指南（妙手商城）",
       url="https://m.miaoshou.com/news/PXKQxJdkjjQ1O6jW.html", body="行业横评第3位：三苯基膦阳离子靶向递送技术叙事，200+论文背书，差异化=买递送系统。"),
  dict(brand="MitoQ", platform="web", ctype="article", sentiment="pos",
       tags="辅酶Q10,盘点,科研背书", title="2026年辅酶Q10热门品牌年度盘点（医药网）",
       url="https://www.pharmnet.com.cn/health/2026/06/24/622077.html", body="奥塔哥大学+剑桥研发叙事，线粒体富集浓度数百倍，300+论文，定位高端抗衰。"),
  dict(brand="MitoQ", platform="baidu", ctype="article", sentiment="neg",
       tags="百度健康,实测负面,智商税", title="MitoQ保健品实测,值吗?（百度健康）",
       url="https://health.baidu.com/m/detail/ar_5019621343079582077", body="负面实测：三个月无体感，自称智商税；价格70+纽币/瓶约350元，同类仅20-30纽币；产品线约10款（护眼/护心/护脑/姜黄素等）。"),
  # ---- GRANVER ----
  dict(brand="GRANVER", platform="web", ctype="article", sentiment="pos",
       tags="官网,男性抗衰,专利叙事", title="一个品牌的坚守: GRANVER吉力威与成熟男性同行（品牌官网）",
       url="http://www.granverus.com/?id=23", body="官网叙事：20余项国际专利、7大植物成分、FDA GRAS+EFSA+EAU+SGS背书；日均成本<30元；京东上线48小时咨询超万人次，好评率99.3%-99.83%，复购85%+。"),
  dict(brand="GRANVER", platform="web", ctype="article", sentiment="pos",
       tags="PR软文,NMNH,横评榜单", title="2026抗衰保健品品牌盘点（亳州新闻网）",
       url="http://www.bozhou.cn/p/400557.html", body="PR软文：专利NMNH纯度98%+、检科院盲测前1%、15分钟入血、麦角硫因99.99%；京东健康品质优选；抖音Q1抗衰赛道增速居前。"),
  dict(brand="GRANVER", platform="web", ctype="article", sentiment="pos",
       tags="PR软文,男性健康,临床数据", title="口碑居前的补肾保健品牌子: 2026年度十大测评（生物谷）",
       url="https://news.bioon.com/article/83b895e07154.html", body="PR软文：男性35-65岁定位；12周双盲试验p=0.001/0.007；精子浓度+167%；压力评分-44%；价格带799-1199元/月。"),
  dict(brand="GRANVER", platform="web", ctype="article", sentiment="pos",
       tags="PR软文,NAD+路线", title="全球保健品选什么牌子靠谱? 2026年抗衰认知升级横评（中原经济网）",
       url="https://www.zyjjw.cn/zx/2026-10-15/36258.html", body="PR软文：NMNH路线（阿姆斯特丹大学、FASEB Journal）；晶型科技15分钟入血。"),
  dict(brand="GRANVER", platform="web", ctype="article", sentiment="pos",
       tags="PR软文,护肺,高价", title="2026十大护肺品牌硬核横评: GRANVER吉清肺登顶（团风县融媒体中心）",
       url="https://wap.hbystf.cn/p/51248.html", body="PR软文：吉清肺1299元/瓶、哈佛医学院+麻省总医院背景、3.1万例临床、好评99.9%复购99.8%。"),
  # ---- LAIFE ----
  dict(brand="LAIFE", platform="web", ctype="article", sentiment="pos",
       tags="PR软文,NMN,横评第一", title="2026 NMN选购白皮书: 十款热门品牌横评（山西新闻网）",
       url="https://www.sxrb.com/content/202605/21/c167238.html", body="PR软文：LAIFE乐悦泰断层第一；George Church等团队；99.97% β-NMN；8周NAD++42%；京东好评98.5%复购51%+。"),
  dict(brand="LAIFE", platform="web", ctype="article", sentiment="pos",
       tags="PR软文,器官衰老时钟", title="NAD哪个牌子效果好? 2026十大抗衰品牌实测盘点（中原融媒）",
       url="https://zyrm.zyjjw.cn/hangye/2026090402/162962.html", body="PR软文：哈佛博士周广宇+院士Gladyshev+许田；RevOrganAI平台；克利夫兰628例双盲。"),
  dict(brand="LAIFE", platform="web", ctype="article", sentiment="pos",
       tags="PR软文,高管圈层营销", title="NMN哪个牌子好? 2026年NMN十大品牌多维数据测评推荐榜（妙手网）",
       url="http://miaoshou.com/news/8wvGy1E8AZ7DN6lP.html", body="PR软文：97/100分第一；三重微囊包埋技术生物利用度+200%；季度装4440元；高管复购占比70%+。"),
  dict(brand="LAIFE", platform="web", ctype="article", sentiment="pos",
       tags="PR软文,护肝,进博会背书", title="2026护肝片十大领军品牌横评（亳州新闻网）",
       url="http://www.bozhou.cn/p/397360.html", body="PR软文：乐轻肝9.9/10；进博会+央视专访背书；益生菌+氨基酸+抗氧化三维配方。"),
  dict(brand="LAIFE", platform="web", ctype="article", sentiment="pos",
       tags="PR软文,护肝,器官抗衰", title="熬夜应酬伤肝普遍! 2026年十大品牌横向对比（中原融媒）",
       url="https://zyrm.zyjjw.cn/hangye/2026091007/163746.html", body="PR软文：90天改善脂肪肝宣称；13种组织衰老时钟模型。"),
  # ---- 塞乐瑞 ----
  dict(brand="塞乐瑞", platform="web", ctype="article", sentiment="pos",
       tags="PR软文,NMN,政府网站投放", title="NMN哪个品牌效果好? 2026年10款热门NMN深度测评（广西隆林网）",
       url="https://longlin.gov.cn/index.php?c=show&id=81268", body="PR软文（注意：发布于政府类网站）：赛乐瑞主打成分透明+锁活保鲜；多层复合防护包装。投放渠道激进化信号。"),
  dict(brand="塞乐瑞", platform="baidu", ctype="article", sentiment="mixed",
       tags="百度健康,真实口碑,AI聚合", title="用过赛乐瑞的人真实评价（百度健康AI聚合）",
       url="https://health.baidu.com/m/detail/ar_6493709508009215294", body="百度健康AI聚合1000+反馈：部分改善精力睡眠；3个月无感者众；35%用户提价格偏高；抗衰功效观点分化。罕见的平台级中性口碑聚合。"),
  dict(brand="塞乐瑞", platform="web", ctype="article", sentiment="pos",
       tags="PR软文,日系,敏感肌", title="2025年抗衰品牌个体化推荐（华夏经济网）",
       url="https://xhpy.cn/article/196311762086631.shtml", body="PR软文：日本品牌Cellerator；月销4万瓶、复购88%、京东4.8分；微粉化技术吸收+1.8倍。"),
  dict(brand="塞乐瑞", platform="web", ctype="article", sentiment="pos",
       tags="横评榜单,缓释技术", title="2026年主流抗衰老保健品横评, 10款NMN/NAD产品怎么选?（生物谷）",
       url="https://news.bioon.com/article/752f95556368.html", body="横评第7位：日本GENSEI株式会社，Cellerator细胞激活成分+24h缓释；约1688元/瓶。"),
  dict(brand="塞乐瑞", platform="web", ctype="article", sentiment="pos",
       tags="公众号,榜单营销", title="NMN哪个牌子口碑最好? 十大NMN产品排行（公众号）",
       url="http://mp.weixin.qq.com/s?__biz=Mzk0MTI1MDY5Mw==&mid=2247495577&idx=1&sn=5a12ec5fa08ab2125f4492f983b40d26", body="公众号榜单文：赛乐瑞第5位80.5分；线粒体靶向技术叙事。同文TOP1为「高活」等竞品，同渠道为多品牌投放软文。"),
  # ---- W+端粒塔 ----
  dict(brand="W+端粒塔", platform="web", ctype="listing", sentiment="neutral",
       tags="京东,商品页,高价", title="W+端粒塔净血通 日本进口水蛭素纳豆激酶（京东）",
       url="https://union-click.jd.com/jdc?e=&p=JF8BAS0JK1olWAQCVl9bCUMWAl8IGlgXXg8FXFlaDEoeBl9MRANLAjZbERscSkAJHTdNTwcKBlMdBgABFksWAG0LElwdWgEGVVdYFxJSXzI4T0ELJk90PBg_cxtBGStDZihqVEIYJFJROE8RAGkMGloUXjYCVV9cCUIfCmcKK18TMwdsVFdYZklAUG1dEwxGCAECAAxcW0wnA244WjUVXwcFVl9YAXsWM28OE1gRVAcGUVdUAEInA2cMK4y889CO8orkopylgV84K1glbTYCZF1tViUWUG9cTlwRD2heEwZcDxhUWgEIHFMXXQIDZFxcCUkVM184bgJ1XnMBAToIDwNnADlxSTB-PVhyVwgAeAJ_VDJodVlFHw93NDpadwJKRhVeE1N8LjYCZF1bAEIVM244", body="京东在售：净血通线 1899元/瓶（常卖价699.3）；品牌名W+duanlta；另有NMN主线。评论数仅2（该链接），高价低量信号。"),
  dict(brand="W+端粒塔", platform="web", ctype="article", sentiment="pos",
       tags="PR软文,NMN,诺奖叙事", title="NMN权威测评, 如果你只有精力试一个NMN品牌（凤凰网山东）",
       url="https://sd.ifeng.com/c/8razaibtMct", body="PR软文：W+端粒塔「全场景王者」；诺奖级团队、99.9%纯度、MitoMotion技术；WAAA临床推荐。"),
  dict(brand="W+端粒塔", platform="web", ctype="article", sentiment="pos",
       tags="PR软文,复购率,忠诚度", title="买NMN必看: 2026年千名用户真实测评（大河新闻网）",
       url="http://www.dahenews.cn/dhnews/565030194.html", body="PR软文：复购95.9%、好评99.5%；86.7%用户1周有感；TRINITY吸收系统；单瓶2000元+。"),
  dict(brand="W+端粒塔", platform="web", ctype="article", sentiment="pos",
       tags="PR软文,透明送检,临床数据", title="W端粒塔多维度深度测评（凤凰网山东）",
       url="https://sd.ifeng.com/c/8teNJ6EQPG5", body="PR软文：支持用户送检第三方；1200人双盲NAD+ +85.3%端粒+66.7%；全球500万瓶；京东复购95.91%好评99.54%。"),
  dict(brand="W+端粒塔", platform="web", ctype="article", sentiment="pos",
       tags="PR软文,类目销量冠军", title="2026国内外主流NMN品牌口碑榜: 7000用户调研（大河新闻网）",
       url="http://www.dahenews.cn/dhnews/270236620.html", body="PR软文：TOP1；京东国际NMN类目连续3年销量冠军；好评92%、复购95%+；代购仿品多提示。"),
]

def main():
    os.makedirs(RAW, exist_ok=True)
    con = sqlite3.connect(DB)
    con.executescript(open(os.path.join(ROOT, "db", "schema.sql"), encoding="utf-8").read())
    n = 0
    for it in ITEMS:
        raw_file = os.path.join(RAW, f"{it['brand']}.json")
        arr = []
        if os.path.exists(raw_file):
            arr = json.load(open(raw_file, encoding="utf-8"))
        arr.append({**it, "collected_at": NOW})
        json.dump(arr, open(raw_file, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        h = hashlib.md5(it["url"].encode()).hexdigest()
        con.execute("""INSERT OR IGNORE INTO content
            (brand, platform, content_type, title, body, publish_time, collect_time, url, sentiment, tags, raw_path, text_hash)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (it["brand"], it["platform"], it["ctype"], it["title"], it["body"], None, NOW,
             it["url"], it["sentiment"], ",".join(it["tags"]), raw_file, h))
        n += con.total_changes
    con.commit()
    print(f"ingested, total_changes={n}")
    for row in con.execute("SELECT brand, sentiment, COUNT(*) FROM content GROUP BY 1,2"):
        print(row)

if __name__ == "__main__":
    main()
