# 数据字典 · CosmoBounty 品牌内容库

导出日期：2026-10-22 ｜ 内容 279 条 ｜ 评论 922 条

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
