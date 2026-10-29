# 04 · 逆龄观察

`base/app.py`（1536 行）+ `base/index.html`（755 行）· Python 标准库 · 零前端框架

## 它给谁用

不看工作台的人：选品的、招商的、外部协作方。

他们不需要横评，不需要图表，只需要三件事：

1. **今天有什么新的**
2. **这条东西的原文在哪**
3. **能导出成表发出去**

## 七个 tab

| tab | 名字 | 内容 |
|---|---|---|
| `brand` | 品牌 | 按品牌浏览，卡片流 |
| `raw` | 原始表 | 全字段表格，可直接核对 |
| `pending` | 待校验 | 未过校验的内容，**对外不可见** |
| `kb` | 知识库 | 品牌资料、产品文档 |
| `docs` | 文档 | 分析报告 |
| `lib` | 媒体库 | 图片 / 视频，**站内可播** |
| `report` | 报告 | 结构化分析 |

## 三条硬设计

### 1. 原文必须能回去

每条内容都带 `url`，点开就是原帖。这是产品能被信任的基础 ——
数据可以少，但不能是编的。

配套的归档层：3592 条小红书笔记快照存在 `service/archives/`，
原帖删了也能还原当时看到的页面。

### 2. 媒体站内可播

不跳转、不新开标签。视频直接 `<video>` 播，图片直接显示。

实现上走同源代理：

```
浏览器 → 底座 /cdn/media/MitoQ/video/xxx.mp4
       → 302 到七牛（或直接反代）
```

**为什么不在前端直接写七牛地址**：七牛测试域名只支持 HTTP，
如果页面将来上 HTTPS，直连会被混合内容拦掉。走同源代理后，
协议跟着页面走，这个问题就不存在了。

### 3. 只读

`app.py` 只有 1 个 POST 接口（`/api/sql`），其余全 GET。
数据库用 `mode=ro` 打开。要写数据只能走采集流水线。

## 鉴权：三层

| 层 | 谁 | 能看什么 |
|---|---|---|
| 公开 | 任何人 | `PUBLIC_GET` 里的 18 个端点 |
| 令牌 | 内部脚本 / MCP / 自建前端 | 加上 `/api/sql` |
| 分享码 | 拿到分享链接的人 | 等同只读浏览 |

令牌来源优先级：

```
CB_TOKEN 环境变量
  → base/data/token.txt
  → 首次启动随机生成并落盘（0600）
```

> 早期这里有个硬编码默认值。源码公开后那等于一个能直接调 `/api/sql` 的
> 后门 —— 谁 clone 下来跑、或者线上忘了配，任何人拿它就能查全库。
> 现在没配置就随机生成，并且持久化，重启不变。

分享码同理：**必须显式配置**（`CB_SHARECODE` 或 `base/data/sharecode.txt`）才启用，
不配就不注册。写死在源码里等于把浏览凭证一起发出去。

## SQL 闸门：唯一的高危出口

`/api/sql` 和 MCP 的 `sql_query` 都能执行任意 SELECT。这是最大的风险面 ——
不处理就能绕过所有其它接口的 `verified=1` 过滤，查到未校验的行。

`apply_gate()` 的做法不是黑名单，是**改写查询**：

```python
# 原始 SQL
SELECT * FROM content WHERE brand='MitoQ'

# 改写后
WITH _gc AS (SELECT * FROM content WHERE verified=1),
     _gm AS (SELECT * FROM comment WHERE content_id IN
             (SELECT id FROM content WHERE verified=1))
SELECT * FROM _gc WHERE brand='MitoQ'
```

把受控表换成只含已校验行的 CTE，同时对 `comment` 连带过滤
（评论必须挂在已校验内容上）。这样：

- 不破坏原 SQL 结构（JOIN、子查询、聚合都还能用）
- 覆盖面是「表」不是「关键词」，写法再花也绕不过
- 原 SQL 里如果自己写了 `WHERE verified=0`，也查不到东西

验证：

```
select count(*) from content                     → 3034（不是 5996）
select verified,count(*) n from content group by verified → 只有 verified=1
```

## MCP：让外部协作者零操作

底座同时是 MCP server，13 个工具。外部协作者装一个技能包
（`base/skills/cosmobounty-insight/`），问一句「MitoQ 这个月有什么新内容」
就能拿到口径一致的回答。

**为什么给 MCP 而不是给数据库**：给数据库等于让对方自己写 SQL，
每个人算出来的口径都不一样，最后又回到「A 说 3000、B 说 800」。
给工具，工具里写死了口径。

| 工具 | 口径 |
|---|---|
| `content_stats` | 已校验内容 + 平台分布 + 品牌对比 |
| `brand_compare` | 六家同规则横评 |
| `top_content` | 按互动排序的头部内容 |
| `search_content` | 全文检索（正文 + 媒体文本） |
| `search_comments` | 评论检索 |
| `brand_knowledge` | 品牌知识库 |
| `analysis_report` | 结构化分析 |
| `sql_query` | 自由 SQL（**已过闸门**） |
| `pending_content` | 待校验（仅内部） |

## 已知缺口

| 缺口 | 影响 |
|---|---|
| `index.html` 755 行单文件内嵌 JS | 改起来不轻松，该拆 |
| 无情感分析（`sentiment` 全空） | 不能讲正负面占比 |
| `publish_time` 空 38% | 「最近一周」类结论要打折 |
| 未打标 3127 条 | `pending` tab 会很长 |
