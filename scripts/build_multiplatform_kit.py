#!/usr/bin/env python3
"""生成跨平台分发包：同一份数据 + 同一份技能，投放到 WorkBuddy / Trae / Claude 等
产出 exports/multiplatform/：
  niling-guancha-skill.zip   通用技能包（SKILL.md 标准，多平台通用）
  mcp-<平台>.json            对应平台的 MCP 配置（各自独立令牌）
  README.md                  分发说明 + 令牌表 + 迁移指引
用法：python3 scripts/build_multiplatform_kit.py
"""
import json
import os
import shutil
from urllib.parse import urlsplit, urlunsplit, parse_qsl

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL = os.path.join(PROJ, "skills", "cosmobounty-insight")
OUT = os.path.join(PROJ, "exports", "multiplatform")
SVC = json.load(open(os.path.join(SKILL, "scripts", "service.json"), encoding="utf-8"))
BASE = SVC["base_url"].rstrip("/")
_sp = urlsplit(BASE)
SCHEME_URL = urlunsplit((_sp.scheme, _sp.netloc, _sp.path, "", ""))
SHARECODE = dict(parse_qsl(_sp.query)).get("sharecode", "")

TOKENS = json.load(open(os.path.join(PROJ, "service", "data", "tokens.json"), encoding="utf-8")) \
    if os.path.exists(os.path.join(PROJ, "service", "data", "tokens.json")) else {}

os.makedirs(OUT, exist_ok=True)
shutil.copy(os.path.join(PROJ, "exports", "niling-guancha-skill.zip"),
            os.path.join(OUT, "niling-guancha-skill.zip"))

rows = []
for token, label in TOKENS.items():
    q = f"sharecode={SHARECODE}&k={token}" if SHARECODE else f"k={token}"
    cfg = {"mcpServers": {"niling-guancha": {"url": f"{SCHEME_URL}/mcp?{q}"}}}
    fn = f"mcp-{label}.json"
    json.dump(cfg, open(os.path.join(OUT, fn), "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    rows.append((label, token, f"{SCHEME_URL}/?{q}", f"{SCHEME_URL}/mcp?{q}"))

readme = f"""# 逆龄观察 · 跨平台分发

同一份数据服务、同一个技能包，可以同时投放到多个 AI 工作台。**数据只维护一份，各平台只是入口。**

## 一、三个层次的可移植性（重要）

| 层 | 是否锁定平台 | 说明 |
|----|------------|------|
| **数据服务** | ❌ 不锁定 | 纯 Python 标准库 + SQLite，`docker compose up` 就能在任何 VPS 跑起来；现在托管在 WorkBuddy 云只是「第一个落脚点」 |
| **接口（MCP）** | ❌ 不锁定 | MCP 是开放协议，WorkBuddy / Trae / Claude / Cursor 都支持 streamable HTTP |
| **技能（SKILL.md）** | ⚠️ 基本通用 | SKILL.md + frontmatter 已是事实标准，同一份包可直接放进各平台的技能目录；差异仅在安装位置和少量字段 |
| **专家 / 应用托管** | ✅ 锁定 | 专家包、应用发布是平台私有格式，跨平台要各做一份 |

策略：**核心能力放在前两层（服务 + SKILL.md），平台专属层只做适配，不做资产沉淀。**

## 二、每个平台一套令牌（已备好）

| 平台/用途 | 令牌 | 说明 |
|-----------|------|------|
{chr(10).join(f"| {l} | `{t}` | 独立吊销、可单独统计 |" for l, t, _, _ in rows)}

好处：某个平台的令牌泄露或要停用，只删 `service/data/tokens.json` 里那一行，其他平台不受影响。

## 三、各平台怎么接

### WorkBuddy
- 浏览器：<{rows[0][2] if rows else ''}>
- 技能：技能市场 → 添加技能 → 上传技能 → 选 `niling-guancha-skill.zip`
- 连接器（可选）：把 `mcp-workbuddy.json` 内容加进 MCP 配置

### Trae / Cursor / Claude Code（MCP 通用）
把 `mcp-trae.json` 的 `mcpServers` 段合并进对应客户端的 MCP 配置文件即可（纯 URL，无需请求头）。

### Claude Desktop
同上，但需要 `mcp-remote` 桥接远程 HTTP：
```json
{{"mcpServers": {{"niling-guancha": {{"command": "npx",
  "args": ["-y", "mcp-remote", "{rows[1][3] if len(rows) > 1 else ''}"]}}}}}}
```

### 不想装任何东西
```bash
curl "{rows[0][2] if rows else ''}&k=<token>"
```

## 四、把服务搬走（去平台化）

```bash
cd service
docker compose up -d          # 任意 Linux 机器，1C1G 够用
```

然后改 `skills/cosmobounty-insight/scripts/service.json`：把新地址加进 `endpoints` 数组（**保留云上那个做备份**）：

```json
{{
  "base_url": "{BASE}",
  "token": "<主令牌>",
  "endpoints": [
    {{"label": "自建", "base_url": "https://api.你的域名.com", "token": "<主令牌>"}},
    {{"label": "云托管", "base_url": "{BASE}", "token": "<主令牌>"}}
  ]
}}
```

技能会自动按顺序试，前一个挂了切下一个——已实测故障切换。重新打包发一次即可，之后朋友不用再动。

## 五、多平台上架前要确认的两件事

1. **各平台协议是否排他**：我查了公开文档，没有看到明确的独家/排他条款，但提交前请自己过一眼开发者协议——这类条款通常写在协议正文而不是文档页。
2. **不要复用同一个令牌跨平台**：一旦某平台审核要求你提供可测试的密钥，泄露范围就不可控。用上面的分平台令牌，出问题单独吊销。

## 六、数据安全边界（不变）

不上传：采集脚本、`browser_data/` 登录态、原始快照 `data/raw/`、主库全文（对外只暴露服务接口）。
"""
open(os.path.join(OUT, "README.md"), "w", encoding="utf-8").write(readme)

print(f"跨平台分发包：{OUT}")
for l, t, view, mcp in rows:
    print(f"  {l:14s} {t}")
print("  通用技能包 niling-guancha-skill.zip")
