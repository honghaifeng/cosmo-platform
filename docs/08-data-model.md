# 08 · 数据模型

`db/cosmobounty.db` · SQLite · 8 张业务表 · 2400 万字节
建表语句：[`base/db/schema.sql`](../base/db/schema.sql)

## 表清单与真实行数

| 表 | 行数 | 一句话 |
|---|---|---|
| `content` | 5,996 | 全平台内容主表 |
| `xhs_note` | 4,145 | 小红书笔记档案 |
| `comment` | 1,898 | 评论 |
| `media_text` | 1,715 | 图片 OCR + 视频 ASR 文本 |
| `creator` | 915 | 达人 |
| `crawl_log` | 334 | 采集日志 |
| `fetch_skip` | 78 | 补采跳过 |
| `daily_metric` | 0 | 建表未跑数 |

## content —— 主表

```sql
CREATE TABLE content (
  id           INTEGER PRIMARY KEY,
  brand        TEXT,      -- MitoQ / GRANVER / LAIFE / 塞乐瑞 / W+端粒塔 / COSMOBOUNTY
  platform     TEXT,      -- baidu / toutiao / xhs / douyin / shipinhao / web
  content_type TEXT,      -- note / video / article / ...
  title        TEXT,
  body         TEXT,      -- 正文，可能为空（信息在媒体里）
  author       TEXT,
  publish_time TEXT,      -- ⚠ 38% 为空
  collect_time TEXT,
  url          TEXT,      -- 原文链接，必须有
  metrics_json TEXT,      -- {"likes":..,"collects":..,"comments":..,"shares":..}
  sentiment    TEXT,      -- ⚠ 整列为空，未做情感分析
  tags         TEXT,      -- 逗号分隔，含 official 标记
  raw_path     TEXT,      -- 原始落盘路径
  text_hash    TEXT,      -- 幂等键，等于 uid
  media_json   TEXT,      -- {"images":[..],"video":"..","cover":".."}
  analysis_json TEXT,
  author_id    TEXT,
  verified     INTEGER,   -- ⭐ 对外准入：1 = 已校验
  verify_note  TEXT,
  pub_time_src TEXT        -- 时间从哪来的（列表页/详情页/推测）
)
```

### `verified` 是唯一的对外准入条件

用户规则：**只有校验过的数据才能对外展示。**

六个出口全部加闸：

| 出口 | 怎么拦 |
|---|---|
| MCP 工具 | 每个工具的 SQL 里写死 `WHERE verified=1` |
| `sql_query`（自由 SQL） | `apply_gate()` 改写成 CTE，把表替换成只剩 verified=1 |
| HTTP API | `PUBLIC_GET` 里的每个端点都带过滤 |
| 导出接口 | 同上 |
| 导出 CSV | 同上 |
| 归档页面 | 同上 |

`sql_query` 是最大泄漏口，因为用户可以写任意 SQL。靠黑名单不行
（写法太多），所以 `apply_gate()` 的做法是**把表换成视图**：

```sql
-- 用户写
SELECT * FROM content WHERE brand='MitoQ'
-- 实际执行
WITH _gc AS (SELECT * FROM content WHERE verified=1), ...
SELECT * FROM _gc WHERE brand='MitoQ'
```

这样不管 WHERE 怎么写，数据源本身就只有已校验的行。

### `text_hash` 是幂等键

采集重跑时靠它判断"这条已经有了"。等于 `uid`（内容唯一标识）。

**为什么不按 URL 去重**：同一篇内容在不同平台可能有不同 URL；
同一 URL 也可能因为带参数变化。`text_hash` 对标题+正文做哈希，更稳。

### `metrics_json` 而不是拆列

四个指标（赞、藏、评、转）放一个 JSON 里。理由：不同平台的指标集不一样
（小红书有"收藏"，抖音没有；抖音有"分享"，百度没有）。拆成列会有大量 NULL。

代价：每次算均赞都要 `json.loads`。实测 6000 条全解析约 40ms，可接受。

```python
def tri(rows):
    """[条数, 均赞, 爆文数]"""
    L = [likes_of(r["metrics_json"]) for r in rows]
    n = len(L)
    return [n, round(sum(L)/n) if n else 0, sum(1 for x in L if x >= 1000)]
```

## xhs_note —— 小红书档案

23 个字段，比 `content` 细。小红书是信息密度最高的平台，值得单独建表。

```sql
CREATE TABLE xhs_note (
  note_id        TEXT PRIMARY KEY,
  title, body, publish_time, author, author_id,
  images_json, tags_json,
  likes, collects, comments, shares,      -- 拆成列了（小红书指标固定）
  source_keyword,                          -- 通过什么搜索词找到的
  brand, brand_mention,                    -- 归属判定
  note_url, xsec_token,                    -- xsec_token 是访问凭证，会过期
  note_type, video_url,
  evidence,                                -- ⭐ 证据分级
  verified, verify_note
)
```

### `evidence` 三级

| 值 | 含义 | 进横评 |
|---|---|---|
| `strong` | 正文明确提到品牌 + 产品 | ✅ |
| `weak` | 图片 OCR / 视频 ASR 提到品牌 | ✅ |
| `tag` | 搜索词命中，正文和媒体都没提品牌（蹭词位） | ❌ |
| 空 | 还没打标 | ❌ |

实际分布：

| 状态 | 条数 |
|---|---|
| 空（未打标） | 3,127 |
| `strong` | 627 |
| `NULL`（采集时就没正文） | 206 |
| `tag` | 131 |
| `weak` | 54 |

**没打完标的不进横评**，所以工作台数字小于库里条数是正常的。

「蹭词位」（`tag`）是个真实存在的问题：搜「MitoQ 智商税」会命中大量讨论
抗衰但不提 MitoQ 的内容。这些内容对品类分析有价值，对品牌横评是噪音。
所以走独立轨道（场景分类 + 痛点提取），可以对外，但不进品牌对比。

## comment —— 评论

```sql
CREATE TABLE comment (
  id INTEGER PRIMARY KEY,
  content_id INTEGER,     -- 外键 → content.id
  ...
)
```

1,898 条，覆盖 252 条内容。评论数远小于内容数是因为：只有打开详情页才能拿评论，
而详情页抓取成本高（风控最严的一步）。

`apply_gate()` 对评论的处理是**连带过滤**：

```sql
_gm AS (SELECT * FROM comment
        WHERE content_id IN (SELECT id FROM content WHERE verified=1))
```

评论本身没有 `verified` 字段，它的准入取决于父内容。

## media_text —— 让无正文内容可用

```sql
CREATE TABLE media_text (
  ...
  source TEXT,     -- ocr / asr
  text   TEXT
)
```

1,715 条。

**为什么必须有这张表**：大量小红书笔记正文是空的（一句"看图"），
信息全在图里。不做 OCR 只能判"无正文"，等于主动放弃一半数据。

它同时也参与归属判定：一张图里有品牌 logo，OCR 出来了，这条就算 `weak` 证据。

## creator —— 达人

```sql
CREATE TABLE creator (
  platform TEXT,
  creator_id TEXT,
  PRIMARY KEY (platform, creator_id)   -- 复合主键
)
```

915 条。复合主键是因为同一个达人可能同时在小红书和抖音，`creator_id` 在
各平台独立发号，单列主键会撞。达人矩阵、官方号识别都靠这张表。

## crawl_log / fetch_skip

| 表 | 作用 |
|---|---|
| `crawl_log` | 每次采集的结果：`ok` / `empty` / `blocked` |
| `fetch_skip` | 主动跳过的内容（已知抓不到或不需要） |

`blocked` 的记录最有价值：能看到风控在什么时间、什么关键词上触发，
用来调采集节奏。

## 五条不变量

改代码时别破坏这些：

1. **`content.url` 不为空** —— 没有原文链接的内容无法核实，不对外展示
2. **`content.text_hash` 唯一** —— 幂等键，重复了说明 ingest 逻辑错
3. **`verified` 只有 0/1** —— 不要引入"部分校验"这种中间态
4. **`comment.content_id` 必须存在** —— 孤儿评论会在连带过滤时消失
5. **`metrics_json` 是合法 JSON** —— 解析失败一律当 0 处理，不抛异常

第 5 条是防御性的：一个字段的 JSON 坏掉，不应该让整个列表页 500。

## 已知数据缺口

| 缺口 | 影响 | 状态 |
|---|---|---|
| `sentiment` 全空 | 不能讲正负面占比 | 未做情感分析 |
| `daily_metric` 0 行 | 没有日度趋势 | 建表未跑数 |
| `publish_time` 空 38% | 「最近一周」要打折 | 部分平台列表页不返回时间 |
| 未打标 3,127 条 | 不进横评 | 每日补 |
| `xhs_note` 正文率 42%–44%（塞乐瑞/MitoQ） | 近一半只有元数据 | 每日 30 条/轮补 |

**这些数字对外说话前必须看。** 说"我们的数据很全"之前，
先想清楚对方问「那你能说说正面评价占比吗」怎么答。
