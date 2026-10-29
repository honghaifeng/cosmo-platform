# 逆龄观察 · 内容数据底座

> 把六个抗衰 / 大健康品牌在小红书、抖音、百度、今日头条、视频号、新闻源上的公开内容，
> 抓下来、洗干净、验过、留档，再用**同一把尺子**给它们做横评 —— 每条结论都能点回原文。

这个仓库管**两个前端 + 一套数据**：

| 前端 | 谁在用 | 入口 | 说明 |
|---|---|---|---|
| **逆龄观察** | 选品 / 招商 / 外部协作方 | `/niling/` | 内容库浏览、单条详情、品牌横评、知识库、导出 |
| **六品牌工作台** | 品牌方 / 操盘手 / 内容运营 | `/wb/` | 六家同口径横评、内容清单、素材溯源、选题与发布 |

两个前端**共用同一份数据库、同一套采集流水线、同一批七牛媒体**。
它们不是两个产品，是同一条数据链的两个视角：一个用来"看明白"，一个用来"用起来"。

> **和星河 StarRiver 的关系**
> 星河（`server.js` 网关 + AI 内容工场）是**另一个独立项目、另一个仓库**。
> 本项目只做数据侧，不依赖星河也能单独跑（自己监听一个本机端口，部署环境是 `127.0.0.1:8098`）。
> 生产环境为了只暴露一个公网端口，由星河网关反代 `/niling/` 与 `/api/cb/*`，
> 并且把工作台静态文件放到星河的 `public/wb/` 下 —— 这是**部署时的组合**，不是代码依赖。
> 换句话说：把星河整个删掉，这个仓库照样起得来；反过来则不行。

---

## 目录

- [一分钟看懂](#一分钟看懂)
- [5 分钟跑起来](#5-分钟跑起来)
- [两个前端分别是什么](#两个前端分别是什么)
- [数据从哪来：四段式流水线](#数据从哪来四段式流水线)
- [数据边界：什么能对外，什么不能](#数据边界什么能对外什么不能)
- [媒体：七牛双空间](#媒体七牛双空间)
- [接口总表](#接口总表)
- [MCP 与技能包](#mcp-与技能包)
- [部署](#部署)
- [目录结构](#目录结构)
- [已知限制](#已知限制)
- [文档索引](#文档索引)

---

## 一分钟看懂

```
                    ┌──────────────── 公网唯一入口 :7777 ────────────────┐
                    │  星河网关 server.js（另一个仓库 / 另一个项目）      │
                    │    /          星河 AI 内容工场                     │
                    │    /wb/       → public/wb/ 静态文件（本项目发布）   │
                    │    /niling/   ┐                                    │
                    │    /api/cb/*  ┴→ 反代 127.0.0.1:8098               │
                    └────────────────────────┬───────────────────────────┘
                                             │
                    ┌────────────────────────▼───────────────────────────┐
                    │  底座 app.py（本项目，Python 标准库，零依赖）       │
                    │  只读打开 SQLite，所有数据出口都在这里              │
                    └────────────────────────┬───────────────────────────┘
                                             │
        ┌────────────────┬───────────────────┼──────────────────┬─────────────────┐
   db/cosmobounty.db  data/media_cdn.json  knowledge/      config/brands.json   七牛 chunke
   （唯一数据源）      （本地→七牛映射）    （文档+报告）    （六品牌判定规则）   （16.9GB 媒体）
```

三条不变量，破了任何一条，页面上的数字就不可信：

1. **只有一个数据库**。工作台和逆龄观察不各存一份快照 —— 曾经拆成三份，结果同一个品牌在三个页面上出现三个不同数字。
2. **只有一个判定口径**。六家品牌用同一套规则算声量、算均赞、算正文率，谁都不许给自己加严。
3. **只有 `verified=1` 的数据能出去**。未校验的数据留在库里，但被闸门挡在所有出口之外。

---

## 5 分钟跑起来

**前置**：Python 3.9+（不需要 pip install 任何东西）、一份数据库文件。

```bash
git clone <本仓库> && cd <本仓库>

# 1) 放数据库（仓库不带库，业务数据不进 Git）
mkdir -p db data
cp /path/to/cosmobounty.db db/cosmobounty.db

# 2) 起服务（零依赖；程序默认 8080，这里指到 8098，只监听本机）
COSMOBOUNTY_ROOT="$PWD" PORT=8098 python3 app.py

# 3) 打开
open http://127.0.0.1:8098/            # 逆龄观察浏览页
```

首次启动会自动生成一个 API token 写到 `data/token.txt`（权限 0600），
页面链接带上 `?k=<token>` 即可访问受保护接口。想固定 token 就设环境变量
`CB_TOKEN`，或在 `config.json` 里配 `sharecode`。

**单独看工作台**（它需要底座在跑）：

```bash
# 工作台是纯静态，随便一个静态服务器就够；它所有数据都从 /api/wb/* 取
python3 -m http.server 8099 --directory workbench
# 但 /api/wb/* 在底座上 → 开发时最省事的做法是让星河网关挂 public/wb/，
# 或者用浏览器插件把 /api/wb 指向 8098。生产上永远是星河网关带 /wb/。
```

**没有数据库也想看形态**：

```bash
sqlite3 db/cosmobounty.db < db/schema.sql   # 建空库，页面能打开，全部显示 0
```

---

## 两个前端分别是什么

### 逆龄观察（`/niling/`）

给**不做技术**的人看的：客户、招商对象、外部协作者。

| 能力 | 说明 |
|---|---|
| 按品牌 / 平台 / 时间浏览 | 5996 条内容，每条带发布时间、达人、互动数、原文链接 |
| 点开看原文 | 本地归档 + 七牛媒体，原帖删了也还能还原 |
| 品牌横评 | 六家同一把尺子：声量、均赞、正文率、强证据占比 |
| 知识库 | 品牌资料、产品事实卡、历史报告 |
| 导出 | 品牌宽表 / 打包 zip / 小红书归档 / 达人账号表 |

浏览页是服务端渲染的单文件 HTML（`index.html`），不打包、不构建。

### 六品牌工作台（`/wb/`）

给**天天做内容**的人用的：品牌操盘手、内容运营。

13 个文件、652KB、纯静态，无框架无构建。所有数据走 `<script src="/api/wb/*">`：
**base 改一行、刷新就生效**，不存在"改了数据要重新 build"。

| 页面 | 干什么 |
|---|---|
| `index.html` | 总览：六家声量、本周新增、异常提示 |
| `brand.html?brand=xxx` | 单品牌深度：平台分布、内容清单、达人结构 |
| `compare.html` | 横向对比：六家同屏，指标口径写在页面上 |
| `content.html` | 内容清单：逐条看、按条件筛、点回原文 |
| `review.html` | 复核队列：待核内容逐条判定 |
| `collect.html` | 采集状态：哪个平台、哪一轮、卡在哪 |
| `create.html` / `publish.html` | 选题与发布：从数据里找角度，写完登记 |
| `mobile.html` | 手机端精简视图 |
| `products.js` | 产品事实卡（成分、规格、价格口径），全站唯一来源 |
| `shared.css` / `shared.js` | 主题变量与公共组件 |

> 工作台以前是「构建期把 DB 烤成 `list-*.js` / `data.js` 静态快照」，
> 数据一变就得重跑脚本再 rsync，忘了跑就是旧数据。
> 现在改成按请求现算（`GET /api/wb/list.js?brand=mitoq`），
> 输出格式与旧静态文件**逐字节兼容**，前端一行没改。

---

## 数据从哪来：四段式流水线

```
① fetch   →  data/raw/<platform>/<date>.jsonl       原始响应，只落盘，不做任何业务判断
② clean   →  data/clean/<platform>/<date>.jsonl     每条带 accept/reject + reason
③ process →  data/enriched/<platform>/<date>.jsonl  下载媒体 + OCR/ASR 解读
④ ingest  →  db/cosmobounty.db                      唯一写库的地方，幂等
```

**为什么是四段**：旧链路是「边抓边入库」，脏数据先落库、靠闸门事后拦。
真出问题时看不出错在哪一段，只能全量重跑。现在每段都有可验收的中间产物，
任何一段能单独重跑、单独定位。

```bash
python3 crawler/pipeline.py --platform baidu --all       # 全链路
python3 crawler/pipeline.py --stages clean,ingest        # 只重跑某段
python3 crawler/pipeline.py --status                     # 各平台状态 + 三层产物统计
python3 crawler/pipeline.py --dry-run                    # 只质检不入库
```

业务判断（去重 / 归属 / 蹭词位 / 字段完整性）**全部集中在 clean 段**，且每条必须留 `reason`。

几条硬规则，都是踩出来的：

| 规则 | 为什么 |
|---|---|
| 「搜索词命中但正文没提品牌」= 蹭词位，不进品牌横评 | 曾把 1901 条这类脏数据混着展示，被当场质疑「跟品牌没关系，你是不是搞错了」 |
| 无正文的笔记不能直接排除 | 图片 OCR / 视频 ASR 里的信息要参与判定；媒体本身就是内容 |
| 缺发布时间不删，标待补 | 不在 clean 段误杀，留给 process 段补；但对外结论要标"该条时间待补" |
| 少而准、拟人路径、随机间隔、遇风控即冷却 | 硬冲两小时能把整条链路打挂 |
| CDN 媒体只能现采现下 | 图片直链约 24h 过期，事后补下必定 403 |

---

## 数据边界：什么能对外，什么不能

**`verified=1` 是唯一准入条件。** 未校验的数据留在库里，但完全隔离。

六个出口全部加闸，少一个就是泄漏口：

| 出口 | 加闸方式 |
|---|---|
| MCP 工具 | 查询里硬编码 `verified=1` |
| `POST /api/sql`（自由 SQL，最大泄漏口） | 用 CTE 把 `content` 包成只含 `verified=1` 的视图，再让用户 SQL 跑在上面 |
| HTTP API | 所有查询基座过滤 |
| 导出接口 / 导出 CSV / 归档页面 | 同基座过滤 |

每次数据变更后必须跑两个脚本：

```bash
python3 crawler/verify_and_gate.py       # 校验并打标 verified
python3 crawler/check_data_boundary.py   # 15+ 项边界守卫
```

三件事要分清，缺一不可：

- **覆盖率体检**（`health_check`）→ 看**缺多少**
- **校验**（`verify_xhs_data`）→ 看**对不对**
- **边界守卫**（`check_data_boundary`）→ 看**漏没漏**

> 「完整」和「正确」不是一回事。数据齐了不等于数据对，
> 数据对了不等于没漏出去。

---

## 媒体：七牛双空间

16.9GB、5988 个对象全在七牛，服务器不落地媒体。

| 空间 | 区域 | 域名 | 放什么 | 访问 |
|---|---|---|---|---|
| `chunke` | 华南 z2 | `tni044vir.hn-bkt.clouddn.com`（**只 HTTP**） | 六品牌公开素材：封面 / 正文图 / 视频 | 直接 URL |
| `sr-private` | 华东 z0 | `sr.pianai.love`（HTTPS） | 星河客户私有素材 | **必须签名** |

`data/media_cdn.json` 是唯一权威映射表（本地相对路径 → 七牛 key）。
新增媒体只跑脚本，不要手工拼 key：

```bash
python3 crawler/upload_qiniu.py          # 公开素材，全目录遍历，增量幂等
```

坑list（每条都真踩过，细节见 [docs/06-media.md](docs/06-media.md)）：

- 测试域名**只支持 HTTP**，`https://` 一律连不上；代码里强制降级。
- 对象 key 里的 `:` 上传时要换成 `_`，不换就 404。
- 私有空间签名签**完整 URL**，不是 path，结尾没有 `\n`。
- CDN 按 path 缓存且忽略 query，某 path 被缓存过 401 之后，带任何 token 都 401 —— 排查必须用没碰过的新 key 做对照。
- 七牛不认 ECDSA 证书，也不认 PKCS#8 私钥；签发必须 `--key-type rsa --rsa-key-size 2048`。

---

## 接口总表

程序默认端口 8080；部署环境固定 `127.0.0.1:8098`。
8080 被服务器上的 nginx 占着，别再抢它。

### 公开（无需 token）

| 路径 | 说明 |
|---|---|
| `GET /` `/index.html` | 逆龄观察浏览页 |
| `GET /health` | 存活探针 |
| `GET /api/stats` | 六品牌 × 平台 × 内容量总览 |
| `GET /api/list?brand=&platform=&limit=&offset=` | 内容清单 |
| `GET /api/item?id=` | 单条详情（含媒体解析、原始链接） |
| `GET /api/top?brand=&limit=` | 高互动内容 |
| `GET /api/compare?brands=` | 横评 |
| `GET /api/comments?content_id=` | 评论 |
| `GET /api/kb_docs` `/api/kb_doc` `/api/kb_media` | 知识库 |
| `GET /api/xhs_archive` `/api/xhs_creators` | 小红书归档 / 达人账号表 |
| `GET /api/export` `/api/raw_export` `/api/kb_export` | 导出 |
| `GET /cdn/<key>` | 七牛媒体同源代理（支持 Range，视频可拖进度） |
| `GET /api/wb/*` | 工作台实时数据层 |

### 需要 token

| 路径 | 说明 |
|---|---|
| `POST /api/sql` | 自由 SQL（闸门内），给 BI / 脚本 / 自建前端 |
| `GET /api/pending` | 待核队列（**故意不放公开**：返回 `verified=0`） |
| `POST /mcp` | MCP 远程协议端点 |

token 两种传法：请求头 `X-API-Key: <token>`，或查询串 `?k=<token>`（浏览页用这个，存 localStorage）。

### 工作台数据层（`/api/wb/*`，输出与旧静态文件逐字节兼容）

| 端点 | 输出 |
|---|---|
| `data.js` | 全局总览 |
| `list.js?brand=<slug>` | 单品牌内容清单 |
| `detail.js?brand=<slug>` | 单品牌深度数据 |
| `plat.js` / `people.js` / `contents.js` | 平台 / 达人 / 内容维度 |
| `media-cdn.js` / `media-index.js` / `media.json` | 媒体映射与落地统计 |
| `pending.js` | 待核队列 |

---

## MCP 与技能包

AI 客户端（WorkBuddy / Trae / Claude）可以直接接这个数据源，不用自己抓、自己算。

```bash
# 远程 MCP：POST /mcp（走网关就是 http://<host>:7777/api/cb/mcp 之外的同源路径）
# 或本地 stdio：mcp/cosmobounty_server.py
python3 mcp/cosmobounty_server.py
```

配置片段见 `mcp/mcp_config_snippet.json`。可用工具：`content_stats` / `search_content` /
`get_content` / `brand_compare` / `top_content` / `search_comments` / `sql_query` / `pending_content`。

`skills/cosmobounty-insight/` 是配套的技能包：告诉 AI 该怎么用这些数据回答
「这几个品牌最近谁声量高」「这条内容算不算真提到品牌」这类问题，
以及哪些话不许说（未校验数据、正负面占比、把品类场景词算进品牌声量）。

---

## 部署

```bash
DEPLOY_HOST=hzck@192.168.40.32 DEPLOY_SUDO_PASS=*** bash scripts/deploy.sh
bash scripts/deploy.sh --code    # 只推代码，不动数据库
bash scripts/deploy.sh --wb      # 只发布工作台前端（改前端最常用）
bash scripts/deploy.sh --check   # 只跑自测，不推任何东西
```

部署后服务器形态：

```
/data/apps/cosmobounty/
  ├── service/        app.py · wbdata.py · mcp/ · skills/ · crawler/
  ├── db/             cosmobounty.db（唯一数据源，含每日备份）
  ├── data/           media_cdn.json · token.txt
  ├── config/         brands.json · qiniu.json
  └── knowledge/      知识库文档（媒体不落盘）
/data/apps/starriver/public/wb/    ← 工作台静态文件（从本仓库 workbench/ 推过去）
```

systemd 单元 `deploy/niling.service`，只监听 `127.0.0.1:8098`。
公网只开 7777，靠星河网关反代。

**远端自测**：

```bash
bash scripts/deploy.sh --check
```

会对底座 3 个接口 + 网关 3 个入口逐个查 HTTP 200 且响应体 >30 字节 ——
只验 200 不够（空响应也可能 200）。

### 修「媒体未落地」的专用脚本

```bash
bash scripts/fix-niling-media.sh --dry-run   # 先看要改什么
bash scripts/fix-niling-media.sh             # 幂等，可重复跑
```

`app.py` 早期版本从 `service/data/` 找 `media_cdn.json`，部署脚本只往根 `data/` 推，
导致服务器上 5996 条内容的封面和视频**全部**被判「未落地」。
现在路径改成三级查找（环境变量 → 根 → 旧位置），这个脚本用来修老部署。

---

## 目录结构

```
app.py                 底座服务：浏览页 + JSON API + MCP，Python 标准库，零依赖
wbdata.py              工作台数据层：实时算 list/detail/媒体索引
index.html             逆龄观察浏览页（服务端不渲染，前端取数）
workbench/             六品牌工作台前端（纯静态，13 文件 652KB）
crawler/               采集与清洗流水线（95 个脚本，主线是 pipeline.py）
  ├── pipeline.py      四段式统一入口（fetch → clean → process → ingest）
  ├── verify_and_gate.py   校验并打标 verified
  ├── check_data_boundary.py  边界守卫
  └── upload_qiniu.py  媒体上传（增量幂等）
db/schema.sql          8 张表的结构（库本身不进仓库）
mcp/                   MCP 服务端 + 配置片段
skills/                技能包 cosmobounty-insight
config/brands.json     六品牌判定规则（关键词 / 竞品关系 / 数据归属）
scripts/               部署、证书、证书续期、打包、修数据
deploy/niling.service  systemd 单元
docs/                  7 篇设计文档
```

**仓库里没有的东西**（有意为之，别为了方便临时放行）：

| 没有 | 为什么 |
|---|---|
| `db/*.db` | 六家品牌的全部原始内容 + 客户名单，进 Git 等于公开 |
| `data/` 采集产物 | 同上 |
| `knowledge/media/` | 16.9GB，全在七牛 |
| `config/qiniu.json` · `config/aliyun.json` | 密钥 |
| `browser_data/` | 小红书/抖音登录态 |
| `data/token.txt` | 首次启动自动生成，每台机器不同 |

---

## 已知限制

| 限制 | 影响 | 现状 |
|---|---|---|
| `sentiment` 整列 100% 空 | 不能讲正负面占比 | 没有可信的情感判定模型，宁可不说 |
| `publish_time` 空 38% | 「近期声量」结论要打折 | 每日补抓中，缺失的标注为待补 |
| 百度数据不全 | 百度平台声量偏低 | 流水线已建，跑批中 |
| 小红书品类场景词 0 条 | 场景分析缺样本 | 每日补抓覆盖中，走独立轨道不进品牌横评 |
| 站外收录与回购证言缺失 | 用户问「有别人用吗」只能说实话 | 待补 |
| `daily_metric` 表为空 | 无日度趋势数据源 | 建表未跑数 |

口径纪律：**缺就写缺**。宁可数据少，不可数据脏，也不可把缺失包装成"暂无数据"。

---

## 文档索引

| 文档 | 讲什么 |
|---|---|
| [01 · 拓扑](docs/01-topology.md) | 两个前端为什么共用一个进程、一份数据；和星河怎么组合 |
| [03 · 工作台](docs/03-workbench.md) | 口径怎么算、为什么均赞比总赞有用 |
| [04 · 逆龄观察](docs/04-niling.md) | 原文可还原、SQL 闸门怎么改写查询 |
| [05 · 采集流水线](docs/05-pipeline.md) | 四段式、为什么业务判断必须集中在一段 |
| [06 · 媒体](docs/06-media.md) | 七牛双空间、五条硬规则（每条都是踩出来的） |
| [07 · 部署](docs/07-deploy.md) | 端口映射怎么判断、两个仓库怎么配合上线 |
| [08 · 数据模型](docs/08-data-model.md) | 8 张表、`verified` 是唯一准入、五条不变量 |

上游项目（星河）的文档在另一个仓库：`starriver/docs/`，10 篇。

---

## 许可

内部项目，未开源。这里的代码和口径归春科数据所有。
数据来自各平台公开内容，仅用于品牌内容分析与选品判断。
