# 查询手册（12 条现成 SQL）

用 `python3 scripts/query.py sql "..."` 或 MCP 的 `sql_query` 工具执行。

## 1. 品牌 × 平台声量对比
```sql
SELECT brand, platform, COUNT(*) n,
       SUM(CAST(json_extract(metrics_json,'$.likes') AS INT)) likes
FROM content GROUP BY brand, platform ORDER BY brand, platform;
```

## 2. 剔除「娱乐蹭词」后的真实产品互动（识破虚假繁荣）
```sql
SELECT brand, platform,
       SUM(CAST(json_extract(metrics_json,'$.likes') AS INT)) 全部赞,
       SUM(CASE WHEN json_extract(analysis_json,'$.content_type')='娱乐蹭词' THEN 0
                ELSE CAST(json_extract(metrics_json,'$.likes') AS INT) END) 真实赞
FROM content WHERE platform IN ('xhs','douyin') GROUP BY brand, platform ORDER BY 真实赞 DESC;
```

## 3. 各品牌内容性质结构
```sql
SELECT brand, json_extract(analysis_json,'$.content_type') nature, COUNT(*) n
FROM content GROUP BY brand, nature ORDER BY brand, n DESC;
```

## 4. 成分心智占位（谁绑定了哪个成分）
```sql
SELECT brand, json_extract(analysis_json,'$.ingredients') ing, COUNT(*) n
FROM content WHERE json_extract(analysis_json,'$.ingredients') != '' GROUP BY brand, ing ORDER BY n DESC;
```

## 5. GEO 高分内容（最可能被 AI 摘要引用，可直接抄结构）
```sql
SELECT brand, platform, substr(title,1,40) title,
       CAST(json_extract(analysis_json,'$.geo.score') AS INT) geo, url
FROM content ORDER BY geo DESC LIMIT 20;
```

## 6. SEO 低分但互动高的内容（有流量没搜索承接，改造空间最大）
```sql
SELECT brand, platform, substr(title,1,40) title,
       CAST(json_extract(metrics_json,'$.likes') AS INT) likes,
       CAST(json_extract(analysis_json,'$.seo.score') AS INT) seo
FROM content
WHERE CAST(json_extract(analysis_json,'$.seo.score') AS INT) < 45
  AND CAST(json_extract(metrics_json,'$.likes') AS INT) > 500
ORDER BY likes DESC;
```

## 7. 广告法合规风险清单
```sql
SELECT brand, platform, substr(title,1,36) title, json_extract(analysis_json,'$.compliance') risks
FROM content WHERE json_extract(analysis_json,'$.compliance') != '[]' AND json_extract(analysis_json,'$.compliance') IS NOT NULL;
```

## 8. 负面口碑原声
```sql
SELECT t.brand, t.platform, c.body, c.like_count
FROM comment c JOIN content t ON c.content_id=t.id
WHERE c.body LIKE '%智商税%' OR c.body LIKE '%没用%' OR c.body LIKE '%没效果%' OR c.body LIKE '%后悔%'
ORDER BY c.like_count DESC;
```

## 9. 高赞评论里的真实关注点（按品牌）
```sql
SELECT t.brand, substr(c.body,1,50) body, c.like_count
FROM comment c JOIN content t ON c.content_id=t.id
WHERE c.like_count > 0 ORDER BY c.like_count DESC LIMIT 40;
```

## 10. 发布节奏（按月，看谁在密集投放）
```sql
SELECT brand, platform, substr(publish_time,1,7) ym, COUNT(*) n
FROM content WHERE publish_time IS NOT NULL GROUP BY 1,2,3 ORDER BY brand, ym DESC;
```

## 11. 站外 PR 投放网络（哪些媒体在同时接多个品牌的软文）
```sql
SELECT substr(url,1,instr(substr(url,9),'/')+8) host, COUNT(DISTINCT brand) brands, COUNT(*) n
FROM content WHERE platform IN ('toutiao','web','baidu') AND url LIKE 'http%'
GROUP BY host HAVING brands > 1 ORDER BY brands DESC, n DESC;
```

## 12. 待补全清单（缺正文的条目）
```sql
SELECT id, brand, platform, substr(title,1,40) title, LENGTH(COALESCE(body,'')) len
FROM content
WHERE platform IN ('baidu','toutiao','web') AND LENGTH(COALESCE(body,'')) < 220
ORDER BY len;
```

> 提示：`metrics_json` 的键是 `likes / collects / comments / shares`；`analysis_json` 的键是 `content_type / seo.score / geo.score / keywords / ingredients / efficacy / intent / cta / compliance / title_len / tag_count`。
