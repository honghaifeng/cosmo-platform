# 05 · 采集流水线：四段式

`base/crawler/pipeline.py`（486 行）· 95 个采集脚本 · 每日 08:30

## 为什么改架构

旧链路是**边抓边入库**：抓到一条，判一下，写库。问题有三个：

1. **脏数据先落库**，靠闸门事后拦。拦掉的那部分还在库里，只是标记了，
   任何绕过闸门的查询都能看到。
2. **出问题看不出在哪一段**。稿子里的数字不对，是抓错了、还是判错了、
   还是入库时字段丢了？只能全量重跑逐个排查。
3. **不能单独重跑**。补一条正文要重抓整个平台。

新架构每段都有可验收的中间产物：

```
① fetch    data/raw/<平台>/<日期>.jsonl        只落盘，不做业务判断
② clean    data/clean/<平台>/<日期>.jsonl      每条带 accept/reject + reason
③ process  data/enriched/<平台>/<日期>.jsonl   媒体下载 + OCR/ASR
④ ingest   db/cosmobounty.db                  唯一写库处，幂等
```

任何一段可以单独重跑，任何一段的产物可以单独检查。

## 四段分别做什么

### ① fetch —— 只落盘

```bash
python3 base/crawler/pipeline.py --platform baidu --stages fetch
```

- 输入：平台、关键词、页数
- 输出：`data/raw/baidu/2026-10-29.jsonl`
- **不做任何业务判断**：不去重、不判归属、不检查字段完整性

为什么：抓取是最贵的（有风控、有频率限制），判断是最便宜的（本地 CPU）。
先保住原始数据，判断错了还能重判；如果抓的时候就过滤掉，错了就得重抓。

### ② clean —— 所有业务判断在这里

```bash
python3 base/crawler/pipeline.py --platform baidu --stages clean
```

- 输入：raw 层
- 输出：clean 层，每条带 `accept: true/false` + `reason`

判断项：

| 检查 | reason 示例 |
|---|---|
| 去重（按 `text_hash`） | `dup` |
| 品牌归属 | `no_brand_mention` |
| 蹭词位 | `tag_only` |
| 字段完整性 | `missing_title` |
| 时间缺失 | `no_publish_time`（**不 reject，只标记**） |

三条原则：

1. **所有业务判断集中在这一段**，别散在各处
2. **必须留 reason**，否则以后不知道这条为什么被拒
3. **「缺时间」不删只标记** —— 留给 process 段补，不在 clean 段误杀

第 3 条是踩过的：早期缺时间的直接 reject，后来发现很多平台的详情页里有时间、
只是列表页没有。已 reject 的没机会补，只能重抓。

### ③ process —— 媒体与文本

```bash
python3 base/crawler/pipeline.py --platform douyin --stages process
```

- 下载媒体到本地（**必须当下下，七牛/小红书的图片 URL 约 24h 过期**）
- 图片 OCR → `media_text`
- 视频 ASR → `media_text`

**为什么必须有这一步**：大量小红书笔记正文是空的，信息全在图里。
不做 OCR，这些内容只能判「无正文」，等于主动放弃一半数据。

媒体下载的时点问题是硬约束：**CDN 媒体只能现采现下**。
过了 24h 再补，图片 URL 一定 403，事后无解。

### ④ ingest —— 唯一写库处

```bash
python3 base/crawler/pipeline.py --platform baidu --stages ingest
```

- 幂等键：`text_hash`（等于 `uid`）
- 重跑不会产生重复行
- 字段级 upsert：只更新非空字段，不会把已有数据覆盖成 null

## 用法

```bash
# 全链路
python3 base/crawler/pipeline.py --platform baidu --all

# 只重跑某几段（不必从头来）
python3 base/crawler/pipeline.py --platform baidu --stages clean,ingest

# 各平台状态 + 三层产物统计
python3 base/crawler/pipeline.py --status

# 只质检不入库（改 clean 规则时用这个验证）
python3 base/crawler/pipeline.py --platform xhs --all --dry-run

# 页数与登录态
python3 base/crawler/pipeline.py --platform xhs --all --pages 5 --profile-dir ./browser_data
```

## 节奏与风控

**有节奏的采集，不做暴力抓取。**

| 约束 | 做法 |
|---|---|
| 随机间隔 | 请求之间有随机延迟，不是固定 sleep |
| 拟人路径 | 先首页 → 搜索页 → 详情页，不直接打接口 |
| 遇风控即冷却 | 检测到验证码/空结果就停，不硬冲 |
| 登录态复用 | `browser_data/` 持久化，不要让用户反复扫码 |

曾两小时硬冲把整条链路打挂（IP 被限），结论就一句：
**不要一下抓那么多。**

## 采集脚本 95 个，怎么找

按前缀分：

| 前缀 | 作用 |
|---|---|
| `baidu_*` | 百度 SERP 抓取（多个版本，`_v3` 最新） |
| `xhs_*` | 小红书（笔记、评论、媒体、登录） |
| `douyin_*` | 抖音（视频、评论、ASR） |
| `backfill_*` | 补采（按字段类型分：时间、正文、媒体、指标） |
| `analyze_*` | 分析（账号矩阵等） |
| `audit_*` | 体检（媒体完整性） |

补采脚本多的原因：每个字段的补采条件不同（有的只要列表页，有的要进详情页），
混在一个脚本里会让条件判断变成一团乱麻。

## 数据完整性三道检查

「完整」和「正确」不是一回事：

| 检查 | 回答什么问题 | 什么时候跑 |
|---|---|---|
| 覆盖率体检 | 还缺多少（按品牌/平台/字段） | 每次采集后 |
| 数据校验 | 数据对不对（抽查原文比对） | 每周 |
| 边界守卫（15+ 项） | 有没有漏出去（未校验数据外泄） | 每次改代码后 |

三者缺一不可。少了第一项不知道缺，少了第二项不知道错，少了第三项不知道漏。
