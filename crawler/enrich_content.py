#!/usr/bin/env python3
"""回填媒体字段 + 生成营销策略分析（关键词/SEO/GEO/内容性质/合规风险）
运行：tools/MediaCrawler/.venv/bin/python crawler/enrich_content.py
"""
import sqlite3, json, os, re, sys, datetime

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(BASE, "db", "cosmobounty.db")
XHS = os.path.join(BASE, "tools/MediaCrawler/data/xhs/jsonl")
DY = os.path.join(BASE, "tools/MediaCrawler/data/douyin/jsonl")

import jieba
import jieba.analyse

INGREDIENTS = ["姜黄素", "辅酶Q10", "辅酶q10", "NMN", "麦角硫因", "线粒体", "端粒", "PQQ", "白藜芦醇",
               "花青素", "硒", "胶原蛋白", "赖氨酸", "米糠脂肪烷醇", "岩藻黄质", "水飞蓟", "谷胱甘肽",
               "维生素", "益生菌", "鱼油", "烟酰胺", "硫辛酸", "虾青素"]
EFFICACY = ["抗衰", "护肝", "养肝", "抗炎", "助眠", "睡眠", "修复", "代谢", "免疫", "抗氧", "抗氧化",
            "护巢", "卵巢", "气色", "疲劳", "氧化应激", "脂肪肝", "解酒", "排毒"]
INTENT = ["测评", "怎么样", "推荐", "对比", "多少钱", "值不值", "有用吗", "效果", "智商税", "回购", "踩雷"]
CTA = ["私信", "链接", "旗舰店", "优惠", "下单", "购买", "评论区", "小黄车", "直播间", "戳"]
ABSOLUTE = ["最", "第一", "唯一", "冠军", "顶级", "彻底", "根治", "100%", "永久"]
MEDICAL = ["治疗", "治愈", "抗癌", "降三高", "降血糖", "降血压", "处方", "药效", "临床治疗"]
HYPE = ["倍", "%", "翻倍", "逆龄", "抗老"]

def keywords(text, topk=8):
    text = (text or "").strip()
    if len(text) < 10:
        return []
    tags = jieba.analyse.extract_tags(text, topK=topk)
    return [t for t in tags if len(t) > 1][:topk]

def hits(text, words):
    t = (text or "").lower()
    return [w for w in words if w.lower() in t]

def seo_score(title, body, tags):
    s, notes = 0, []
    t = title or ""
    if any(k.lower() in t.lower() for k in INGREDIENTS + EFFICACY) or re.search(r"[A-Za-z]{3,}", t):
        s += 25; notes.append("标题含品类/成分词")
    if 10 <= len(t) <= 26:
        s += 15; notes.append("标题长度适中")
    elif len(t) > 40:
        notes.append("标题过长，搜索点击率受损")
    n_tags = len([x for x in (tags or "").split(",") if x.strip()])
    if n_tags >= 3:
        s += 20; notes.append(f"话题标签 {n_tags} 个，搜索入口多")
    elif n_tags:
        s += 10; notes.append(f"话题标签仅 {n_tags} 个")
    if hits(body, INTENT):
        s += 15; notes.append("正文覆盖搜索意图词")
    if re.search(r"\d+", t):
        s += 10; notes.append("标题含数字（清单感）")
    if len(title or "") < 8:
        notes.append("标题信息量不足")
    return min(s, 100), notes

def geo_score(title, body):
    s, notes = 0, []
    t = f"{title or ''} {body or ''}"
    ing = hits(t, INGREDIENTS)
    if ing:
        s += 30; notes.append("明确成分名：" + "、".join(dict.fromkeys(ing))[:40])
    if re.search(r"(研究|实验|临床|专利|论文|试验|数据|报告)", t):
        s += 20; notes.append("含研究/数据引用（AI 摘要素材）")
    if re.search(r"(是|为|意味着|说明|结论)[^。]{6,}", t or ""):
        s += 15; notes.append("有可抽取的结论句")
    if re.search(r"[？?]", t or "") or re.search(r"(怎么|如何|为什么|哪些)", t or ""):
        s += 15; notes.append("Q&A 句式，易被问答型引擎引用")
    if re.search(r"(每天|每次|坚持|周期|疗程|个月|周)", t or ""):
        s += 10; notes.append("含使用方式/周期等结构化信息")
    if re.search(r"(含量|mg|毫克|单位|纯度)", t or ""):
        s += 10; notes.append("含规格/含量数据")
    return min(s, 100), notes

def content_type(title, body, brand, url):
    t = f"{title or ''} {body or ''}"
    if brand and brand.lower() not in t.lower()[:200] and re.search(r"(AI|建模脸|颜值|明星|审美|八卦|公主|剧情)", title or "") and len(body or "") < 120:
        return "娱乐蹭词", "标题与产品无关，靠热点词吃搜索流量，品牌关联弱"
    if re.search(r"(记者|报道|发布会|宣布|融资|上市|战略合作|获评|入选)", t):
        return "PR 通稿", "新闻体叙事，重权威背书，弱用户视角"
    if re.search(r"(测评|实测|停服|吃了一年|用了半年|回购|空瓶|踩雷|红黑榜)", t):
        return "测评种草", "使用周期+主观感受，可信度叙事"
    if len(hits(t, INGREDIENTS)) >= 2 or re.search(r"(原理|机制|科学|成分)", t):
        return "成分科普", "成分机制切入，适合承接搜索/问答流量"
    if re.search(r"(姐妹|宝子|姐妹们都|建议|一定要|别再|快冲|码住)", t):
        return "口播种草", "强情绪+行动号召，转化导向"
    return "常规内容", "无明显套路特征"

def compliance(title, body):
    t = f"{title or ''} {body or ''}"
    risks = []
    ab = hits(t, ABSOLUTE)
    md = hits(t, MEDICAL)
    if ab:
        risks.append(f"绝对化用语（{'、'.join(dict.fromkeys(ab))[:24]}）—《广告法》第九条风险")
    if md:
        risks.append(f"医疗功效宣称（{'、'.join(dict.fromkeys(md))[:24]}）— 保健食品不得宣称治疗作用")
    if re.search(r"(逆龄|抗老|冻龄|细胞级|逆转)", t):
        risks.append("功效夸大倾向（逆龄/细胞级表述）")
    if re.search(r"(\d+\.?\d*倍|\+\d+\.?\d*%)", t):
        risks.append("量化效果宣称，需有可查依据")
    return risks

def load_media():
    """扫描 jsonl 目录下全部 search_contents_*.jsonl，后写的覆盖先写的（新数据优先）。"""
    media = {}
    import glob
    for d, plat in ((XHS, "xhs"), (DY, "douyin")):
        if not os.path.isdir(d):
            continue
        for fn in sorted(glob.glob(os.path.join(d, "search_contents_*.jsonl"))):
            for line in open(fn, encoding="utf-8"):
                line = line.strip()
                if not line:
                    continue
                try:
                    n = json.loads(line)
                except Exception:
                    continue
                nid = n.get("note_id") or n.get("aweme_id")
                if not nid:
                    continue
                if plat == "xhs":
                    imgs = [u.strip() for u in (n.get("image_list") or "").split(",") if u.strip()]
                    media[f"xhs:{nid}"] = {"images": imgs[:9], "video": n.get("video_url") or "",
                                           "cover": "", "url": n.get("note_url"),
                                           "type": n.get("type")}
                else:
                    media[f"douyin:{nid}"] = {"images": [], "video": n.get("video_download_url") or "",
                                              "cover": n.get("cover_url") or "",
                                              "url": n.get("aweme_url"),
                                              "type": "video"}
    return media

def main():
    con = sqlite3.connect(DB)
    con.execute("ALTER TABLE content ADD COLUMN media_json TEXT") if "media_json" not in [c[1] for c in con.execute("PRAGMA table_info(content)")] else None
    con.execute("ALTER TABLE content ADD COLUMN analysis_json TEXT") if "analysis_json" not in [c[1] for c in con.execute("PRAGMA table_info(content)")] else None
    media = load_media()
    only_missing = "--missing" in sys.argv
    sql = ("SELECT id, brand, platform, title, body, tags, url, text_hash, media_json "
           "FROM content")
    if only_missing:
        sql += " WHERE analysis_json IS NULL OR analysis_json = ''"
    rows = con.execute(sql).fetchall()
    n = 0
    for cid, brand, platform, title, body, tags, url, h, old_media in rows:
        m = media.get(h, {})
        if m.get("url"):
            con.execute("UPDATE content SET media_json=?, url=? WHERE id=?",
                        (json.dumps(m, ensure_ascii=False), m["url"], cid))
        elif m.get("images") or m.get("video") or m.get("cover"):
            con.execute("UPDATE content SET media_json=? WHERE id=?",
                        (json.dumps(m, ensure_ascii=False), cid))
        # 找不到素材时不覆盖已有的 media_json（避免把回填好的图片清空）
        text = f"{title or ''} {body or ''}"
        ctype, cnote = content_type(title, body, brand, url)
        ss, snotes = seo_score(title, body, tags)
        gs, gnotes = geo_score(title, body)
        analysis = {
            "keywords": keywords(text),
            "ingredients": list(dict.fromkeys(hits(text, INGREDIENTS))),
            "efficacy": list(dict.fromkeys(hits(text, EFFICACY))),
            "intent": list(dict.fromkeys(hits(text, INTENT))),
            "cta": list(dict.fromkeys(hits(text, CTA))),
            "content_type": ctype,
            "type_note": cnote,
            "seo": {"score": ss, "notes": snotes},
            "geo": {"score": gs, "notes": gnotes},
            "compliance": compliance(title, body),
            "title_len": len(title or ""),
            "tag_count": len([x for x in (tags or "").split(",") if x.strip()]),
        }
        con.execute("UPDATE content SET analysis_json=? WHERE id=?",
                    (json.dumps(analysis, ensure_ascii=False), cid))
        n += 1
    con.commit()
    print(f"已分析 {n} 条")
    print(*con.execute("SELECT json_extract(analysis_json,'$.content_type') t, COUNT(*) FROM content GROUP BY t ORDER BY 2 DESC"), sep="\n")
    print("有图片/视频素材:", con.execute("SELECT COUNT(*) FROM content WHERE media_json IS NOT NULL AND media_json != '{}'").fetchone()[0])
    con.close()

if __name__ == "__main__":
    main()
