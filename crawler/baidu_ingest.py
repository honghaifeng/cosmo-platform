#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""百度侧内容入库：WebFetch 通道实测可抓 SERP（2026-10-21）。链接为百度跳转链，浏览器可直接打开原文。"""
import hashlib, json, os, sqlite3, datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "db", "cosmobounty.db")
RAW = os.path.join(ROOT, "data", "raw", "baidu")
NOW = datetime.datetime.now().isoformat(timespec="seconds")
L = "http://www.baidu.com/link?url="

ITEMS = [
  # MitoQ —— SERP 被护肝片品类词生态占据
  dict(brand="MitoQ", st="横评", senti="pos", src="护肝胶囊评测", title="2025年护肝胶囊大评测! 8款热门产品功效解析",
       url=L+"A_4kX3gnBrXFTDdxNXVPPFSG6WH1e2bty7JjTDiiq54RuqQ-BaneEdQQ-gQTI9a4dQhJcHgz9NOveq-cbLEoFyCMFfgUqacKw59aKdRyqH7"),
  dict(brand="MitoQ", st="百度精选笔记", senti="pos", src="百度自有内容池(贴吧/百家号)", title="打工人的护肝片怎么选? 附MitoQ奶蓟草护肝胶囊3个月自用测评（29赞）",
       url=L+"A_4kX3gnBrXFTDdxNXVPPFSG6WH1e2bty7JjTDiiq54iBo7gv0ILvwgPTNGlUjhpXk-F22zq8KxjrAdFckNVrweyYt01sl2xekBHUxJxn5q"),
  dict(brand="MitoQ", st="横评", senti="mix", src="39健康网", title="2026年水飞蓟护肝品牌综合测评: MitoQ TOP5，但护肝特异性成分(水飞蓟宾)含量不突出",
       url=L+"vdKaM_mu3UC_17-MYBITJ_IF3nsIEEzm8yyOZKvOD_QdtGfUAJZhrcTDVrc9udD_aD6-9cLRB-edZNZt_mRjfa"),
  dict(brand="MitoQ", st="横评", senti="pos", src="知乎", title="护肝片哪个品牌更好更安全? 2026十大排行榜: MitoQ线粒体护肝片第三",
       url=L+"z__pEmoF_GYhgDktJPB3LWD0VPg-RbU_Oa6wdv06AjYtqXJQljMKHc1eiESPvfSX9VYWSJiKXehShG9cY7vcMK"),
  dict(brand="MitoQ", st="视频", senti="neu", src="B站(经百度视频聚合)", title="MitoQ 测评视频（06:59，哔哩哔哩）",
       url=L+"flDUb7WKt-ncufSRnbPEL9cgxfBddYm5RwoSxQfPdBocFaRVyoWbLFuv6fcCAqgXdFRT8rrLSMwDUBCrkejQ1K"),
  # GRANVER —— PR 渗透百度精选笔记与地方媒体
  dict(brand="GRANVER", st="PR", senti="pos", src="烟台在线", title="2025年评测GRANVER吉益眠深度评测: 睡眠修复+神经抗衰定位",
       url=L+"zWLr0A9NYbz7Q77Pg2dxN8bMDzkK43yodteRgdKNth71DtGKH-XVOt4F1z2zfmDsINbBV8TIGjUnJCf_jE8MeK"),
  dict(brand="GRANVER", st="PR", senti="pos", src="广西隆林网(政府类站点,同时投放塞乐瑞)", title="降低转氨酶产品哪个牌子好? 2026十款护肝横向评测: GRANVER吉利肝脱颖而出",
       url=L+"KEW9ki4aCDpSXCyQOuJ-9Xi04Cb2ObkglaMUtQkUho-5bUjXVJU-7TLMZRivaPFbEkffDb_mNeUy1OHa_4hz8q"),
  dict(brand="GRANVER", st="PR", senti="pos", src="39健康网", title="2026养肺保健品前10强测评: GRANVER吉清肺获FDA/GRAS/EFSA三重认证",
       url=L+"ESWFHX0Lm80ioO4yRqhUyA-RzE775_RgwqOSddjSy1fqA293-PQPNvW3OoB_dg8bAmX-9jvmVVJDNpR8UfaM9q"),
  dict(brand="GRANVER", st="百度精选笔记", senti="pos", src="百度自有内容池(营养保健品吧/百家号)", title="NMN哪个牌子最好? 2026NMN深度实测, 吉瑞维值得信",
       url=L+"1QOxLm1pOQpIeJxcDyTJwwut_RWREwM-p3aNz6k9uxXZdFUiM-CsU0THc_Ad9OTP"),
  # LAIFE —— 百度 AI 摘要给出完整品牌卡
  dict(brand="LAIFE", st="百度AI摘要", senti="pos", src="百度百科+百度搜索AI", title="LAIFE(乐梵)品牌卡: 美国Regenerative Bio旗下, 中国运营主体杭州悦泰健康, 全系2025销量+180%",
       url=L+"UeTVUyIjZMrabmCBdwtV98hzrftFUryG6r2Q8KgbOFzAB15VQFRAtoGX_nzYbnWHvlQ3ReJdFxWCTKYevVSuUK"),
  dict(brand="LAIFE", st="商品页", senti="neu", src="京东", title="LAIFE海外官方旗舰店(京东)",
       url=L+"bsXVBRukRq4N633tiGwW05LiBXj_AjdF5LBGQNwR-ntrDb7k5cQaMjQ9fpbFyYbYo_3IQ43hgrS6lj55hj3-vePLwnk9dl5tC_-7fdwY3pWQwJGQIkn87qKRZJSwWi9f2aGvtI4c8N00CA56UiQKdipGG7HXTUwU0IL3BzgIgNpZWUJip7MP_UdBZ9O-7Q5c0oIK98pfv79waF6ywszuVQ4ih0trY3qtC6JF6_O_zJ98CWcAodEEaOa1RBLiqfImLE-UIKo-sbpRXtsuhqHQUWrCUTaBGFkg5Qf3XyXHj5eO6YiRSla9YA7R49UFwcex-OoMJ0GwhyQzmhtoxa4E9smK0WeV1Kzb8i6uHMHXduFw2VLGyaT_E4e0Es0oWYG6vDHRujHc4IccK-2gyU8KmTbBU-gBdU6_L6lIXV8uB6P_Dh7TY-ZX2-9jhFcOceCPgWCTbeAQ2gPqUGO-71iQEo1fsGpbXfAZKw3suD-p29QRLVa7X6lttjcmPz8y-DmqHGFgvG5IYWNtNLjzINM7YGGrjRsJJ-OQsZ8wBKiR7r27w6CRfwcudSV_Sr7MYXulpUr79a9z7kCHuxioctlCI_"),
  # 塞乐瑞 —— 百度 AI 健康长文偏审慎
  dict(brand="塞乐瑞", st="百度AI摘要", senti="neg", src="百度健康AI聚合", title="百度AI: 赛乐瑞PRO单瓶5000元+, 公开成分信息少, 溢价主要来自营销包装, 性价比存疑",
       url=L+"E5QUj-RW9LtWa1YHrenKWzzWCoE_Pe6fpFqeaZxJMDrUq7e7EMUzUQ3DYfooXMRtfsdkcm2g5Evmz4cHOaiQuKOOcwxkC0SjTVTwLsN2ln4OTt38GUKa74oG4K8xXVORAVGiff4XQ0A3q6Tm1pJ2adgiZq6C1r1moIy7BR4bINNOIoUHgJPQH2Qy1fI9N8bcH8jgWQgb4WSqtpHeKVRsfhH6ZPwIWggoGLx3LQfAih0U7_YXeey7-YfYP1tPS2u-I8lQVJ0y-F2AofMEpxusBofdoMa8vzFfgRgDxvZJiqC"),
  # W+端粒塔 —— 百度 AI 摘要持保留态度
  dict(brand="W+端粒塔", st="百度AI摘要", senti="mix", src="百度百科+百度搜索AI", title="百度AI: W+端粒塔宣称诺奖团队/Nature Aging发文, 效果数据来自品牌自身研究, 建议理性看待",
       url="https://www.baidu.com/s?wd=W%2B%E7%AB%AF%E7%B2%92%E5%A1%94"),
]

def main():
    os.makedirs(RAW, exist_ok=True)
    con = sqlite3.connect(DB)
    n = 0
    for it in ITEMS:
        raw_file = os.path.join(RAW, f"{it['brand']}.json")
        arr = json.load(open(raw_file, encoding="utf-8")) if os.path.exists(raw_file) else []
        arr.append({**it, "collected_at": NOW})
        json.dump(arr, open(raw_file, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        h = hashlib.md5(it["url"].encode()).hexdigest()
        con.execute("""INSERT OR IGNORE INTO content
            (brand, platform, content_type, title, body, collect_time, url, sentiment, tags, raw_path, text_hash)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (it["brand"], "baidu", "article", it["title"], it["src"] + "｜" + it["st"], NOW,
             it["url"], it["senti"], it["st"] + ",src:" + it["src"], raw_file, h))
        n += con.total_changes
    con.commit()
    print(*con.execute("SELECT platform, COUNT(*) FROM content GROUP BY 1"), sep="\n")
    print(*con.execute("SELECT brand, platform, COUNT(*) FROM content GROUP BY 1,2 ORDER BY 1"), sep="\n")

if __name__ == "__main__":
    main()
