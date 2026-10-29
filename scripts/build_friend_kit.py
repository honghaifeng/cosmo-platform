#!/usr/bin/env python3
"""生成「逆龄观察 · 朋友接入包」：说明书 + 远程 MCP 配置片段 + 技能 zip

用法：python3 scripts/build_friend_kit.py
读取 skills/cosmobounty-insight/scripts/service.json 里的 base_url + token，
产出 exports/cosmobounty_friend_kit.zip：
  接入指南.md          —— 朋友照着做，三分钟接上
  mcp-remote-config.json —— WorkBuddy / Trae / Claude 加远程连接器的配置
  cosmobounty-insight-skill.zip —— 技能包（含内置地址与密钥的 service.json）
"""
import json, os, shutil, zipfile
from urllib.parse import urlsplit, urlunsplit, parse_qsl

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL = os.path.join(PROJ, "skills", "cosmobounty-insight")
SVC = json.load(open(os.path.join(SKILL, "scripts", "service.json"), encoding="utf-8"))
URL, TOKEN = SVC["base_url"].rstrip("/"), SVC["token"]
SHARECODE = SVC.get("sharecode") or dict(parse_qsl(urlsplit(URL).query)).get("sharecode", "")
_sp = urlsplit(URL)
BASE = URL if not _sp.query else urlunsplit((_sp.scheme, _sp.netloc, _sp.path, "", ""))
OUT = os.path.join(PROJ, "exports")
os.makedirs(OUT, exist_ok=True)

MCP_URL = f"{BASE}/mcp?sharecode={SHARECODE}&k={TOKEN}"
MCP_CFG = {"mcpServers": {"cosmobounty": {"url": MCP_URL}}}

GUIDE = f"""# 逆龄观察 · 接入指南

你拿到的是一份**每天自动更新**的大健康内容情报：MitoQ、GRANVER、LAIFE、塞乐瑞、W+端粒塔
五个品牌在小红书 / 抖音 / 百度 / 今日头条 / 新闻源的最新内容——像每天刷一次这个方向出了什么。

你**不需要自己抓数据**——数据在远程服务上，接入后 AI 直接查。

当前数据版本：<{BASE}/health?k={TOKEN}&sharecode={SHARECODE}>

## 方式一：装技能包（推荐，一步搞定）

1. WorkBuddy 左侧「专家·技能·连接器」→「技能」→ 右上「添加技能」→「**上传技能**」
2. 选 `cosmobounty-insight-skill.zip`，装完即用——**服务地址和密钥已内置，不用配连接器、不用填任何东西**
3. 直接问：

   > 今天这个方向有什么新动态？挑几条值得看的
   > LAIFE 和 MitoQ 在小红书的声量对比，剔除娱乐蹭词内容
   > 把「智商税」相关的评论原声都拉出来，按品牌分组
   > 出一份本周逆龄赛道周报

技能里带分析框架、数据边界规则、报告输出规范（HTML + 全量原文链接附录），任何人问出来的口径都一致。

## 方式二：浏览器直接看（不想装东西）

打开 <{BASE}/?k={TOKEN}&sharecode={SHARECODE}>（密钥已带在链接里），可筛选浏览全部内容、
点开单条看图文/评论/营销分析（SEO 分、GEO 分、关键词、合规风险）。

## 方式三：接远程 MCP（想用别的 AI 客户端时）

在 WorkBuddy / Trae / Claude 里加远程 MCP，地址照着 `mcp-remote-config.json` 填：
`{URL}/mcp?sharecode={SHARECODE}&k={TOKEN}`（密钥已在地址里，不需要请求头）。

## 不装任何东西？直接用 HTTP API / CLI

```bash
# 任意 HTTP 客户端，带请求头 X-API-Key: {TOKEN}
curl "{BASE}/api/stats?k={TOKEN}&sharecode={SHARECODE}"
curl "{BASE}/api/compare?platform=xhs&k={TOKEN}&sharecode={SHARECODE}"
curl "{BASE}/api/item?id=301&k={TOKEN}&sharecode={SHARECODE}"
```

技能包里的 `scripts/query.py` 也是零依赖 CLI：
`stats / compare / top / search / comments / pending / item / sql`。

## 注意

- 整条链接（含密钥）请勿外传，泄露后让提供方重置
- 数据每天 08:30 更新一次，分析前先看 `stats` 里的数据版本日期
- 抖音视频直链有时效，打不开不影响正文与数据分析
- 仅限内部竞品研究，勿公开传播评论原声与截图
"""

# ---- 打包技能（service.json 已内置真实地址）
skill_zip = os.path.join(OUT, "cosmobounty-insight-skill.zip")
tmp = os.path.join(OUT, "_skill_stage")
if os.path.exists(tmp):
    shutil.rmtree(tmp)
shutil.copytree(SKILL, tmp)
with zipfile.ZipFile(skill_zip, "w", zipfile.ZIP_DEFLATED) as z:
    for root, _, files in os.walk(tmp):
        for f in files:
            full = os.path.join(root, f)
            z.write(full, os.path.relpath(full, os.path.dirname(tmp)))
shutil.rmtree(tmp)

kit = os.path.join(OUT, "cosmobounty_friend_kit.zip")
guide_path = os.path.join(OUT, "接入指南.md")
open(guide_path, "w", encoding="utf-8").write(GUIDE)
mcp_path = os.path.join(OUT, "mcp-remote-config.json")
json.dump(MCP_CFG, open(mcp_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

with zipfile.ZipFile(kit, "w", zipfile.ZIP_DEFLATED) as z:
    z.write(guide_path, "接入指南.md")
    z.write(mcp_path, "mcp-remote-config.json")
    z.write(skill_zip, "cosmobounty-insight-skill.zip")

os.remove(skill_zip)
print(f"朋友接入包：{kit}")
print(f"  服务地址 {URL}")
print(f"  密钥     {TOKEN}")
