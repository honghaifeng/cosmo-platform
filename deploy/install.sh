#!/bin/bash
# 统一底座一键安装（在服务器上以 root 运行）
#
# 装完的形态：
#   127.0.0.1:8098   逆龄观察数据底座（只读 SQLite + 知识库 + 七牛媒体）
#   0.0.0.0:7777     星河网关：/  /wb/  /niling/  /api/cb  /api/wb
#
# 用法：sudo bash install.sh
#
# 设计原则：能不猜就不猜——星河装在哪儿、用哪个用户，全部从 systemd 读；
# 读不到才用约定值。任何一步失败立刻退出，不留半装状态。
set -uo pipefail
P="$(cd "$(dirname "$0")" && pwd)"
APP=${APP_DIR:-/data/apps/cosmobounty}
TS=$(date +%Y%m%d_%H%M)
say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
warn() { printf '\033[33m%s\033[0m\n' "$*"; }

[ "$(id -u)" = "0" ] || { echo "请用 root 运行：sudo bash install.sh"; exit 1; }

SR=$(systemctl show -p WorkingDirectory --value starriver.service 2>/dev/null)
[ -n "${SR:-}" ] && [ -d "$SR" ] || SR=/data/apps/starriver
RUNUSER=$(systemctl show -p User --value starriver.service 2>/dev/null)
[ -n "${RUNUSER:-}" ] && [ "$RUNUSER" != "root" ] || RUNUSER=hzck
echo "星河目录：$SR   运行用户：$RUNUSER"

say "1. 备份"
mkdir -p "$APP"/{db,data,config,knowledge,logs,"service"}
[ -f "$APP/db/cosmobounty.db" ] && cp -f "$APP/db/cosmobounty.db" "$APP/db/backup_$TS.db" && echo "  已备份数据库"
[ -f "$SR/server.js" ] && cp -f "$SR/server.js" "$SR/backup-server-$TS.js" && echo "  已备份 server.js"
[ -f "$SR/lib/sr-cdn.js" ] && cp -f "$SR/lib/sr-cdn.js" "$SR/lib/sr-cdn.js.bak-$TS" && echo "  已备份 sr-cdn.js"

say "2. 安装底座（代码 / 数据库 / 知识库文档 / 媒体映射）"
rm -rf "$APP/service.new"
cp -a "$P/cosmobounty/service" "$APP/service.new"
rm -rf "$APP/service.old"; [ -d "$APP/service" ] && mv "$APP/service" "$APP/service.old"
mv "$APP/service.new" "$APP/service"
cp -f "$P/cosmobounty/db/cosmobounty.db" "$APP/db/cosmobounty.db"
cp -f "$P/cosmobounty/data/media_cdn.json" "$APP/data/media_cdn.json"
cp -f "$P/cosmobounty/data/token.txt" "$APP/data/token.txt" 2>/dev/null
cp -f "$P/cosmobounty/config/"*.json "$APP/config/" 2>/dev/null
# 知识库只覆盖文档，媒体留在七牛（不落盘，省 16GB）
cp -a "$P/cosmobounty/knowledge/." "$APP/knowledge/" 2>/dev/null
chown -R "$RUNUSER:$RUNUSER" "$APP"
echo "  完成：$(du -sh "$APP" | cut -f1)"

say "3. 注册 systemd（niling.service → 127.0.0.1:8098）"
cp -f "$P/cosmobounty/niling.service" /etc/systemd/system/niling.service
sed -i "s/^User=.*/User=$RUNUSER/;s/^Group=.*/Group=$RUNUSER/" /etc/systemd/system/niling.service
systemctl daemon-reload
systemctl enable niling >/dev/null 2>&1
systemctl restart niling
sleep 2
systemctl is-active niling || { journalctl -u niling -n 30 --no-pager; exit 1; }

say "4. 更新星河网关（反代底座 + 动态工作台）"
cp -f "$P/starbox/server.js" "$SR/server.js"
mkdir -p "$SR/lib" "$SR/public/wb" "$SR/data"
cp -a "$P/starbox/lib/." "$SR/lib/"
rm -rf "$SR/public/wb"; mkdir -p "$SR/public/wb"
cp -a "$P/starbox/public/wb/." "$SR/public/wb/"
chown -R "$RUNUSER:$RUNUSER" "$SR/server.js" "$SR/lib" "$SR/public/wb"

# 4.5 私有素材签名配置 —— 漏了这一步，星河所有 AI 图/视频会 404
# （server.js 里 serveMedia 找不到配置就回落本地文件，而服务器不落地这批媒体）
say "4.5 私有素材（七牛签名配置）"
if [ -f "$P/starbox/data/sr-cdn.json" ]; then
  cp -f "$P/starbox/data/sr-cdn.json" "$SR/data/sr-cdn.json"
  chmod 600 "$SR/data/sr-cdn.json"
  chown "$RUNUSER:$RUNUSER" "$SR/data/sr-cdn.json"
  echo "  已安装 sr-cdn.json（$(wc -c <"$SR/data/sr-cdn.json" | tr -d ' ') 字节）"
else
  warn "  包里没有 starbox/data/sr-cdn.json —— 私有素材将回落本地并 404，请检查打包"
fi
# 4.6 网关必须知道底座在哪儿 —— 漏了就会 401
# （server.js 拿不到 CB_BASE 就不反代 /api/wb、/niling、/api/cb，
#   请求落到星河自己的鉴权上，页面全空）
say "4.6 网关指向底座（CB_BASE）"
mkdir -p /etc/systemd/system/starriver.service.d
cat > /etc/systemd/system/starriver.service.d/base.conf <<'EOF'
# 由 install.sh 写入：星河网关反代逆龄观察底座
[Service]
Environment=CB_BASE=http://127.0.0.1:8098
EOF
systemctl daemon-reload
echo "  已写入 /etc/systemd/system/starriver.service.d/base.conf"

# 服务器上 node 不在 PATH 里（装在 /data/apps/node/bin），从 unit 的 ExecStart 里取
NODE=$(systemctl show -p ExecStart --value starriver.service 2>/dev/null | grep -oE '/[^ ;]*/node' | head -1)
[ -n "${NODE:-}" ] && [ -x "$NODE" ] || NODE=$(command -v node || true)
if [ -n "${NODE:-}" ]; then
  ( cd "$SR" && "$NODE" -e "
    const c=require('./lib/sr-cdn');
    const s=c.stat();
    if(!s.ready){ console.log('✗ 签名配置未就绪（找不到 data/sr-cdn.json）'); process.exit(0); }
    const k=Object.keys(c.load().map)[0];
    const u=c.urlFor(k);
    console.log('✓ 私有 CDN host='+s.host+'  keys='+s.keys);
    console.log('  样例签名 …'+(u?u.slice(-42):'(null)'));
  " 2>&1 | sed 's/^/  /' )
else
  warn "  找不到 node，跳过签名自检"
fi

systemctl restart starriver
sleep 2
systemctl is-active starriver || { journalctl -u starriver -n 30 --no-pager; exit 1; }

say "5. 自测"
ok=0; bad=0
chk() { printf '  %-42s ' "$1"; code=$(curl -s -o /tmp/_sm -m 20 -w '%{http_code}' "$2"); sz=$(wc -c </tmp/_sm | tr -d ' ')
  if [ "$code" = "200" ] && [ "$sz" -gt 30 ]; then echo "OK  $code ${sz}B"; ok=$((ok+1)); else echo "FAIL $code ${sz}B"; bad=$((bad+1)); fi; }
chk "底座 /api/stats"        http://127.0.0.1:8098/api/stats
chk "底座 /api/wb/data.js"   http://127.0.0.1:8098/api/wb/data.js
chk "底座 /api/wb/list"      "http://127.0.0.1:8098/api/wb/list.js?brand=mitoq"
chk "网关 /api/ping"         http://127.0.0.1:7777/api/ping
chk "网关 /api/wb/data.js"   http://127.0.0.1:7777/api/wb/data.js
chk "网关 /api/cb/stats"     http://127.0.0.1:7777/api/cb/stats
chk "网关 /wb/"              http://127.0.0.1:7777/wb/
chk "网关 /wb/brand.html"    "http://127.0.0.1:7777/wb/brand.html?brand=MitoQ"
chk "网关 /niling/"          http://127.0.0.1:7777/niling/
echo "  ---- 合计 OK=$ok FAIL=$bad"
[ "$bad" = "0" ] && say "部署完成：http://<公网IP>:7777/  ·  /wb/  ·  /niling/" || warn "有 $bad 项未通过，见上"
