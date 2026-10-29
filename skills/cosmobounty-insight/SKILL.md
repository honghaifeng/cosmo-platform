---
name: cosmobounty-insight
display_name: 逆龄观察
display_name_en: Longevity Watch
description: 查询并分析五个大健康品牌（MitoQ、GRANVER、LAIFE、塞乐瑞、W+端粒塔）在小红书、抖音、百度、今日头条、新闻源的内容库。当用户想了解这些品牌近期有哪些新内容、声量谁高谁低、主打什么卖点、评论区在夸什么骂什么、谁在做 SEO/GEO 优化、哪些内容有广告法风险，或要看某个方向的最新动态归纳成日报时使用。数据由远程服务每日更新，使用者无需自己采集。触发词：逆龄观察、长寿赛道、抗衰品牌、NMN、麦角硫因、端粒塔、线粒体、竞品声量、小红书笔记、抖音内容、GEO 优化、智商税、日报。
description_zh: 五个大健康品牌的社媒内容库与分析框架，数据每日更新，直接查直接答，无需自己采集。
description_en: Daily-refreshed social content intelligence for five longevity/health supplement brands across Xiaohongshu, Douyin, Baidu and Toutiao, with SEO/GEO and compliance scoring.
category: research
version: 1.2.0
author: CosmoBounty
allowed-tools: Bash, Read, Write, WebFetch
---

# 逆龄观察

五个大健康品牌在**小红书 / 抖音 / 百度 / 今日头条 / 新闻源**的内容库：每条带全文、互动指标、营销分析（内容性质、SEO 分、GEO 分、关键词、广告法风险），评论原文单独成表。

**数据由提供方的远程服务每日更新（每天 08:30 采集入库）。使用者不需要、也不应该自己抓数据——直接查、直接分析。**

## 第一步：查数据（零配置，一条命令）

服务地址与密钥已内置在 `scripts/service.json`，装好就能用，**不需要配连接器、不需要填任何密钥**：

```bash
python3 scripts/query.py guide           # 拉最新分析口径（远程下发，优先于本文件）
python3 scripts/query.py stats          # 数据版本日期 / 内容量 / 平台与品牌分布 / 完整度
python3 scripts/query.py compare --platform xhs
python3 scripts/query.py top --brand LAIFE --platform douyin --by likes --limit 10
python3 scripts/query.py comments --query 智商税 --limit 30
python3 scripts/query.py item --id 301
python3 scripts/query.py sql "SELECT brand, COUNT(*) FROM content GROUP BY 1"
```

加 `--json` 或 `--csv` 切换输出格式。**优先用 CLI**，它是唯一必装依赖（Python 3 标准库，无需 pip）。

**口径以远程为准**：开始分析前先跑 `guide`，它会返回数据提供方最新维护的边界规则与报告规范。若 `guide` 失败（离线/服务变更），退回使用本文件内置的框架，不要因此拒绝回答。技能包因此可以长期不重装——数据和方法论都在服务端更新。

如果客户端另外接入了 `cosmobounty` 远程 MCP 连接器，也可直接用工具名调用（`content_stats` / `search_content` / `get_content` / `brand_compare` / `top_content` / `search_comments` / `pending_content` / `sql_query`）——这只是可选的加速方式，没装也不影响任何分析。

连接不上时：看报错。401 = 密钥失效，请提供方换新密钥；连不上 = 服务地址变了。**不要转而自己去爬数据，也不要凭印象编数据**。

## 第二步：先看数据边界，再下结论

**必须遵守的诚实性要求：**

- 小红书/抖音为**全文级**（正文 + 评论原文）；百度/头条/新闻源部分条目只有标题或摘要，引用时说明数据边界
- 样本是**关键词搜索 Top N**，不是全平台普查；表述用「本轮样本中」而非「全网」
- 头条站内自有文章 ≈ 0 是真实结论（国货品牌把预算押在站外 PR），不是抓取失败
- 抖音视频直链有时效，链接打不开不代表数据缺失（正文/指标已入库）
- 开头先声明数据版本日期，别把昨天的数据说成今天

## 分析框架（五个必做维度）

1. **声量与互动对比**：品牌 × 平台的内容数、总赞/藏/评。注意识别「虚假繁荣」——高赞笔记可能是娱乐蹭词内容（`nature` 字段为 `娱乐蹭词`），必须剔除后看真实产品内容
2. **内容性质结构**：`成分科普 / 测评种草 / 口播种草 / 娱乐蹭词 / PR 通稿 / 常规内容` 的分布，反映各品牌打法差异
3. **关键词与卖点**：TF-IDF 关键词 + 成分词（姜黄素、辅酶 Q10、NMN、麦角硫因、端粒、线粒体…），看谁的成分心智占位更牢
4. **口碑与痛点**：`comments` 检索「智商税 / 没用 / 回购 / 有效」，摘原声引用。负面往往不是「没效果」而是「说不清为什么值这个价」
5. **SEO / GEO 双维评估**：SEO 看站内搜索可见性（标题品类词、标签数、意图词），GEO 看被 AI 摘要引用的概率（成分名、研究数据、Q&A 结构、规格含量）。同一批内容可以 SEO 高但 GEO 低

用户问「今天有什么新动态」时，按**日报口径**答：先一句话总览（今天新增多少条、哪个品牌动作最大），再分 3–5 条要点（每条带品牌、平台、数据、原文链接），最后一句观察（这波动作在抢什么心智）。不要写成罗列清单。

## 报告输出规范（用户强制要求）

- 交付 **HTML 分析页**，带图带表，不要纯文字长报告
- **必须附全量原文链接附录**：按平台/品牌分组、按互动排序、每组默认 4 条 + 「展开更多」查看其余（`<details>` 实现）
- 每个版本独立文件（`report/brand_analysis_vN_YYYY-MM-DD.html`），**旧版本保留不覆盖**
- 数字要可溯源：区分「材料中的事实」与「你的推断」，推断单独标注
- 引用观点时附原文链接与互动数据

## 数据字典与常用查询

- 字段说明见 `references/schema.md`
- 12 条现成 SQL 见 `references/query_cookbook.md`（声量对比、虚假繁荣识别、GEO 高分内容、合规风险清单、负面评论等），用 `python3 scripts/query.py sql "..."` 执行

## 数据更新（维护者专用，使用者可跳过）

数据提供方在项目内跑 `bash scripts/daily_update.sh --collect`：增量采集 → 入库 → 补抓缺正文 → 重算分析 → 导出数据包 → 同步服务目录并重新发布。
小红书/抖音登录态已缓存，通常无需扫码；提示扫码时用小红书/抖音 App 扫，**建议小号**。已挂每日 08:30 自动更新。

使用者**不需要执行任何一条**，只需在分析时确认 `stats` 里的数据版本日期。

## 合规红线

仅采集公开内容、仅内部竞品研究使用；不存储用户个人信息；引用评论时保留原意、不拼接改写；涉及品牌负面结论须附原文依据。
