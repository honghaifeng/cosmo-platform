# 07 · 部署与运维

## 目标形态

```
/data/apps/cosmobounty/     底座（Python）
    service/app.py           listen 127.0.0.1:8098
    db/cosmobounty.db        只读
    knowledge/               知识库 + 媒体映射
    data/media_cdn.json      5988 条七牛映射
    niling.service           systemd 单元

/data/apps/starriver/       网关（Node）
    server.js                listen 0.0.0.0:7777
    public/                  星河 SPA + wb/ 工作台
    data/db.json             业务数据
    starriver.service        systemd 单元
```

公网只开 7777 一个端口。底座、数据库、MCP 全在 127.0.0.1 后面。

## 一键部署

```bash
bash scripts/deploy.sh            # 全量（代码 + 数据 + 前端）
bash scripts/deploy.sh --code     # 只推代码，不动数据库
bash scripts/deploy.sh --check    # 只自测，不推任何东西
```

脚本执行顺序：

```
1. 备份现网（db + db.json，带时间戳）
2. 同步底座 service/ crawler/ scripts/
3. 同步星河 server.js lib/ public/          ← public/ 是后补的
4. 推 systemd 单元，daemon-reload
5. 重启 niling + starriver
6. 跑 7 项冒烟（都打 127.0.0.1，避免公网/代理干扰）
7. 逐文件 sha256 比对两端
```

### 第 7 步是补上的，代价是一次事故

早期脚本只同步 `server.js` 和 `lib/`，**从来不管 `public/`**。结果出现过：

| 层 | 状态 |
|---|---|
| 后端接口 | 新的（`/api/compliance/dict` 返回 401 而不是 404，说明存在） |
| 后端逻辑 | 新的（分类名返回中文） |
| 前端 `public/app.js` | **旧的**（`compHTML` 出现 0 次） |
| 前端 `public/style.css` | **旧的**（27 个变量修复没上去） |

后果：合规体检接口能算出违禁词，但页面上没有渲染它的代码 ——
**对用户等于该功能不存在**。而接口测试全绿，部署脚本也报成功。

现在补了两次校验：同步 `public/`（`--exclude wb` 保护工作台目录），
以及部署末尾逐文件 sha256 比对，两端不一致就导致部署失败。

## sudo 密码

仓库里**没有**任何密码。部署时：

```bash
export DEPLOY_SUDO_PASS='...'      # 或者
bash scripts/deploy.sh # 脚本会交互提示
```

早期脚本里有 7 处硬编码 `echo '<密码>' | sudo -S ...`。这些在公开仓库里
等于把服务器 root 权限发出去。已全部改为环境变量。

## 反向代理

如果前面再加一层 Caddy / Nginx，**必须关 buffering**：

```nginx
location / {
    proxy_pass http://127.0.0.1:7777;
    proxy_buffering off;          # ← 不关这个，SSE 会攒成一坨再吐
    proxy_read_timeout 300s;      # 长任务要够长
    proxy_http_version 1.1;
    proxy_set_header Connection '';
}
```

Caddy：

```
reverse_proxy 127.0.0.1:7777 {
    flush_interval -1
}
```

## 端口连通性判断

**别用 `nc -z`。** 跨子网时会出假阴性（明明通的说成不通）。

用 Python socket 或 ssh：

```bash
python3 - <<'PY'
import socket
s = socket.socket(); s.settimeout(4)
try:
    s.connect(('192.168.40.32', 7777)); print('通')
except Exception as e:
    print('不通', type(e).__name__)
PY

ssh -o ConnectTimeout=6 hzck@192.168.40.32 'echo ok'
```

还要区分「超时」和「拒绝」：

| 现象 | 含义 |
|---|---|
| 超时 | 被丢弃 —— 没做端口映射，或防火墙丢包 |
| 立刻拒绝 | 端口映射存在，但那个端口没有服务在听 |

判断方法：拿一个肯定没人用的端口（如 12345）做对照。如果 12345 也是
「立刻拒绝」，说明这个 IP 的未映射端口由网络设备统一回 RST ——
那么目标端口「拒绝」就等于**没做映射**，而不是服务没起。

## 所有对外探测都要加 `--noproxy '*'`

开发机常挂着 HTTP 代理。不加这个参数，连 `127.0.0.1` 的请求也会走代理，
然后收到一个**假的 502**，让人以为服务挂了。

```bash
curl --noproxy '*' http://127.0.0.1:8098/api/stats      # 对
curl http://127.0.0.1:8098/api/stats                    # 可能拿到假 502
```

## 域名

当前：`http://ck.fronted.love:7777/`（直连，80/443 未做映射）。

要裸域名访问，需要在网络侧加一条 `80 → 内网:80` 的映射。
**代码侧无需改动** —— 前端全部相对路径，没有硬编码 IP/端口。

验证方法：

```bash
curl -s http://ck.fronted.love:7777/app.js | grep -c "122\.233\.94\.228"   # 应为 0
```

## 离线包

内网不通时走离线包：

```bash
bash base/scripts/build_install_pkg.sh          # 产出 dist/cb-<日期>.tgz
# 上传到对象存储，服务器上：
sudo bash install.sh
```

> 注意：`dist/` 里的历史离线包可能带旧版 `service/app.py`，
> 用它们装出来的环境同样可能有媒体路径问题。装完先跑一次冒烟。

## 日常运维

| 事情 | 命令 |
|---|---|
| 看状态 | `systemctl status niling starriver` |
| 看日志 | `journalctl -u starriver -f` / `journalctl -u niling -f` |
| 重启 | `sudo systemctl restart starriver` |
| 清底座缓存 | 重启底座（进程内 TTL 缓存，没有外部失效通道） |
| 看采集情况 | `python3 base/crawler/pipeline.py --status` |

## 备份

```bash
bash starriver/scripts/backup.sh        # 星河业务数据（约 92MB 包）
```

底座要备的是：

| 文件 | 频率 | 说明 |
|---|---|---|
| `db/cosmobounty.db` | 每日 | 内容库，24MB |
| `data/media_cdn.json` | 每次上传后 | 七牛映射表 |
| `data/token.txt` | 一次 | 主令牌，丢了重启会重新生成（会导致旧令牌失效） |
| `knowledge/` | 每周 | 知识库文档 |

媒体本身不用备 —— 全在七牛。

## 排错：症状 → 原因

| 症状 | 最可能的原因 |
|---|---|
| 工作台/逆龄观察 502 | 底座没起。`systemctl status niling` |
| 工作台 500 | `brands.json` 路径找不到，看底座日志 |
| 所有媒体显示「未落地」 | `media_cdn.json` 没读到。查 `COSMOBOUNTY_CDN_MAP` 和默认路径 |
| 视频 404 但图片正常 | key 里的 `:` 没换成 `_` |
| 图片 401 | 私有空间签名格式错（签了 path 而不是完整 URL，或结尾多了 `\n`） |
| 流式输出一次性吐出 | 反向代理没关 `proxy_buffering` |
| 页面是新的但功能没有 | 静态文件缓存，或缺 `public/` 同步 |
| 稿子停在半个标题 | 撞输出上限，看 `done.truncated` |
