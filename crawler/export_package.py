#!/usr/bin/env python3
"""生成数据交付包：CSV + Excel + SQLite 副本 + 数据字典 + 使用说明
运行：tools/MediaCrawler/.venv/bin/python crawler/export_package.py
"""
import sqlite3, os, json, shutil, datetime
import pandas as pd

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(BASE, "db", "cosmobounty.db")
STAMP = datetime.date.today().isoformat()
OUT = os.path.join(BASE, "exports", f"cosmobounty_{STAMP}")
os.makedirs(OUT, exist_ok=True)

def load():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute("SELECT * FROM content")]
    comments = [dict(r) for r in con.execute(
        """SELECT c.id, c.content_id, ct.brand, ct.platform, c.body, c.like_count,
                  c.publish_time, ct.url AS source_url
           FROM comment c JOIN content ct ON c.content_id = ct.id""")]
    con.close()

    recs = []
    for r in rows:
        m = json.loads(r.get("metrics_json") or "{}")
        a = json.loads(r.get("analysis_json") or "{}")
        media = json.loads(r.get("media_json") or "{}")
        recs.append({
            "id": r["id"], "brand": r["brand"], "platform": r["platform"],
            "content_type": r["content_type"], "title": r["title"], "body": r["body"],
            "author": r["author"], "publish_time": r["publish_time"],
            "collect_time": r["collect_time"], "url": r["url"],
            "likes": m.get("likes", 0), "collects": m.get("collects", 0),
            "comment_count": m.get("comments", 0), "shares": m.get("shares", 0),
            "tags": r["tags"],
            "content_nature": a.get("content_type", ""),
            "seo_score": (a.get("seo") or {}).get("score"),
            "geo_score": (a.get("geo") or {}).get("score"),
            "keywords": ",".join(a.get("keywords") or []),
            "ingredients": ",".join(a.get("ingredients") or []),
            "efficacy_terms": ",".join(a.get("efficacy") or []),
            "compliance_risks": " | ".join(a.get("compliance") or []),
            "image_count": len(media.get("images") or []),
            "has_video": 1 if media.get("video") else 0,
        })
    return pd.DataFrame(recs), pd.DataFrame(comments)

def main():
    content, comments = load()
    content.to_csv(os.path.join(OUT, "content.csv"), index=False, encoding="utf-8-sig")
    comments.to_csv(os.path.join(OUT, "comment.csv"), index=False, encoding="utf-8-sig")

    # 汇总表
    summ = (content.groupby(["brand", "platform"])
            .agg(内容数=("id", "count"), 总赞=("likes", "sum"), 总藏=("collects", "sum"),
                 总评论=("comment_count", "sum"), 平均SEO=("seo_score", "mean"),
                 平均GEO=("geo_score", "mean"))
            .round(1).reset_index())
    summ.to_csv(os.path.join(OUT, "summary_brand_platform.csv"), index=False, encoding="utf-8-sig")

    daily = content.copy()
    daily["date"] = pd.to_datetime(daily["publish_time"], errors="coerce").dt.date
    daily = (daily.dropna(subset=["date"])
             .groupby(["brand", "platform", "date"])
             .agg(post_count=("id", "count"), engagement_total=("likes", "sum")).reset_index())
    daily.to_csv(os.path.join(OUT, "daily_metric.csv"), index=False, encoding="utf-8-sig")

    # Excel 汇总工作簿
    with pd.ExcelWriter(os.path.join(OUT, "cosmobounty_data.xlsx"), engine="openpyxl") as w:
        content.to_excel(w, sheet_name="内容库", index=False)
        comments.to_excel(w, sheet_name="评论库", index=False)
        summ.to_excel(w, sheet_name="品牌平台汇总", index=False)
        daily.to_excel(w, sheet_name="每日指标", index=False)

    # SQLite 副本
    shutil.copy2(DB, os.path.join(OUT, "cosmobounty.db"))

    open(os.path.join(OUT, "schema.md"), "w").write(f"""# 数据字典 · CosmoBounty 品牌内容库

导出日期：{STAMP} ｜ 内容 {len(content)} 条 ｜ 评论 {len(comments)} 条

## 文件清单
| 文件 | 用途 |
|---|---|
| cosmobounty.db | SQLite 主库，**推荐给 AI 工具/代码分析用**（可直接 SQL 查询） |
| content.csv | 内容明细（含正文全文、指标、营销分析） |
| comment.csv | 评论明细（关联 content_id） |
| summary_brand_platform.csv | 品牌×平台汇总 |
| daily_metric.csv | 品牌×平台×日期 的发布量与互动量 |
| cosmobounty_data.xlsx | 上述 4 张表的 Excel 合集（给不写代码的人） |

## content 表字段
| 字段 | 说明 |
|---|---|
| id | 主键 |
| brand | 品牌：MitoQ / GRANVER / LAIFE / 塞乐瑞 / W+端粒塔 |
| platform | xhs(小红书) / douyin(抖音) / baidu(百度) / toutiao(今日头条) / web(新闻源) |
| content_type | note(笔记) / video(视频) / article(文章) |
| title / body / author | 标题 / 正文全文 / 作者昵称 |
| publish_time / collect_time | 发布时间 / 采集时间 |
| url | 原文链接（可直接打开） |
| metrics_json | 互动指标 JSON：likes 赞 / collects 藏 / comments 评 / shares 转 |
| tags | 话题标签 |
| content_nature | 内容性质：成分科普 / 测评种草 / 口播种草 / 娱乐蹭词 / PR 通稿 / 常规内容 |
| seo_score | SEO 分 0-100（站内搜索可见性：标题品类词、标签数、搜索意图词等） |
| geo_score | GEO 分 0-100（被 AI 摘要/生成式引擎引用的概率：成分名、研究数据、Q&A 结构等） |
| keywords | TF-IDF 关键词 |
| ingredients | 识别出的成分词 |
| compliance_risks | 广告法合规风险（绝对化用语/医疗宣称/量化效果） |
| image_count / has_video | 素材情况 |

## comment 表字段
id / content_id（关联 content.id）/ brand / platform / body（评论原文）/ like_count / publish_time / source_url

## 常用查询
```sql
-- 各品牌小红书互动对比
SELECT brand, COUNT(*) n, SUM(likes) likes FROM content WHERE platform='xhs' GROUP BY brand ORDER BY likes DESC;
-- 高 GEO 分内容（易被 AI 引用）
SELECT brand, title, geo_score FROM content ORDER BY geo_score DESC LIMIT 20;
-- 风险内容清单
SELECT brand, title, compliance_risks FROM content WHERE compliance_risks != '';
-- 某品牌全部负面评论
SELECT c.body FROM comment c JOIN content t ON c.content_id=t.id
WHERE t.brand='MitoQ' AND (c.body LIKE '%智商税%' OR c.body LIKE '%没用%');
```
""")

    open(os.path.join(OUT, "README_怎么用.md"), "w").write(f"""# 这份数据怎么用

## 给用 WorkBuddy / Trae / Cursor 这类 AI 工具的朋友（推荐）
把整个文件夹丢给 AI，一句话说明：
> 这是 5 个大健康品牌在小红书/抖音/百度/今日头条的内容库，`cosmobounty.db` 是主库，`schema.md` 有字段说明。帮我分析各品牌内容策略差异。

AI 会直接用 SQLite 查询（无需你写代码）。数据量小（{len(content)} 条内容），单文件 SQLite 是最佳载体——AI 能精确 SQL 而不是猜 CSV 列。

## 给用 Excel / BI 的人
直接用 `cosmobounty_data.xlsx`，4 个工作表：内容库、评论库、品牌平台汇总、每日指标。

## 给写 Python 的人
```python
import sqlite3, pandas as pd
df = pd.read_sql("SELECT * FROM content WHERE platform='xhs'", sqlite3.connect("cosmobounty.db"))
```

## 注意
- 数据仅含公开内容，用于内部竞品研究；评论已去除用户 ID 等个人信息
- 小红书原文链接带访问凭证（xsec_token），过期需重新采集；正文已全文留存，不依赖链接存活
- 抖音视频直链有时效，过期后需重跑采集
""")

    print("导出完成:", OUT)
    for f in sorted(os.listdir(OUT)):
        print("  ", f, round(os.path.getsize(os.path.join(OUT, f)) / 1024, 1), "KB")

if __name__ == "__main__":
    main()
