#!/bin/bash
# 把「逆龄观察 + 内容工作台」这个项目部署到公司服务器
#
# 本项目管辖的东西（部署后形态）：
#   /data/apps/cosmobounty/service/   底座服务（app.py，127.0.0.1:8098）
#   /data/apps/cosmobounty/{db,data,config,knowledge}   数据层：库 / 媒体映射 / 品牌配置 / 知识库
#   /data/apps/starriver/public/wb/   内容工作台前端（跑在星河网关的 /wb/ 下）
#
# 注意：星河（网关 7777）是**另一个仓库、另一个项目**，它的代码不在本仓库里。
# 本脚本只往它下面放一个目录（public/wb/），因为网关是公网唯一入口。
#
# 用法：
#   bash scripts/deploy.sh              # 全量（代码 + 数据 + 工作台）
#   bash scripts/deploy.sh --code       # 只推代码和前端，不动数据库
#   bash scripts/deploy.sh --wb         # 只发布工作台（改前端时最常用）
#   bash scripts/deploy.sh --check      # 只跑自测，不推任何东西
#
# 必需的环境变量（不写进仓库）：
#   DEPLOY_HOST       默认 hzck@192.168.40.32
#   DEPLOY_SUDO_PASS  服务器 sudo 密码（不加 --check 时必需）
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOST="${DEPLOY_HOST:-hzck@192.168.40.32}"
APP=/data/apps/cosmobounty          # 本项目在服务器上的根
SR=/data/apps/starriver             # 星河网关（另一个项目）
TS=$(date +%Y%m%d_%H%M)

MODE="${1:-all}"

SUDO_PASS="${DEPLOY_SUDO_PASS:-}"
if [ -z "$SUDO_PASS" ] && [ "$MODE" != "--check" ]; then
  read -r -s -p "服务器 sudo 密码（$HOST）：" SUDO_PASS; echo
  [ -z "$SUDO_PASS" ] && { echo "需要 sudo 密码才能重启服务"; exit 1; }
fi
export SUDO_PASS
say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
die() { printf '\033[31m失败：%s\033[0m\n' "$*"; exit 1; }

# 自测一律在服务器本机跑：走公网会撞上出口代理和端口映射，得到假 502
smoke() {
  say "自测（服务器本机）"
  ssh "$HOST" 'bash -s' <<'EOS'
set -uo pipefail
ok=0; bad=0
chk() { printf '  %-46s ' "$1"; code=$(curl -s -o /tmp/_sm -m 20 -w '%{http_code}' "$2"); sz=$(wc -c </tmp/_sm | tr -d ' ')
  if [ "$code" = "200" ] && [ "$sz" -gt 30 ]; then echo "OK  $code ${sz}B"; ok=$((ok+1)); else echo "FAIL $code ${sz}B"; bad=$((bad+1)); fi; }
chk "底座 /api/stats"          http://127.0.0.1:8098/api/stats
chk "底座 /api/wb/data.js"     http://127.0.0.1:8098/api/wb/data.js
chk "底座 /api/wb/list"        "http://127.0.0.1:8098/api/wb/list.js?brand=mitoq"
chk "底座 /niling/ 浏览页"      http://127.0.0.1:8098/
chk "网关 /api/cb/stats"       http://127.0.0.1:7777/api/cb/stats
chk "网关 /wb/"                http://127.0.0.1:7777/wb/
chk "网关 /wb/brand.html"      "http://127.0.0.1:7777/wb/brand.html?brand=MitoQ"
echo "  ---- 合计 OK=$ok FAIL=$bad"
[ "$bad" = "0" ] || exit 1
EOS
}

if [ "$MODE" = "--check" ]; then smoke; exit $?; fi

say "0. 通道检查"
ssh -o ConnectTimeout=8 -o BatchMode=yes "$HOST" 'echo ok' >/dev/null 2>&1 \
  || die "连不上 $HOST:22 —— 需要连公司网络/VPN，或让网管把 22 映射出来"

say "1. 目录与备份"
ssh "$HOST" "mkdir -p $APP/{service,db,data,config,logs} && \
  cp -f $APP/db/cosmobounty.db $APP/db/backup_$TS.db 2>/dev/null; echo done"

if [ "$MODE" != "--code" ] && [ "$MODE" != "--wb" ]; then
  say "2. 数据层（库 / 媒体映射 / 品牌配置）"
  if [ -f "$ROOT/db/cosmobounty.db" ]; then
    rsync -az "$ROOT/db/cosmobounty.db" "$HOST:$APP/db/cosmobounty.db" || die "数据库同步失败"
  else
    echo "  · 本地没有 db/cosmobounty.db（仓库本来就不带库），跳过 —— 服务器上的库保持不动"
  fi
  [ -f "$ROOT/data/media_cdn.json" ] \
    && rsync -az "$ROOT/data/media_cdn.json" "$HOST:$APP/data/media_cdn.json" \
    || echo "  · 本地没有 data/media_cdn.json，跳过（页面会把媒体判成未落地，注意）"
  [ -f "$ROOT/data/token.txt" ] && rsync -az "$ROOT/data/token.txt" "$HOST:$APP/data/token.txt"
  rsync -az "$ROOT/config/brands.json" "$HOST:$APP/config/" || die "品牌配置同步失败"
  [ -f "$ROOT/config/qiniu.json" ] && rsync -az "$ROOT/config/qiniu.json" "$HOST:$APP/config/"
  [ -f "$ROOT/config/aliyun.json" ] && rsync -az "$ROOT/config/aliyun.json" "$HOST:$APP/config/"

  if [ -d "$ROOT/knowledge" ]; then
    say "3. 知识库（只推文档，媒体在七牛上，不落盘）"
    rsync -az --delete \
      --exclude 'media/' --exclude '*.mp4' --exclude '*.jpg' --exclude '*.jpeg' \
      --exclude '*.png' --exclude '*.webp' \
      "$ROOT/knowledge/" "$HOST:$APP/knowledge/" || die "知识库同步失败"
  fi
fi

say "4. 底座服务代码（→ service/，只监听 127.0.0.1:8098）"
rsync -az --delete \
  "$ROOT/app.py" "$ROOT/wbdata.py" "$ROOT/index.html" "$ROOT/guide.md" \
  "$HOST:$APP/service/" 2>/dev/null || die "底座主程序同步失败"
for d in mcp skills; do
  [ -d "$ROOT/$d" ] && rsync -az --delete --exclude '__pycache__/' "$ROOT/$d/" "$HOST:$APP/service/$d/"
done
[ -d "$ROOT/crawler" ] && rsync -az --exclude '__pycache__/' "$ROOT/crawler/" "$HOST:$APP/service/crawler/"
[ -d "$ROOT/deploy" ] && rsync -az "$ROOT/deploy/niling.service" "$HOST:/tmp/niling.service"

ssh "$HOST" "echo '$SUDO_PASS' | sudo -S cp /tmp/niling.service /etc/systemd/system/niling.service 2>/dev/null && \
  echo '$SUDO_PASS' | sudo -S systemctl daemon-reload && \
  echo '$SUDO_PASS' | sudo -S systemctl enable --now niling >/dev/null 2>&1; \
  echo '$SUDO_PASS' | sudo -S systemctl restart niling && sleep 1.5 && \
  echo '$SUDO_PASS' | sudo -S systemctl is-active niling"

say "5. 内容工作台前端 → 星河 $SR/public/wb/"
[ -d "$ROOT/workbench" ] || die "找不到 workbench/（工作台前端源码）"
rsync -az --delete "$ROOT/workbench/" "$HOST:$SR/public/wb/" || die "工作台同步失败"
# 工作台是纯静态，改完不用重启网关（server.js 每次请求都读盘）；这里只验文件到位
ssh "$HOST" "test -f $SR/public/wb/index.html" || die "远端没有 $SR/public/wb/index.html"

# 最后比一次文件指纹：只验 HTTP 200 抓不到「推了但没生效」
say "6. 工作台文件一致性"
lh=$(shasum -a 256 "$ROOT/workbench/index.html" | awk '{print $1}')
rh=$(ssh "$HOST" "sha256sum $SR/public/wb/index.html 2>/dev/null | awk '{print \$1}'")
printf '  %-46s ' "public/wb/index.html"
if [ -n "$rh" ] && [ "$lh" = "$rh" ]; then echo "OK"; else
  echo "FAIL"; die "工作台没推上去（本地 ${lh:0:12} / 远端 ${rh:0:12}）"
fi

smoke
say "完成。入口：http://<公网IP>:7777/wb/  ·  /niling/"
