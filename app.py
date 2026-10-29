#!/usr/bin/env python3
"""逆龄观察 · 内容数据服务（只读，零第三方依赖）
项目代号 CosmoBounty；对外名称「逆龄观察」

一个进程同时提供三种访问方式：
  1. 浏览页        GET  /                       朋友直接点开看内容库
  2. JSON API      GET  /api/stats|list|item|compare|comments|pending
                   POST /api/sql                 给脚本 / BI / 自建前端
  3. 远程 MCP      POST /mcp                     AI 客户端（WorkBuddy / Trae / Claude）直接接

鉴权：所有数据接口需 token，支持两种传法
  - 请求头  X-API-Key: <token>
  - 查询串  ?k=<token>   （浏览页链接用这个，页面会存到 localStorage）

环境变量：
  PORT            监听端口（默认 8080）
  CB_TOKEN        API token（默认取 data/token.txt，再默认内置常量）
  COSMOBOUNTY_DB  数据库路径（默认 data/cosmobounty.db）
"""
import json, os, re, secrets, sqlite3, sys, uuid, datetime
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, unquote, quote

HERE = os.path.dirname(os.path.abspath(__file__))


def _detect_root(here):
    """项目根探测：两种布局都必须活着。

    · 部署布局  /data/apps/cosmobounty/service/app.py  → 根在上一层
    · 仓库布局  <repo>/app.py                          → 根就是自己

    判据不用 data/（部署时 service/data/ 确实存在，会误判），
    用只有仓库才有的 crawler/ 和 workbench/。
    """
    for d in ("crawler", "workbench"):
        if os.path.isdir(os.path.join(here, d)):
            return here
    return os.path.dirname(here)


def _first(*cands):
    """按顺序取第一个存在的路径；都不存在就返回第一个候选（报错信息更有指向性）。"""
    for c in cands:
        if c and os.path.exists(c):
            return c
    return cands[0]


# 数据层（db / data / knowledge）挂在项目根上，不在 service/ 里 ——
# 所以凡是「读数据文件」的路径都要从 ROOT 拼，不能从 HERE 拼。
ROOT = os.environ.get("COSMOBOUNTY_ROOT") or _detect_root(HERE)
DB = os.environ.get("COSMOBOUNTY_DB") or _first(
    os.path.join(ROOT, "db", "cosmobounty.db"),
    os.path.join(HERE, "data", "cosmobounty.db"),
    os.path.join(ROOT, "data", "cosmobounty.db"),
)
KB = os.environ.get("COSMOBOUNTY_KB") or _first(  # 知识库根（文档+媒体）
    os.path.join(ROOT, "knowledge"),
    os.path.join(HERE, "knowledge"),
)

PORT = int(os.environ.get("PORT", "8080"))
PLATFORMS = {"xhs": "小红书", "douyin": "抖音", "baidu": "百度", "toutiao": "今日头条", "web": "新闻源", "shipinhao": "视频号"}

# 「品类场景词」不是品牌，而是一条独立的内容轨道：抓的是品类/场景/痛点类笔记
# （熬夜、精力、抗疲劳……），用来分析用户在什么场景下、用什么话术谈这件事。
# 它有独立价值，但绝不能混进六品牌横评 —— 混进去就是拿"别人家的内容"给
# 某个品牌凑声量。所有品牌维度（占比、对比、排名）都必须把它排除，单独计数。
SCENE_BRAND = "品类场景词"

# 横评识别：一篇内容同时点名 ≥2 个竞品时，它是「横向对比稿」，
# 归属到任何一家都会让那一家的声量虚高，也容易让人误读成单品牌口碑。
# 不删、不隐藏（内容本身是真的），但在表里标出来，和「独立观点」区分开。
# 只用品牌主名，避免产品词误伤。
CROSS_RX = [
    ("COSMOBOUNTY", re.compile(r"(cosmobounty|科诗博缇|黑白分时)", re.I)),
    ("MitoQ", re.compile(r"(mitoq|mito\s*q)", re.I)),
    ("GRANVER", re.compile(r"(granver|吉力威|吉清肺|吉利肝)", re.I)),
    ("LAIFE", re.compile(r"(laife|乐梵)", re.I)),
    ("W+端粒塔", re.compile(r"(端粒塔)", re.I)),
    ("塞乐瑞", re.compile(r"(塞乐瑞|赛乐瑞|cellerator)", re.I)),
]

def _token():
    """主令牌。优先级：环境变量 → data/token.txt → 首次启动随机生成并落盘。

    早期版本这里有个硬编码默认值（cb-cosmo-2026）。源码公开后，这个默认值
    等于一个能直接调 /api/sql 的后门 —— 谁 clone 下来跑、或者线上忘了配，
    任何人拿它就能查全库。现在改成没配置就随机生成，并且持久化，重启不变。
    """
    t = os.environ.get("CB_TOKEN")
    if t:
        return t.strip()
    p = os.path.join(HERE, "data", "token.txt")
    if os.path.exists(p):
        v = open(p, encoding="utf-8").read().strip()
        if v:
            return v
    v = secrets.token_urlsafe(24)
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(v)
        os.chmod(p, 0o600)
        sys.stderr.write("[base] 未配置主令牌，已生成并写入 %s\n" % p)
    except Exception as e:
        sys.stderr.write("[base] 主令牌落盘失败（%s），本次运行用临时令牌\n" % e)
    return v

TOKEN = _token()

def _tokens():
    """多令牌：按平台/使用者分别签发，便于单独吊销与统计
    来源：CB_TOKEN 环境变量、data/token.txt（主令牌）、data/tokens.json（{token: 标签}）
    """
    out = {TOKEN: "primary"}
    p = os.path.join(HERE, "data", "tokens.json")
    if os.path.exists(p):
        try:
            for k, v in json.load(open(p, encoding="utf-8")).items():
                out[str(k).strip()] = str(v)
        except Exception:
            pass
    return out

TOKENS = _tokens()
# 分享链接码：发布平台自动拼在 URL 上的 sharecode，等同于只读浏览凭证
# （拿到分享链接的人本来就能看到数据，这里把它正式纳入鉴权，浏览页免输密钥）
# 分享码：发布平台会自动在链接上拼 sharecode，等同于只读浏览凭证。
# 必须显式配置（CB_SHARECODE 或 data/sharecode.txt）才启用；不配就不注册。
# 早期版本把分享码写死在源码里，公开仓库等于把浏览凭证一起发出去。
SHARECODE = (os.environ.get("CB_SHARECODE") or "").strip()
if not SHARECODE:
    _sp = os.path.join(HERE, "data", "sharecode.txt")
    if os.path.exists(_sp):
        SHARECODE = open(_sp, encoding="utf-8").read().strip()
if SHARECODE:
    TOKENS[SHARECODE] = "share-link"
VERSION_FILE = os.path.join(HERE, "data", "version.json")
# 七牛对象存储（视频/图片在线播放）：测试域名仅支持 http，故统一走 /cdn/ 同源代理
CDN_BASE = os.environ.get("CB_CDN_BASE", "http://tni044vir.hn-bkt.clouddn.com")

# ---------------------------------------------------------------- db helpers
def conn():
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=10)
    c.row_factory = sqlite3.Row
    return c

def version():
    if os.path.exists(VERSION_FILE):
        try:
            return json.load(open(VERSION_FILE, encoding="utf-8"))
        except Exception:
            pass
    return {}

def own_profile():
    """自有品牌产品档案（来自 knowledge/cosmobounty，打包为 data/own_brand.json）"""
    p = os.path.join(HERE, "data", "own_brand.json")
    if os.path.exists(p):
        try:
            return json.load(open(p, encoding="utf-8"))
        except Exception:
            pass
    return {}

def level_of(body, platform):
    b = (body or "").strip()
    if b.startswith("（SERP 收录级"):
        return "serp"
    if len(b) >= 220:
        return "full"
    if len(b) >= 30:
        return "partial"
    return "full" if platform in ("xhs", "douyin") else "missing"

LEVEL_TXT = {"full": "全文", "partial": "摘要级（缺正文）", "serp": "仅 SERP 收录", "missing": "仅标题"}

def item_row(r, with_body=False):
    m = json.loads(r["metrics_json"] or "{}")
    a = json.loads(r["analysis_json"] or "{}") if "analysis_json" in r.keys() else {}
    d = {"id": r["id"], "brand": r["brand"], "platform": r["platform"],
         "platform_cn": PLATFORMS.get(r["platform"], r["platform"]),
         "title": r["title"], "author": r["author"], "publish_time": r["publish_time"],
         "url": r["url"], "metrics": m,
         "likes": m.get("likes", 0), "collects": m.get("collects", 0),
         "comments": m.get("comments", 0), "shares": m.get("shares", 0),
         "level": level_of(r["body"], r["platform"]),
         "level_txt": LEVEL_TXT[level_of(r["body"], r["platform"])]}
    if with_body:
        d["body"] = r["body"] or ""
        d["tags"] = r["tags"] if "tags" in r.keys() else ""
        d["media"] = json.loads(r["media_json"] or "{}") if "media_json" in r.keys() else {}
        # media 里存的是采集当时的签名直链（约 24h 后 403），只作溯源用。
        # 真正打得开的是 media_local：优先七牛 /cdn/，其次本地 /kb/。
        # 不写进数据库是有意的 —— 资产索引会随补采变化，写死进库就会过期失真。
        d["media_local"] = {
            "video": local_media(r["platform"], r["url"], r["brand"], d["media"], "video")[0],
            "cover": local_media(r["platform"], r["url"], r["brand"], d["media"], "cover")[0],
            "images": local_media_list(r["platform"], r["url"], r["brand"], "image"),
        }
        d["analysis"] = a
    else:
        d["nature"] = a.get("content_type")
        d["seo"] = (a.get("seo") or {}).get("score")
        d["geo"] = (a.get("geo") or {}).get("score")
        d["body_len"] = len(r["body"] or "")
        d["body_snippet"] = (r["body"] or "").strip().replace("\n", " ")[:120]
    return d

# ---------------------------------------------------------------- knowledge helpers
DOC_EXT = {".md", ".json"}
MEDIA_MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
              ".webp": "image/webp", ".gif": "image/gif", ".mp4": "video/mp4",
              ".webm": "video/webm", ".pdf": "application/pdf"}

def kb_safe(rel):
    """防目录穿越：规范路径并确保落在 KB 根内"""
    rel = (rel or "").lstrip("/\\")
    full = os.path.normpath(os.path.join(KB, rel))
    if not full.startswith(os.path.normpath(KB) + os.sep) or not os.path.isfile(full):
        return None
    return full

MEDIA_EXT = (".mp4", ".m4v", ".mov", ".jpg", ".jpeg", ".png", ".webp", ".gif")
_MEDIA_CDN = None


def media_cdn_map():
    """本地媒体 → 七牛对象 key 的映射（上传脚本产出）。

    ⚠️ 这份表在「项目根/data/media_cdn.json」，不在 service/data/ 下。
    以前这里写的是 os.path.join(HERE, "data", ...)，即 service/data/ ——
    开发机上恰好有人放了一份副本，所以本地一直是对的；
    而部署脚本从来只把文件推到根上的 data/，服务器上那个位置什么都没有。
    读不到表 → _media_index() 退化成「只扫本地磁盘」→ 服务器不落地 16.9GB 媒体
    → 5996 条内容的封面/视频**全部**判成「未落地」，
    页面于是写着「视频文件未落地本站」，可七牛上明明躺着 2183 个视频。
    现在按 wbdata.CDN_MAP_FILE 的口径找，并保留旧位置做兜底。
    """
    global _MEDIA_CDN
    if _MEDIA_CDN is None:
        cands = [
            os.environ.get("COSMOBOUNTY_CDN_MAP"),
            os.path.join(ROOT, "data", "media_cdn.json"),   # 正确位置
            os.path.join(HERE, "data", "media_cdn.json"),   # 旧布局，兼容
        ]
        _MEDIA_CDN = {}
        for p in cands:
            if not p or not os.path.exists(p):
                continue
            try:
                m = json.load(open(p, encoding="utf-8"))
            except Exception as e:
                print(f"[warn] 媒体映射表读取失败 {p}: {e}", file=sys.stderr)
                continue
            if m:
                _MEDIA_CDN = m
                break
        # 空表不能静默 —— 它会让整站媒体变成「未落地」，而且看起来像数据没采到。
        if not _MEDIA_CDN:
            print("[warn] 媒体映射表为空/未找到，封面与视频将全部显示「未落地」。"
                  " 检查 " + os.path.join(ROOT, "data", "media_cdn.json"), file=sys.stderr)
    return _MEDIA_CDN


_MEDIA_IDX = None
# 媒体文件名规则：<平台>:<内容ID>[_序号].<扩展名>
# 旧实现是「按类型硬拼几个候选后缀」，有两个真问题：
#   1) 只试 .jpg/.png/.webp —— LAIFE/MitoQ/W+ 两千多张封面是 .jpeg，
#      文件明明在本地、也传了七牛，页面一律判「未存」；
#   2) 多图笔记只试无序号的那一个文件 —— 第 2 张起全判缺失。
# 现在统一按文件名索引解析，后缀和序号都不再靠猜。
_MFILE_RX = re.compile(r"^(?P<plat>[a-z]+)[:_](?P<pid>[0-9a-zA-Z]+?)(?:_(?P<idx>\d+))?\."
                       r"(?P<ext>[a-z0-9]+)$", re.I)
_MEDIA_DIR = {"video": "video", "cover": "cover", "image": "post", "post": "post"}


def _pid_of(platform, url):
    """从内容原始链接取平台内容 ID。

    小红书 note_id 是 24 位十六进制（如 6adf0fe8000000001603cf23），里面夹着
    a-f，用「连续 10 位以上数字」去抓会抓不到或抓错，导致小红书媒体一律
    「未落地」。所以小红书单独走 note_id 精确匹配，其余平台维持数字 id。
    """
    if platform == "xhs":
        m = re.search(r"/(?:explore|discovery/item|search_result)/([0-9a-f]{16,32})", url or "")
        if m:
            return m.group(1)
    m = re.search(r"(\d{10,})", url or "")
    return m.group(1) if m else ""


def _media_index():
    """(品牌, 目录, 平台, 内容ID) → [地址]，按序号排好

    地址优先 /cdn/（七牛同源代理，不占本机带宽、也不会被 https 混内容拦掉），
    其次 /kb/（本地文件）。两个来源都收：只传了没落盘、或落了盘还没传，都能用。
    """
    global _MEDIA_IDX
    if _MEDIA_IDX is not None:
        return _MEDIA_IDX
    mroot = os.path.join(KB, "media")
    idx, seen = {}, set()

    def _size(rel):
        try:
            return os.path.getsize(os.path.join(mroot, rel))
        except OSError:
            return -1

    for rel, cdn in (media_cdn_map() or {}).items():
        parts = rel.split("/")
        if len(parts) < 3:
            continue
        brand, kind, fn = parts[0], parts[1], "/".join(parts[2:])
        m = _MFILE_RX.match(fn)
        if not m:
            continue
        key = (brand, kind, m.group("plat").lower(), m.group("pid"))
        _k = cdn.split("/", 3)[-1] if "://" in cdn else "media/" + rel
        idx.setdefault(key, []).append((0, 0 if m.group("idx") is not None else 1,
                                        int(m.group("idx") or 0),
                                        "/cdn/" + _k.replace(":", "_"), _size(rel)))
        seen.add(rel)
    if os.path.isdir(mroot):
        for brand in os.listdir(mroot):
            bdir = os.path.join(mroot, brand)
            if not os.path.isdir(bdir):
                continue
            for kind in os.listdir(bdir):
                kdir = os.path.join(bdir, kind)
                if not os.path.isdir(kdir):
                    continue
                for fn in os.listdir(kdir):
                    if fn.startswith(".") or f"{brand}/{kind}/{fn}" in seen:
                        continue
                    m = _MFILE_RX.match(fn)
                    if not m:
                        continue
                    key = (brand, kind, m.group("plat").lower(), m.group("pid"))
                    idx.setdefault(key, []).append(
                        (1, 0 if m.group("idx") is not None else 1, int(m.group("idx") or 0),
                         f"/kb/media/{brand}/{kind}/{fn}", _size(f"{brand}/{kind}/{fn}")))
    for k, v in idx.items():
        v.sort(key=lambda x: (x[0], x[1], x[2]))
        # 同一张首图存过两份：早期存成 xxx.jpg，后来存成 xxx_0.jpg。
        # 两个都留着会把「4 张图」报成 5 张、首图重复出现。
        # 只在「后缀相同」或「字节数相同」时去重 —— 两者都不符时宁可多留，
        # 也不误删一张真图（有 173 条笔记只有无序号版 + _2/_3，删了就少一张）。
        if k[1] == "post" and len(v) > 1:
            z = [(x[4], os.path.splitext(x[3])[1].lower()) for x in v if x[1] == 0 and x[2] == 0]
            if z:
                zs = {s for s, _ in z if s > 0}
                ze = {e for _, e in z}
                v = [x for x in v
                     if not (x[1] == 1 and (os.path.splitext(x[3])[1].lower() in ze
                                            or (x[4] > 0 and x[4] in zs)))]
        idx[k] = [x[3] for x in v]
    _MEDIA_IDX = idx
    return idx


def local_media_list(platform, url, brand, kind):
    """该条内容在本地/七牛已落地的全部媒体地址（多图按序号返回，可为空）。"""
    pid = _pid_of(platform, url)
    if not pid or not brand:
        return []
    return list(_media_index().get((brand, _MEDIA_DIR.get(kind, kind), platform, pid), []))


def local_media(platform, url, brand, media, kind):
    """兼容旧调用：返回第一个已落地地址 + 是否有。没有则空，不回退外链。"""
    lst = local_media_list(platform, url, brand, kind)
    return (lst[0], True) if lst else ("", False)


def _has_archive(platform, url):
    """是否有该条内容的本地「仿原页」快照。"""
    if platform != "xhs":
        return ""
    m = re.search(r"/(?:explore|discovery/item)/([0-9a-f]{16,32})", url or "")
    if not m:
        return ""
    nid = m.group(1)
    if os.path.isfile(os.path.join(HERE, "archives", "xhs", nid + ".html")):
        return "/note/" + nid
    return ""


def kb_docs():
    out = []
    for root, _dirs, files in os.walk(KB):
        for f in files:
            ext = os.path.splitext(f)[1].lower()
            if ext not in DOC_EXT:
                continue
            full = os.path.join(root, f)
            rel = os.path.relpath(full, KB).replace(os.sep, "/")
            st = os.stat(full)
            out.append({"path": rel, "size": st.st_size,
                        "mtime": datetime.datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")})
    out.sort(key=lambda x: x["path"])
    return out

_KIND_ORDER = {k: i for i, k in enumerate(("logo", "product", "post", "cover", "video"))}

def kb_media_list():
    """媒体素材清单：本地已落盘的 + 七牛对象存储里的，按品牌/类型分组"""
    out = []
    seen = set()
    mroot = os.path.join(KB, "media")
    if os.path.isdir(mroot):
        for brand in sorted(os.listdir(mroot)):
            bdir = os.path.join(mroot, brand)
            if not os.path.isdir(bdir):
                continue
            # 不能写死子目录名：早期只有 logo/product/post/video，后来新增了 cover（平台封面图），
            # 写死会导致目录里明明有上千张图，页面只显示几十个。
            _order = {k: i for i, k in enumerate(("logo", "product", "post", "cover", "video"))}
            kinds = sorted(
                (d for d in os.listdir(bdir) if os.path.isdir(os.path.join(bdir, d))),
                key=lambda d: (_order.get(d, 99), d))
            for kind in kinds:
                kdir = os.path.join(bdir, kind)
                if not os.path.isdir(kdir):
                    continue
                for f in sorted(os.listdir(kdir)):
                    ext = os.path.splitext(f)[1].lower()
                    if ext not in MEDIA_MIME:
                        continue
                    st = os.stat(os.path.join(kdir, f))
                    seen.add(f"{brand}/{kind}/{f}")
                    out.append({"brand": brand, "kind": kind, "file": f,
                                "path": f"media/{brand}/{kind}/{f}",
                                "size": st.st_size, "cdn": False,
                                "mtime": datetime.datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d")})
    # 七牛对象存储里的素材（视频基本只在这里，发布包放不下）：一并列出，
    # 否则媒体库只看到本地那部分，等于少了一大半。
    for rel, cdn_url in (media_cdn_map() or {}).items():
        if rel in seen:
            continue
        parts = rel.split("/")
        if len(parts) < 3:
            continue
        brand, kind, fn = parts[0], parts[1], "/".join(parts[2:])
        if os.path.splitext(fn)[1].lower() not in MEDIA_MIME:
            continue
        # 对象键是 media/<品牌>/<类型>/<文件名>（文件名里的冒号上传时换成了下划线），
        # 直接用 rel 拼会少一层 media/，七牛返回 404
        _key = cdn_url.split("/", 3)[-1] if "://" in cdn_url else "media/" + rel
        # url 直接给七牛地址（页面按「媒体全部走七牛」这条规矩来）；proxy 留给 https 站点兜底
        _abs = cdn_url if "://" in cdn_url else (CDN_BASE.rstrip("/") + "/" + _key)
        out.append({"brand": brand, "kind": kind, "file": fn,
                    "path": f"media/{rel}", "cdn": True,
                    "url": _abs,
                    "proxy": "/cdn/" + _key.replace(":", "_"),
                    "size": None, "mtime": "", "_sort": 1})
    out.sort(key=lambda x: (x["brand"], _KIND_ORDER.get(x["kind"], 99), x.get("_sort", 0), x["file"]))
    for x in out:
        x.pop("_sort", None)
    return out

def kb_manifest():
    p = os.path.join(KB, "media", "manifest.json")
    if os.path.exists(p):
        try:
            return json.load(open(p, encoding="utf-8"))
        except Exception:
            pass
    return {"items": []}

# ---------------------------------------------------------------- tools (MCP + REST 共用)
TOOLS = [
    {"name": "content_stats",
     "description": "内容库总览：数据版本日期、内容/评论总量、平台分布、品牌分布、内容性质分布、正文完整度。任何分析的第一步。",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "search_content",
     "description": "按关键词/品牌/平台/内容性质检索内容，返回标题、互动数据、原文链接、SEO/GEO 分。",
     "inputSchema": {"type": "object", "properties": {
         "query": {"type": "string", "description": "关键词，匹配标题或正文，可空"},
         "brand": {"type": "string", "description": "MitoQ/GRANVER/LAIFE/塞乐瑞/W+端粒塔"},
         "platform": {"type": "string", "description": "xhs/douyin/baidu/toutiao/web"},
         "nature": {"type": "string", "description": "成分科普/测评种草/口播种草/娱乐蹭词/PR 通稿/常规内容"},
         "min_likes": {"type": "integer"},
         "limit": {"type": "integer", "description": "默认 30"}}}},
    {"name": "get_content",
     "description": "取单条内容全文：正文、作者、时间、互动指标、图片/视频直链、营销分析（关键词/SEO/GEO/合规风险）、评论原文。",
     "inputSchema": {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]}},
    {"name": "brand_compare",
     "description": "品牌对比：各品牌内容数、总赞、总藏、总评论、平均 SEO/GEO 分，可按平台限定。",
     "inputSchema": {"type": "object", "properties": {"platform": {"type": "string"}}}},
    {"name": "top_content",
     "description": "头部内容：按点赞/评论/SEO/GEO 排序，可限定品牌与平台，用于爆文拆解。",
     "inputSchema": {"type": "object", "properties": {
         "brand": {"type": "string"}, "platform": {"type": "string"},
         "by": {"type": "string", "enum": ["likes", "comments", "seo", "geo"]},
         "limit": {"type": "integer", "description": "默认 10"}}}},
    {"name": "search_comments",
     "description": "检索评论原文：按品牌/关键词（智商税、没用、回购、有效等）找用户原声，用于口碑痛点分析。",
     "inputSchema": {"type": "object", "properties": {
         "query": {"type": "string"}, "brand": {"type": "string"},
         "limit": {"type": "integer", "description": "默认 50"}}}},
    {"name": "pending_content",
     "description": "查看缺正文的条目清单与缺失原因（数据完整度透明化）。",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "kb_docs",
     "description": "知识库文档清单：自有品牌档案、竞品档案（背景/产品线/价格/账号矩阵/打法/风险）、监测总纲、搜索词配置。分析前必读。",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "kb_doc",
     "description": "读取知识库文档全文（Markdown）。path 来自 kb_docs 返回，如 competitors/LAIFE.md。",
     "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
    {"name": "sql_query",
     "description": "对内容库执行只读 SELECT（复杂聚合用）。表：content / comment / daily_metric / crawl_log。禁止写操作。",
     "inputSchema": {"type": "object", "properties": {"sql": {"type": "string"}}, "required": ["sql"]}},
]

def apply_gate(sql):
    """给任意 SELECT 强制注入「只查已校验数据」的限制。

    用户规则：未校验的数据不对外展示。/api/sql 与 MCP 的 sql_query
    都是自由查询入口，不处理就能绕过其它接口查到未校验的行。
    策略：把受控表换成只含已校验行的 CTE，不破坏原 SQL 结构。
    """
    out = sql
    W = chr(92) + "b"
    FJ = chr(92) + "b(from|join)" + chr(92) + "s+"
    has_content = re.search("(?i)" + FJ + "content" + W, out)
    has_xhs = re.search("(?i)" + FJ + "xhs_note" + W, out)
    has_comment = re.search("(?i)" + FJ + "comment" + W, out)
    if not (has_content or has_xhs or has_comment):
        return out
    ctes = []
    if has_content:
        ctes.append("_gc AS (SELECT * FROM content WHERE verified=1)")
    if has_xhs:
        ctes.append("_gx AS (SELECT * FROM xhs_note WHERE verified=1)")
    if has_comment:
        ctes.append("_gm AS (SELECT * FROM comment WHERE content_id IN "
                    "(SELECT id FROM content WHERE verified=1))")
    for tbl, alias in (("content", "_gc"), ("xhs_note", "_gx"), ("comment", "_gm")):
        pat = "(?i)(" + chr(92) + "bfrom" + chr(92) + "s+|" + chr(92) + "bjoin" + chr(92) + "s+)" + tbl + chr(92) + "b"
        out = re.sub(pat, lambda m, a=alias: m.group(1) + a, out)
    cte_text = ", ".join(ctes)
    if re.match("(?is)^" + chr(92) + "s*with" + chr(92) + "b", out):
        m = re.search("(?is)" + chr(92) + "bwith" + chr(92) + "b", out)
        j = m.end(); depth = 0
        while j < len(out):
            ch = out[j]
            if ch == "(":
                depth += 1
            elif ch == ")":
                if depth == 0: break
                depth -= 1
            elif depth == 0 and re.match("(?i)select" + chr(92) + "b", out[j:j + 8]):
                break
            j += 1
        out = out[:j] + ", " + cte_text + " " + out[j:]
    else:
        out = "WITH " + cte_text + " " + out
    return out


def call_tool(name, args):
    c = conn()
    try:
        if name == "content_stats":
            lv = {"full": 0, "partial": 0, "serp": 0, "missing": 0}
            for r in c.execute("SELECT platform, body FROM content WHERE verified=1"):
                lv[level_of(r["body"], r["platform"])] += 1
            by_brand = [dict(r) for r in c.execute(
                "SELECT brand, COUNT(*) n, SUM(CAST(json_extract(metrics_json,'$.likes') AS INT)) likes "
                "FROM content WHERE verified=1 AND brand<>? GROUP BY 1 ORDER BY likes DESC",
                (SCENE_BRAND,))]
            # 场景词轨道单列，不参与品牌排名
            scene_n = c.execute(
                "SELECT COUNT(*) FROM content WHERE verified=1 AND brand=?",
                (SCENE_BRAND,)).fetchone()[0]
            # 自有品牌：即使暂无采集内容也固定展示（产品知识库资料已在 own_brand.json）
            own = own_profile()
            if own:
                hit = next((x for x in by_brand if x["brand"] == own["brand"]), None)
                if hit:
                    hit["own"] = True
                else:
                    by_brand.insert(0, {"brand": own["brand"], "n": 0, "likes": 0, "own": True})
            return {"data_version": version() or "unknown",
                    "contents": c.execute("SELECT COUNT(*) FROM content WHERE verified=1").fetchone()[0],
                    "comments": c.execute("SELECT COUNT(*) FROM comment").fetchone()[0],
                    "by_platform": [dict(r) for r in c.execute(
                        "SELECT platform, COUNT(*) n FROM content WHERE verified=1 GROUP BY 1 ORDER BY n DESC")],
                    "by_brand": by_brand,
                    # 独立观点数：同文案批量投放（旗舰店连发几十上百条）会虚高条数，
                    # 这里按 title 去重给出一个不被刷量的口径。
                    "unique_titles": c.execute(
                        "SELECT COUNT(DISTINCT title) FROM content WHERE verified=1 "
                        "AND IFNULL(title,'')<>''").fetchone()[0],
                    "scene_track": scene_n,
                    "by_nature": [dict(r) for r in c.execute(
                        "SELECT json_extract(analysis_json,'$.content_type') nature, COUNT(*) n "
                        "FROM content WHERE verified=1 GROUP BY 1 ORDER BY n DESC")],
                    "fulltext_levels": lv}

        if name == "search_content":
            q, p = ["SELECT * FROM content WHERE (verified=1) AND 1=1"], []
            if args.get("query"):
                q.append("AND (title LIKE ? OR body LIKE ?)"); p += [f"%{args['query']}%"] * 2
            for k in ("brand", "platform"):
                if args.get(k):
                    q.append(f"AND {k}=?"); p.append(args[k])
            if args.get("nature"):
                q.append("AND json_extract(analysis_json,'$.content_type')=?"); p.append(args["nature"])
            if args.get("min_likes"):
                q.append("AND CAST(json_extract(metrics_json,'$.likes') AS INT)>=?"); p.append(int(args["min_likes"]))
            q.append("ORDER BY CAST(json_extract(metrics_json,'$.likes') AS INT) DESC LIMIT ?")
            p.append(min(int(args.get("limit", 30)), 200))
            rows = c.execute(" ".join(q), p).fetchall()
            return {"count": len(rows), "items": [item_row(r) for r in rows]}

        if name == "get_content":
            r = c.execute("SELECT * FROM content WHERE (verified=1) AND id=?", (int(args["id"]),)).fetchone()
            if not r:
                return {"error": "not found"}
            d = item_row(r, True)
            d["comment_count"] = c.execute("SELECT COUNT(*) FROM comment WHERE content_id=?", (r["id"],)).fetchone()[0]
            d["comments_list"] = [dict(x) for x in c.execute(
                "SELECT body, like_count, publish_time FROM comment WHERE content_id=? "
                "ORDER BY like_count DESC LIMIT 40", (r["id"],))]
            try:
                d["media_text"] = [dict(x) for x in c.execute(
                    "SELECT kind, source, text FROM media_text WHERE content_id=? "
                    "ORDER BY source", (r["id"],))]
            except sqlite3.OperationalError:
                d["media_text"] = []
            return d

        if name == "raw_table":
            """原始数据总表：品牌/渠道/标题/正文/图片/视频/OCR/ASR/原文链接/SEO/GEO"""
            n = max(1, min(int(args.get("limit", 200)), 500))
            off = max(0, int(args.get("offset", 0) or 0))
            cond, params = [], []
            if args.get("brand"):
                cond.append("c.brand=?"); params.append(args["brand"])
            if args.get("platform"):
                cond.append("c.platform=?"); params.append(args["platform"])
            # verified=1 直接写死在 SQL 字面量里（不拼进变量），
            # 保证边界守卫能静态识别，也避免将来清空附加条件时误放未校验数据。
            tail = (" AND " + " AND ".join(cond)) if cond else ""
            total = c.execute(f"SELECT COUNT(*) FROM content c WHERE c.verified=1{tail}",
                              params).fetchone()[0]
            rows = c.execute(f"""SELECT c.* FROM content c
                                 WHERE c.verified=1{tail}
                                 ORDER BY c.publish_time DESC, c.id DESC LIMIT {n} OFFSET {off}""",
                             params).fetchall()
            items = []
            # 同文案批量投放：矩阵号/旗舰店会拿同一段文案连发几十上百条视频
            # （每条形如不同 aweme_id、不同发布时间，都是真实内容，不能删）。
            # 但它会让「条数」「声量」被单品牌刷上去，所以要在表里标出来，
            # 让人知道这一行代表的是「一组里的第几条」，而不是独立观点。
            bulk = {t: n for t, n in c.execute(
                "SELECT title, COUNT(*) FROM content WHERE verified=1 AND IFNULL(title,'')<>'' "
                "GROUP BY title HAVING COUNT(*)>1")}
            for r in rows:
                try:
                    media = json.loads(r["media_json"] or "{}")
                except Exception:
                    media = {}
                mts = {}
                try:
                    for x in c.execute("SELECT kind, source, text FROM media_text WHERE content_id=? ORDER BY source",
                                       (r["id"],)):
                        mts.setdefault(x["kind"], []).append(f"[{x['source']}] {x['text']}")
                except sqlite3.OperationalError:
                    pass
                a = json.loads(r["analysis_json"] or "{}") if "analysis_json" in r.keys() else {}
                seo_obj = a.get("seo") or {}
                geo_obj = a.get("geo") or {}
                kw = a.get("keywords") or []
                geo_words = (a.get("ingredients") or []) + (a.get("efficacy") or [])
                m = json.loads(r["metrics_json"] or "{}")
                cmts = [f"{x['body']}（赞{int(x['like_count'] or 0)}）"
                        for x in c.execute(
                            "SELECT body, like_count FROM comment WHERE content_id=? "
                            "ORDER BY like_count DESC LIMIT 5", (r["id"],))]
                trunc = lambda s, n2=100: (s or "").replace("\n", " ")[:n2]
                vid = media.get("video") or ""
                cov = media.get("cover") or ""
                imgs = (media.get("images") or [])
                lv, has_v = local_media(r["platform"], r["url"], r["brand"], media, "video")
                lc, has_c = local_media(r["platform"], r["url"], r["brand"], media, "cover")
                li_all = local_media_list(r["platform"], r["url"], r["brand"], "image")
                # 内容完整度：让「这条到底拿到多少」在表里直接可见，
                # 而不是把只有标题的条目和全文条目混在一起看不出来。
                _blen = len((r["body"] or "").strip())
                _mlen = sum(len(t) for t in mts.get("ocr", [])) + sum(len(t) for t in mts.get("asr", []))
                if _blen >= 220:
                    _lv = "full"        # 全文
                elif _blen >= 30:
                    _lv = "partial"     # 有正文但偏短
                elif _mlen >= 30:
                    _lv = "media"       # 正文空，内容在图片文字/口播里
                else:
                    _lv = "thin"        # 只有标题
                _txt = f"{r['title'] or ''} {trunc(r['body'], 800)}"
                _cross = sum(1 for _b, _rx in CROSS_RX if _rx.search(_txt))
                items.append({
                    "id": r["id"], "brand": r["brand"], "platform": r["platform"],
                    "title": r["title"], "body": trunc(r["body"], 160),
                    "body_full": len(r["body"] or ""),
                    "level": _lv,
                    "level_txt": {"full": "全文", "partial": "有正文",
                                  "media": "媒体解读", "thin": "仅标题"}[_lv],
                    "cross_n": _cross,
                    "bulk_n": bulk.get(r["title"] or "", 1),
                    "archived": _has_archive(r["platform"], r["url"]),
                    "author": r["author"], "publish_time": r["publish_time"],
                    "n_images": len(li_all) or len(imgs),
                    "image_urls": li_all,
                    "video_url": lv or vid,
                    "video_local": has_v,
                    "cover_url": lc or cov,
                    "ocr": trunc(" / ".join(mts.get("ocr", [])), 160),
                    "ocr_full": sum(len(t) for t in mts.get("ocr", [])),
                    "asr": trunc(" ".join(mts.get("asr", [])), 160),
                    "asr_full": sum(len(t) for t in mts.get("asr", [])),
                    "url": r["url"],
                    "seo": "、".join(kw[:12]),
                    "seo_score": seo_obj.get("score"),
                    "geo": "、".join(geo_words[:12]),
                    "geo_score": geo_obj.get("score"),
                    "intent": "、".join((a.get("intent") or [])[:6]),
                    "content_type": a.get("content_type") or "",
                    # 百度/头条/新闻源是文章，本来就没有点赞收藏，返回 0 会让人
                    # 以为「这条没人看」。这里保留 null，前端渲染成「—」，
                    # 有真实互动数的平台照旧给数字（含真实的 0）。
                    "likes": m.get("likes"), "collects": m.get("collects"),
                    "comments_n": m.get("comments"), "shares": m.get("shares"),
                    "cmts": trunc(" ｜ ".join(cmts), 300),
                    "cmts_n": len(cmts),
                })
            # 「仅标题」= 正文和媒体解读都没拿到，点开只能看原帖，内容不可用。
            # 默认不摆进对外表格：数据在库里，但不该让它看起来像一条完整情报。
            # 需要看时传 hide_thin=0，或在「数据完整度」页看缺口明细。
            _thin = [x for x in items if x["level"] == "thin"]
            if str(args.get("hide_thin", "1")) != "0":
                items = [x for x in items if x["level"] != "thin"]
            return {"count": len(items), "total": total, "offset": off,
                    "items": items, "hidden_thin": len(_thin)}

        if name == "analysis_report":
            """分析报告：全部指标由原始数据聚合，不做任何人工输入。"""
            # 1) 品牌总览
            brands = []
            for r in c.execute("""SELECT brand, COUNT(*) n,
                    SUM(CAST(json_extract(metrics_json,'$.likes') AS INT)) likes,
                    SUM(CAST(json_extract(metrics_json,'$.collects') AS INT)) collects,
                    SUM(CAST(json_extract(metrics_json,'$.comments') AS INT)) comments,
                    SUM(CAST(json_extract(metrics_json,'$.shares') AS INT)) shares,
                    ROUND(AVG(CAST(json_extract(analysis_json,'$.seo.score') AS REAL)),1) avg_seo,
                    ROUND(AVG(CAST(json_extract(analysis_json,'$.geo.score') AS REAL)),1) avg_geo
                    FROM content WHERE verified=1 GROUP BY brand ORDER BY likes DESC"""):
                top = c.execute("""SELECT id, title, CAST(json_extract(metrics_json,'$.likes') AS INT) likes, platform
                        FROM content WHERE (verified=1) AND brand=? ORDER BY 3 DESC LIMIT 1""", (r["brand"],)).fetchone()
                kw_rows = c.execute("""SELECT analysis_json FROM content WHERE (verified=1) AND brand=?
                        AND analysis_json IS NOT NULL AND analysis_json!=''""", (r["brand"],)).fetchall()
                from collections import Counter
                cnt = Counter()
                for kr in kw_rows:
                    for w in (json.loads(kr[0]).get("keywords") or []):
                        if isinstance(w, str) and len(w) >= 2:
                            cnt[w] += 1
                plat_rows = c.execute("""SELECT platform, COUNT(*) n FROM content
                        WHERE (verified=1) AND brand=? GROUP BY platform ORDER BY n DESC""", (r["brand"],)).fetchall()
                brands.append({**dict(r),
                    "platforms": [{"p": x["platform"], "cn": PLATFORMS.get(x["platform"]), "n": x["n"]} for x in plat_rows],
                    "top": dict(top) if top else None, "keywords": cnt.most_common(12)})
            # 2) 平台分布
            plats = [dict(r) for r in c.execute("""SELECT platform,
                    COUNT(*) n, SUM(CAST(json_extract(metrics_json,'$.likes') AS INT)) likes
                    FROM content WHERE verified=1 GROUP BY platform ORDER BY n DESC""")]
            for x in plats:
                x["cn"] = PLATFORMS.get(x["platform"]) or "其他"
            # 3) 内容类型分布
            natures = [dict(r) for r in c.execute("""SELECT json_extract(analysis_json,'$.content_type') nature,
                    COUNT(*) n, SUM(CAST(json_extract(metrics_json,'$.likes') AS INT)) likes
                    FROM content WHERE (verified=1) AND json_extract(analysis_json,'$.content_type') IS NOT NULL
                    GROUP BY 1 ORDER BY n DESC LIMIT 12""")]
            # 4) 全库热词
            from collections import Counter
            allcnt = Counter()
            _stop = {"话题", "相关", "内容", "分享", "关注", "笔记", "视频", "小红书", "抖音", "今日", "头条"}
            for kr in c.execute("SELECT analysis_json FROM content WHERE (verified=1) AND analysis_json IS NOT NULL AND analysis_json!=''"):
                for w in (json.loads(kr[0]).get("keywords") or []):
                    w = (w or "").strip().strip("#").strip()
                    if isinstance(w, str) and len(w) >= 2 and w not in _stop and re.match(r"^[\u4e00-\u9fa5a-zA-Z0-9+]+$", w):
                        allcnt[w] += 1
            # 5) 互动 TOP10
            tops = []
            for r in c.execute("""SELECT id, brand, platform, title,
                    CAST(json_extract(metrics_json,'$.likes') AS INT) likes,
                    CAST(json_extract(metrics_json,'$.collects') AS INT) collects,
                    CAST(json_extract(metrics_json,'$.comments') AS INT) comments
                    FROM content WHERE verified=1 AND brand<>?
                    ORDER BY 5 DESC LIMIT 10""", (SCENE_BRAND,)):
                hc = c.execute("SELECT body, like_count FROM comment WHERE content_id=? ORDER BY like_count DESC LIMIT 1",
                               (r["id"],)).fetchone()
                tops.append({**dict(r), "platform_cn": PLATFORMS.get(r["platform"]),
                             "hot_comment": dict(hc) if hc else None})
            # 6) 高 GEO 内容（易被 AI 引用）
            geos = [dict(r) for r in c.execute("""SELECT id, brand, platform, title,
                    json_extract(analysis_json,'$.geo.score') geo,
                    CAST(json_extract(metrics_json,'$.likes') AS INT) likes
                    FROM content WHERE (verified=1) AND json_extract(analysis_json,'$.geo.score') >= 60
                    ORDER BY 5 DESC, 6 DESC LIMIT 8""")]
            for x in geos:
                x["platform_cn"] = PLATFORMS.get(x["platform"])
            # 7) 评论热词（简单切分，长度>=2 的中文片段）
            cmtcnt = Counter()
            for cr in c.execute("SELECT body FROM comment"):
                b = re.sub(r"\[[^\]]*\]", "", cr["body"] or "")  # 去掉 [偷笑R] 等表情码
                for seg in re.findall(r"[\u4e00-\u9fa5]{2,6}", b):
                    cmtcnt[seg] += 1
            v = version()
            return {"data_version": v.get("date"), "contents": v.get("contents"), "comments": v.get("comments"),
                    "brands": brands, "platforms": plats, "natures": natures,
                    "hot_keywords": allcnt.most_common(24), "tops": tops, "geo_picks": geos,
                    "comment_words": cmtcnt.most_common(20)}

        if name == "brand_compare":
            # 场景词轨道不是品牌，不进横评
            w, p = (["platform=?"], [args["platform"]]) if args.get("platform") else ([], [])
            w.append("brand<>?"); p.append(SCENE_BRAND)
            wtxt = (" AND " + " AND ".join(w)) if w else ""
            rows = c.execute(f"""SELECT brand, platform, COUNT(*) n,
                SUM(CAST(json_extract(metrics_json,'$.likes') AS INT)) likes,
                SUM(CAST(json_extract(metrics_json,'$.collects') AS INT)) collects,
                SUM(CAST(json_extract(metrics_json,'$.comments') AS INT)) comments,
                ROUND(AVG(CAST(json_extract(analysis_json,'$.seo.score') AS REAL)),1) avg_seo,
                ROUND(AVG(CAST(json_extract(analysis_json,'$.geo.score') AS REAL)),1) avg_geo,
                ROUND(AVG(CAST(json_extract(metrics_json,'$.likes') AS REAL)),0) avg_likes
                FROM content WHERE verified=1{wtxt} GROUP BY brand, platform ORDER BY brand""", p).fetchall()
            return [{**dict(r), "platform_cn": PLATFORMS.get(r["platform"])} for r in rows]

        if name == "top_content":
            w, p = ["1=1"], []
            for k in ("brand", "platform"):
                if args.get(k):
                    w.append(f"{k}=?"); p.append(args[k])
            order = {"likes": "CAST(json_extract(metrics_json,'$.likes') AS INT)",
                     "comments": "CAST(json_extract(metrics_json,'$.comments') AS INT)",
                     "seo": "CAST(json_extract(analysis_json,'$.seo.score') AS INT)",
                     "geo": "CAST(json_extract(analysis_json,'$.geo.score') AS INT)"}[args.get("by", "likes")]
            p.append(min(int(args.get("limit", 10)), 100))
            return [item_row(r) for r in c.execute(
                f"SELECT * FROM content WHERE (verified=1) AND {' AND '.join(w)} ORDER BY {order} DESC LIMIT ?", p)]

        if name == "search_comments":
            w, p = [], []
            if args.get("query"):
                w.append("c.body LIKE ?"); p.append(f"%{args['query']}%")
            if args.get("brand"):
                w.append("t.brand=?"); p.append(args["brand"])
            p.append(min(int(args.get("limit", 50)), 300))
            rows = c.execute(f"""SELECT t.brand, t.platform, c.body, c.like_count, c.publish_time, t.url
                FROM comment c JOIN content t ON c.content_id=t.id
                WHERE t.verified=1{' AND ' + ' AND '.join(w) if w else ''} ORDER BY c.like_count DESC LIMIT ?""", p).fetchall()
            return {"count": len(rows), "comments": [dict(r) for r in rows]}

        if name == "brand_knowledge":
            """品牌产品知识库：产品词、价格线索、投放分布、素材、效果，全部来自原始数据。
            自有品牌额外返回产品档案 profile（来自产品/品牌手册）。"""
            own = own_profile()
            brands = [r[0] for r in c.execute(
                "SELECT DISTINCT brand FROM content WHERE verified=1 AND brand<>? ORDER BY brand",
                (SCENE_BRAND,))] if not args.get("brand") else [args["brand"]]
            if own and own["brand"] not in brands and (args.get("brand") in (None, "", own["brand"])):
                brands = [own["brand"]] + brands
            _stop = {"测评", "品牌", "十大", "横评", "盘点", "推荐", "榜单", "效果", "哪个", "牌子",
                     "口碑", "排行", "白皮书", "选购", "指南", "深度", "年度", "主流", "热门", "领军",
                     "国际", "国内", "真实", "用户", "硬核", "权威", "必看", "合适", "靠谱", "怎么样",
                     "2026", "2025", "年", "评测", " multidimensional", "多维", "数据", "测评推荐"}
            out = []
            for b in brands:
                prof = c.execute("""SELECT COUNT(*) n,
                        SUM(CAST(json_extract(metrics_json,'$.likes') AS INT)) likes,
                        SUM(CAST(json_extract(metrics_json,'$.collects') AS INT)) collects,
                        SUM(CAST(json_extract(metrics_json,'$.comments') AS INT)) comments,
                        SUM(CAST(json_extract(metrics_json,'$.shares') AS INT)) shares,
                        ROUND(AVG(CAST(json_extract(analysis_json,'$.seo.score') AS REAL)),1) avg_seo,
                        ROUND(AVG(CAST(json_extract(analysis_json,'$.geo.score') AS REAL)),1) avg_geo
                        FROM content WHERE (verified=1) AND brand=?""", (b,)).fetchone()
                prof = dict(prof)
                for k_ in ("likes", "collects", "comments", "shares"):
                    prof[k_] = prof[k_] or 0
                # 均赞口径：只除有互动数据的条数（未抓到互动的 ≠ 0 赞），与横评报告一致
                prof["with_metrics"] = c.execute(
                    "SELECT COUNT(*) FROM content WHERE verified=1 AND brand=? "
                    "AND metrics_json IS NOT NULL AND metrics_json<>''", (b,)).fetchone()[0]
                prof["avg_likes"] = round(prof["likes"] / prof["with_metrics"], 1) if prof["with_metrics"] else 0
                # 官号 / 达人双口径：官号矩阵会显著推高条数但拉低均赞，混算会让横评失真
                for _lb, _cond in (("official", "tags LIKE '%official%'"),
                                   ("koc", "(tags IS NULL OR tags NOT LIKE '%official%')")):
                    _r = c.execute(f"""SELECT COUNT(*) n,
                            SUM(CAST(json_extract(metrics_json,'$.likes') AS INT)) likes,
                            SUM(CASE WHEN metrics_json IS NOT NULL AND metrics_json<>'' THEN 1 ELSE 0 END) wm
                            FROM content WHERE verified=1 AND brand=? AND {_cond}""", (b,)).fetchone()
                    _n, _l, _m = _r["n"] or 0, _r["likes"] or 0, _r["wm"] or 0
                    prof[_lb] = {"n": _n, "likes": _l, "with_metrics": _m,
                                 "avg_likes": round(_l / _m) if _m else 0}
                prof["recent90"] = c.execute(
                    "SELECT COUNT(*) FROM content WHERE verified=1 AND brand=? "
                    "AND publish_time>=date('now','-90 day')", (b,)).fetchone()[0]
                # 心智卡位：主题词命中占比（同一套关键词跑六个品牌，保证同口径可比）
                _TM = {"卵巢/女性": ("卵巢", "养巢", "amh", "养雌", "更年期"),
                       "护肝": ("护肝", "奶蓟", "解酒"),
                       "熬夜/睡眠": ("熬夜", "睡眠", "失眠", "入睡"),
                       "NAD/细胞能量": ("nad", "nmn", "线粒体", "辅酶", "pqq"),
                       "抗衰/逆龄": ("抗衰", "逆龄", "冻龄", "抗老", "k衰"),
                       "男性/补肾": ("补肾", "男性", "睾酮", "精倍莱", "前列腺"),
                       "榜单/SEO": ("排名", "排行榜", "十大", "榜单", "哪个好", "测评"),
                       "精力/免疫": ("精力", "免疫", "疲劳", "体力", "元气"),
                       "口服美容": ("胶原", "皮肤", "紧致", "气色"),
                       "价格": ("价格", "多少钱", "性价比", "智商税", "贵")}
                _th = {}
                for _t, _bo in c.execute("SELECT title, body FROM content WHERE verified=1 AND brand=?", (b,)):
                    _s = ((_t or "") + " " + (_bo or "")).lower()
                    for _k, _kws in _TM.items():
                        if any(_w in _s for _w in _kws):
                            _th[_k] = _th.get(_k, 0) + 1
                prof["themes"] = [{"k": _k, "pct": round(_v * 100 / max(prof["n"], 1))}
                                  for _k, _v in sorted(_th.items(), key=lambda x: -x[1])][:8]
                prof["platforms"] = [dict(x) for x in c.execute(
                    """SELECT platform, COUNT(*) n, SUM(CAST(json_extract(metrics_json,'$.likes') AS INT)) likes
                       FROM content WHERE (verified=1) AND brand=? GROUP BY platform ORDER BY n DESC""", (b,))]
                for x in prof["platforms"]:
                    x["cn"] = PLATFORMS.get(x["platform"], x["platform"])
                prof["natures"] = [dict(x) for x in c.execute(
                    """SELECT json_extract(analysis_json,'$.content_type') nature, COUNT(*) n
                       FROM content WHERE (verified=1) AND brand=? AND json_extract(analysis_json,'$.content_type') IS NOT NULL
                       GROUP BY 1 ORDER BY n DESC LIMIT 6""", (b,))]
                # 产品/主题词：从标题高频提取
                from collections import Counter
                pcnt = Counter()
                for r in c.execute("SELECT title FROM content WHERE (verified=1) AND brand=?", (b,)):
                    for seg in re.findall(r"[A-Za-z\u4e00-\u9fa5]{2,8}", (r["title"] or "").replace("#", " ")):
                        if seg not in _stop and b.lower() not in seg.lower() and seg.lower() not in b.lower():
                            pcnt[seg] += 1
                products = pcnt.most_common(10)
                # 价格线索：正文/标题里的「××元」
                prices = []
                for r in c.execute("SELECT id, title, body FROM content WHERE (verified=1) AND brand=?", (b,)):
                    text = (r["title"] or "") + " " + (r["body"] or "")
                    for mm in list(re.finditer(r"([\d,]{3,7}(?:\.\d)?)\s*元", text))[:3]:
                        s = max(0, mm.start() - 18)
                        prices.append({"id": r["id"], "price": mm.group(1),
                                       "ctx": text[s:mm.end() + 12].replace("\n", " ")})
                    if len(prices) >= 6:
                        break
                # 素材与爆款
                tops = []
                for r in c.execute("""SELECT id, platform, title,
                        CAST(json_extract(metrics_json,'$.likes') AS INT) likes,
                        CAST(json_extract(metrics_json,'$.collects') AS INT) collects,
                        media_json, url FROM content WHERE (verified=1) AND brand=?
                        ORDER BY 4 DESC LIMIT 6""", (b,)):
                    try:
                        media = json.loads(r["media_json"] or "{}")
                    except Exception:
                        media = {}
                    # 卡片缩略图只用已落地的本地/七牛地址。
                    # 不要回退成 media_json 里的原始 CDN 直链 —— 那是带签名的临时链，
                    # 24h 就 403，页面上会变成一张裂图，还会污染控制台的失败请求。
                    _ti = local_media_list(r["platform"], r["url"], b, "image")
                    _tc, _ = local_media(r["platform"], r["url"], b, media, "cover")
                    tops.append({"id": r["id"], "platform": r["platform"],
                                 "platform_cn": PLATFORMS.get(r["platform"], r["platform"]),
                                 "title": r["title"], "likes": r["likes"], "collects": r["collects"],
                                 "img": (_ti[0] if _ti else ""),
                                 "cover": _tc or ""})
                # 品牌评论声音（按赞前5）
                voices = [dict(x) for x in c.execute(
                    """SELECT cm.body, cm.like_count, ct.platform FROM comment cm
                       JOIN content ct ON ct.id=cm.content_id
                       WHERE ct.verified=1 AND ct.brand=?
                       ORDER BY cm.like_count DESC LIMIT 5""", (b,))]
                out.append({"brand": b, **prof, "products": products,
                            "prices": prices[:6], "tops": tops, "voices": voices,
                            "own": bool(own and b == own["brand"]),
                            **({"profile": own} if own and b == own["brand"] else {})})
            return {"brands": out}

        if name == "pending_content":
            from collections import Counter
            cond, p = ["platform IN ('baidu','toutiao','web')"], []
            if args.get("brand"):
                cond.append("brand=?"); p.append(args["brand"])
            if args.get("platform"):
                cond.append("platform=?"); p.append(args["platform"])
            rows = c.execute(f"SELECT * FROM content WHERE (verified=1) AND {' AND '.join(cond)}", p).fetchall()
            bad = [{"id": r["id"], "brand": r["brand"], "platform": r["platform"], "title": r["title"],
                    "url": r["url"], "level": level_of(r["body"], r["platform"])}
                   for r in rows if level_of(r["body"], r["platform"]) in ("serp", "missing", "partial")]
            return {"total": len(bad), "by_platform": dict(Counter(x["platform"] for x in bad)), "items": bad[:80]}

        if name == "kb_docs":
            return {"count": len(kb_docs()), "docs": kb_docs()}

        if name == "kb_doc":
            full = kb_safe(args.get("path", ""))
            if not full:
                return {"error": "not found or illegal path"}
            if os.path.splitext(full)[1].lower() not in DOC_EXT:
                return {"error": "only md/json docs readable via tool"}
            return {"path": args["path"], "content": open(full, encoding="utf-8").read()}

        if name == "sql_query":
            sql = (args.get("sql") or "").strip()
            if not re.match(r"(?is)^\s*(select|with)\b", sql):
                return {"error": "仅允许 SELECT / WITH 查询"}
            if re.search(r"(?i)\b(drop|delete|update|insert|alter|attach|pragma|create|replace)\b", sql):
                return {"error": "检测到写操作，已拒绝"}
            # 数据边界：只允许查已校验通过的数据
            rows = c.execute(apply_gate(sql)).fetchall()
            return {"rows": len(rows), "data": [dict(r) for r in rows[:500]]}

        return {"error": f"unknown tool: {name}"}
    finally:
        c.close()

# ---------------------------------------------------------------- http
def json_bytes(o):
    return json.dumps(o, ensure_ascii=False).encode()

class Handler(BaseHTTPRequestHandler):
    server_version = "NilingGuancha/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, code, payload, ctype="application/json; charset=utf-8", extra=None):
        data = payload if isinstance(payload, bytes) else json_bytes(payload)
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-API-Key, Mcp-Session-Id, Accept")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _auth_ok(self, q):
        cands = [self.headers.get("X-API-Key", "").strip(),
                 q.get("k", [""])[0],
                 q.get("sharecode", [""])[0],
                 self.headers.get("Authorization", "").replace("Bearer ", "").strip()]
        return any(c and c in TOKENS for c in cands)

    # 浏览类只读接口白名单：免密钥（分享链接即可看）
    # 注意：/api/pending 不在白名单 —— 它吐的是 verified=0 的待核内容（标题 + 原文链接），
    # 属于「未校验数据」，按规矩一律不对外。要看得带 token。
    PUBLIC_GET = {"/api/stats", "/api/list", "/api/item", "/api/raw", "/api/knowledge",
                  "/api/report", "/api/compare", "/api/top", "/api/comments",
                  "/api/raw_export", "/api/export",
                  "/api/xhs_archive", "/api/xhs_creators",
                  "/api/kb_docs", "/api/kb_doc", "/api/kb_media", "/api/kb_export"}

    def _public_get(self, p):
        # /api/wb/* 是工作台/星河共用的实时数据层，与 /api/stats 同级：浏览即读，不需要密钥
        return p in self.PUBLIC_GET or p.startswith("/api/wb/")

    def _deny(self):
        self._send(401, {"error": "unauthorized",
                         "hint": "需要 token：请求头 X-API-Key，或在 URL 加 ?k=<token>"})

    def _cdn_proxy(self, rel):
        """把七牛对象以同源方式输出（支持 Range，视频可拖动进度）。"""
        import urllib.request as _ur, urllib.error as _ue
        rel = (rel or "").lstrip("/")
        if not rel or ".." in rel:
            return self._send(404, {"error": "not found"})
        src = CDN_BASE + "/" + rel
        req = _ur.Request(src, headers={"User-Agent": "cosmobounty-proxy/1.0"})
        rng = self.headers.get("Range")
        if rng:
            req.add_header("Range", rng)
        try:
            r = _ur.urlopen(req, timeout=30)
        except _ue.HTTPError as e:
            return self._send(e.code, b"", "text/plain")
        except Exception:
            return self._send(502, {"error": "cdn fetch failed"})
        ctype = r.headers.get("Content-Type") or "application/octet-stream"
        clen = r.headers.get("Content-Length")
        self.send_response(206 if rng else 200)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "public, max-age=86400")
        if clen:
            self.send_header("Content-Length", clen)
        cr = r.headers.get("Content-Range")
        if cr:
            self.send_header("Content-Range", cr)
        self.end_headers()
        try:
            while True:
                chunk = r.read(65536)
                if not chunk:
                    break
                self.wfile.write(chunk)
        except Exception:
            pass

    # ---------------------------------------------------------------- 工作台实时数据层
    # 原来工作台是「构建期把 DB 烤成 list-*.js / data.js 静态快照」，数据一变就得重跑脚本再 rsync。
    # 这里改成按请求从 DB 现算：前端 <script src> 直接指过来，刷新就是最新数据。
    # 输出格式与旧静态文件逐字节兼容（var LIST_x / const DATA / window.MEDIA_CDN），页面代码不用改。
    def _wb(self, p, q):
        try:
            import wbdata
        except Exception as e:  # 缺模块时给出明确提示，不假装 200
            return self._send(500, {"error": "wbdata 不可用: %s" % e})
        name = p[len("/api/wb/"):]
        js = "application/javascript; charset=utf-8"
        slug = (q.get("brand") or q.get("slug") or [""])[0].strip().lower()
        if slug and not re.fullmatch(r"[a-z_]{2,20}", slug):
            return self._send(400, {"error": "bad brand slug"})
        try:
            if name == "data.js":
                d = wbdata.bundle()["data"]
                body = ("/* 实时生成 %s · 来源 SQLite */\nconst DATA = %s;\nwindow.DATA = DATA;\n"
                        % (d.get("generated", ""), json.dumps(d, ensure_ascii=False, separators=(",", ":"))))
                return self._send(200, body.encode(), js)
            if name == "plat.js":
                v = wbdata.bundle()["plat"]
                return self._send(200, ("const PLAT_DATA = %s;\nwindow.PLAT_DATA = PLAT_DATA;\n"
                                        % json.dumps(v, ensure_ascii=False, separators=(",", ":"))).encode(), js)
            if name == "people.js":
                v = wbdata.bundle()["people"]
                body = ("const CREATORS = %s;\nconst OFFICIAL = %s;\n"
                        "window.CREATORS = CREATORS; window.OFFICIAL = OFFICIAL;\n"
                        % (json.dumps(v["creators"], ensure_ascii=False, separators=(",", ":")),
                           json.dumps(v["official"], ensure_ascii=False, separators=(",", ":"))))
                return self._send(200, body.encode(), js)
            if name == "contents.js":
                v = wbdata.bundle()["contents"]
                return self._send(200, ("const contents = %s;\nwindow.contents = contents;\n"
                                        % json.dumps(v, ensure_ascii=False, separators=(",", ":"))).encode(), js)
            if name == "media-cdn.js":
                v = wbdata.media_map()
                return self._send(200, ("/* 七牛媒体映射（实时）· 基址 %s */\nwindow.MEDIA_CDN = %s;\n"
                                        % (wbdata.cdn_base(), json.dumps(v, ensure_ascii=False,
                                                                         separators=(",", ":")))).encode(), js)
            if name == "media-index.js":
                v = wbdata.media_stat()
                return self._send(200, ("const MEDIA_STAT = %s;\nwindow.MEDIA_STAT = MEDIA_STAT;\n"
                                        % json.dumps(v, ensure_ascii=False, separators=(",", ":"))).encode(), js)
            if name == "pending.js":
                v = wbdata.pending_map()
                return self._send(200, ("/* 待核队列（实时）*/\nconst PENDING = %s;\nwindow.PENDING = PENDING;\n"
                                        % json.dumps(v, ensure_ascii=False, separators=(",", ":"))).encode(), js)
            if name == "list.js":
                if not slug:
                    return self._send(400, {"error": "need ?brand=<slug>"})
                rows = wbdata.list_rows(slug)
                body = ("/* 实时生成 · %d 条 */\nvar LIST_%s = %s;\n"
                        "if (typeof WB_REG !== 'undefined') { WB_REG.list['%s'] = LIST_%s; }\n"
                        % (len(rows), slug, json.dumps(rows, ensure_ascii=False, separators=(",", ":")),
                           slug, slug))
                return self._send(200, body.encode(), js)
            if name == "detail.js":
                if not slug:
                    return self._send(400, {"error": "need ?brand=<slug>"})
                det = wbdata.detail_map(slug)
                body = ("var DETAIL_%s = %s;\n"
                        "if (typeof WB_REG !== 'undefined') { WB_REG.detail['%s'] = DETAIL_%s; }\n"
                        % (slug, json.dumps(det, ensure_ascii=False, separators=(",", ":")), slug, slug))
                return self._send(200, body.encode(), js)
            if name in ("summary.json", "summary"):
                return self._send(200, wbdata.bundle())
            if name == "media.json":
                return self._send(200, wbdata.media_map())
        except Exception as e:
            import traceback
            traceback.print_exc()
            return self._send(500, {"error": "wb build failed: %s" % e})
        return self._send(404, {"error": "unknown wb endpoint"})

    def do_OPTIONS(self):
        self._send(204, b"", "text/plain")

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        p = u.path

        if p in ("/", "/index.html"):
            html = open(os.path.join(HERE, "index.html"), "rb").read()
            return self._send(200, html, "text/html; charset=utf-8")

        if p == "/report/brand_v2":
            # 「竞品分析」tab 内嵌展示：优先读最新六品牌横评，缺失时回落到旧版
            for fn in ("competitor_analysis.html", "brand_analysis_v2.html"):
                fp = os.path.join(HERE, fn)
                if os.path.exists(fp):
                    with open(fp, "rb") as f:
                        return self._send(200, f.read(), "text/html; charset=utf-8")
            return self._send(404, {"error": "report not generated"})

        if p == "/health":
            v = version()
            return self._send(200, {"ok": True, "service": "niling-guancha",
                                    "data_version": v.get("date", "unknown"),
                                    "contents": v.get("contents"), "comments": v.get("comments")})

        if p == "/mcp":
            # 无状态流式 HTTP 传输：GET 仅用于探测，不提供服务器推送流
            return self._send(405, {"error": "use POST for MCP"}, extra={"Allow": "POST"})

        if p == "/kb" or p.startswith("/kb/"):
            # 知识库原文件（媒体/文档字节流）；前端 <img>/<video> 直接引用
            # 免密钥：能拿到分享链接（sharecode 过平台网关）即可读，浏览零门槛
            # 注意要 unquote：浏览器会把「W+端粒塔」「xhs:6adf...」编码成
            # W%2B… 和 xhs%3A…，不解码就永远 404。
            rel = unquote(u.path[len("/kb/"):]).strip("/")
            full = kb_safe(rel)
            if not full:
                return self._send(404, {"error": "not found"})
            ext = os.path.splitext(full)[1].lower()
            ctype = MEDIA_MIME.get(ext, "application/octet-stream")
            with open(full, "rb") as f:
                # http.server 用 latin-1 编码响应头，中文文件名塞进 filename="…"
                # 会直接 UnicodeEncodeError → 500。中文命名的产品图（38 张）线上
                # 因此一张也打不开。按 RFC 5987 用 filename*=UTF-8''<百分号编码>，
                # 同时保留一个纯 ASCII 的 filename 兜底给老客户端。
                _bn = os.path.basename(full)
                _ascii = re.sub(r"[^A-Za-z0-9._-]", "_", _bn) or "file"
                return self._send(200, f.read(), ctype,
                                  extra={"Accept-Ranges": "none",
                                         "Content-Disposition":
                                             f'inline; filename="{_ascii}"; '
                                             f"filename*=UTF-8''{quote(_bn)}"})

        if p.startswith("/note/"):
            # 本地归档的「仿原页」：小红书笔记页离线快照（正文/图片/时间都在）
            nid = p[len("/note/"):].strip("/")
            if not re.fullmatch(r"[0-9a-f]{16,32}", nid or ""):
                return self._send(404, {"error": "not found"})
            f = os.path.join(HERE, "archives", "xhs", nid + ".html")
            if not os.path.isfile(f):
                return self._send(404, {"error": "archive not found"})
            return self._send(200, open(f, "rb").read(), "text/html; charset=utf-8")

        if p.startswith("/cdn/"):
            # 媒体同源代理：七牛测试域名只有 http，https 证书不属于该域名，
            # 页面是 https 直接引用会被浏览器按混合内容拦掉。这里转发并透传 Range。
            return self._cdn_proxy(p[len("/cdn/"):])

        if p.startswith("/api/wb/"):
            return self._wb(p, q)

        if not p.startswith("/api/"):
            return self._send(404, {"error": "not found"})
        # 浏览类读接口免密钥：分享链接的 sharecode 过平台网关即是凭证；
        # 密钥仅保留给写型/高级接口（POST /api/sql、POST /mcp）
        if not self._auth_ok(q) and not self._public_get(p):
            return self._deny()

        try:
            if p == "/api/kb_docs":
                return self._send(200, {"count": len(kb_docs()), "docs": kb_docs()})
            if p == "/api/kb_doc":
                full = kb_safe(q.get("p", [""])[0])
                if not full:
                    return self._send(404, {"error": "not found"})
                ext = os.path.splitext(full)[1].lower()
                if ext == ".json":
                    return self._send(200, json.load(open(full, encoding="utf-8")))
                return self._send(200, {"path": q.get("p", [""])[0],
                                        "content": open(full, encoding="utf-8").read()})
            if p == "/api/kb_media":
                return self._send(200, {"count": len(kb_media_list()), "items": kb_media_list(),
                                        "manifest_expired": sum(1 for x in kb_manifest()["items"]
                                                               if str(x.get("status", "")).startswith("expired"))})
            if p == "/api/kb_export":
                # 知识库文档打包下载（md/json 全量，媒体过大不打包，单文件走 /kb/）
                import io as _io, zipfile as _zip
                buf = _io.BytesIO()
                with _zip.ZipFile(buf, "w", _zip.ZIP_DEFLATED) as z:
                    for d in kb_docs():
                        full = kb_safe(d["path"])
                        if full:
                            z.write(full, d["path"])
                return self._send(200, buf.getvalue(), "application/zip",
                                  extra={"Content-Disposition": "attachment; filename=niling-knowledge.zip"})
            if p == "/api/stats":
                return self._send(200, call_tool("content_stats", {}))
            if p == "/api/list":
                args = {"query": q.get("q", [""])[0], "brand": q.get("brand", [""])[0],
                        "platform": q.get("platform", [""])[0], "nature": q.get("ctype", [""])[0],
                        "limit": 400}
                d = call_tool("search_content", args)
                sort = q.get("sort", ["likes"])[0]
                if sort in ("seo", "geo") and isinstance(d, dict):
                    key = sort
                    d["items"].sort(key=lambda x: (x.get(key) or 0), reverse=True)
                elif sort == "time" and isinstance(d, dict):
                    d["items"].sort(key=lambda x: x.get("publish_time") or "", reverse=True)
                return self._send(200, d)
            if p == "/api/guide":
                # 远程下发分析口径：技能无需重装即可同步最新框架与报告规范
                gp = os.path.join(HERE, "guide.md")
                md = open(gp, encoding="utf-8").read() if os.path.exists(gp) else ""
                ver = "unknown"
                m = re.search(r"口径版本：\s*([^\s]+)", md)
                if m:
                    ver = m.group(1)
                mv = re.search(r"技能最低版本：\s*([^\s]+)", md)
                return self._send(200, {"guide_version": ver,
                                        "skill_min_version": mv.group(1) if mv else None,
                                        "data_version": version().get("date", "unknown"),
                                        "markdown": md})
            if p == "/api/item":
                return self._send(200, call_tool("get_content", {"id": int(q.get("id", ["0"])[0])}))
            if p == "/api/raw":
                return self._send(200, call_tool("raw_table", {
                    "brand": q.get("brand", [""])[0], "platform": q.get("platform", [""])[0],
                    "limit": int(q.get("limit", ["200"])[0]),
                    "offset": int(q.get("offset", ["0"])[0])}))
            if p == "/api/knowledge":
                return self._send(200, call_tool("brand_knowledge", {"brand": q.get("brand", [""])[0]}))
            if p == "/api/report":
                return self._send(200, call_tool("analysis_report", {}))
            if p == "/api/compare":
                return self._send(200, call_tool("brand_compare", {"platform": q.get("platform", [""])[0]}))
            if p == "/api/top":
                return self._send(200, call_tool("top_content", {
                    "brand": q.get("brand", [""])[0], "platform": q.get("platform", [""])[0],
                    "by": q.get("by", ["likes"])[0], "limit": int(q.get("limit", ["10"])[0])}))
            if p == "/api/comments":
                return self._send(200, call_tool("search_comments", {
                    "query": q.get("q", [""])[0], "brand": q.get("brand", [""])[0],
                    "limit": int(q.get("limit", ["50"])[0])}))
            if p == "/api/pending":
                return self._send(200, call_tool("pending_content", {
                    "brand": q.get("brand", [""])[0], "platform": q.get("platform", [""])[0]}))
            if p in ("/api/xhs_archive", "/api/xhs_creators"):
                # 小红书完整归档导出：
                #   /api/xhs_archive  → 3592 条笔记档案（含正文/标签/时间/达人/证据分级）
                #   /api/xhs_creators → 达人账号表（昵称/主页/发帖量/赞藏评/涉及品牌）
                # 这批数据体量远大于 content 表（品牌维度），供深度分析用
                import csv, io, json as _json
                c = conn()
                try:
                    s_ = io.StringIO()
                    w = csv.writer(s_)
                    if p == "/api/xhs_archive":
                        w.writerow(["笔记ID", "品牌", "归属", "证据强度", "标题", "正文",
                                    "发布时间", "作者", "账号ID", "点赞", "收藏", "评论", "分享",
                                    "话题标签", "图片数", "视频链接", "笔记类型", "采集词",
                                    "原文链接", "主页链接"])
                        for r in c.execute(
                                """SELECT note_id, brand, brand_mention, evidence, title, body,
                                          publish_time, author, author_id, likes, collects, comments,
                                          shares, tags_json, images_json, video_url, note_type,
                                          source_keyword, note_url
                                   FROM xhs_note WHERE verified=1 ORDER BY publish_time DESC"""):
                            try:
                                tg = _json.loads(r["tags_json"] or "[]")
                            except Exception:
                                tg = []
                            try:
                                im = _json.loads(r["images_json"] or "[]")
                            except Exception:
                                im = []
                            w.writerow([
                                r["note_id"], r["brand"] or "",
                                "已归属" if r["brand_mention"] else "未归属",
                                {"strong": "强证据", "weak": "弱证据", "tag": "蹭词位"}.get(
                                    r["evidence"] or "", ""),
                                r["title"] or "", r["body"] or "", r["publish_time"] or "",
                                r["author"] or "", r["author_id"] or "",
                                r["likes"] or 0, r["collects"] or 0, r["comments"] or 0,
                                r["shares"] or 0,
                                "、".join(tg), len(im), r["video_url"] or "",
                                r["note_type"] or "", r["source_keyword"] or "",
                                r["note_url"] or "",
                                (f"https://www.xiaohongshu.com/user/profile/{r['author_id']}"
                                 if r["author_id"] else ""),
                            ])
                    else:
                        w.writerow(["平台", "账号ID", "昵称", "主页链接", "发帖数", "点赞",
                                    "收藏", "评论", "最早发布", "最新发布", "涉及品牌"])
                        for r in c.execute(
                                """SELECT platform, creator_id, nickname, profile_url, note_count,
                                          total_likes, total_collects, total_comments,
                                          first_publish, last_publish, brands
                                   FROM creator WHERE creator_id IN
                                         (SELECT DISTINCT author_id FROM xhs_note
                                          WHERE verified=1 AND author_id!='')
                                   ORDER BY note_count DESC"""):
                            w.writerow(list(r))
                finally:
                    c.close()
                fn = ("xhs-archive-3592.csv" if p == "/api/xhs_archive"
                      else "xhs-creators.csv")
                return self._send(200, s_.getvalue().encode("utf-8-sig"), "text/csv",
                                  extra={"Content-Disposition":
                                         f"attachment; filename={fn}"})
            if p == "/api/raw_export":
                # 页面「原始数据」宽表的完整版 CSV（全量行、不截断），供直接下载
                # 单元格内不含换行符 → Excel 打开时一行数据就是一行，不会被撑高
                import csv, io
                def _flat(v):
                    if v is None:
                        return ""
                    return " | ".join(str(v).replace("\r\n", "\n").replace("\r", "\n").split("\n"))
                c = conn()
                try:
                    rows = c.execute("SELECT c.* FROM content c WHERE verified=1 ORDER BY c.id").fetchall()
                    s = io.StringIO()
                    w = csv.writer(s)
                    w.writerow(["ID", "品牌", "渠道", "标题", "正文内容", "作者", "发布时间",
                                "图片链接", "视频链接", "图片识别内容(OCR)", "视频识别内容(ASR)",
                                "SEO词", "SEO分", "GEO词", "GEO分", "意图词", "内容类型",
                                "原文链接", "赞", "藏", "评", "转发", "评论内容(按赞排序)"])
                    for r in rows:
                        try:
                            media = json.loads(r["media_json"] or "{}")
                        except Exception:
                            media = {}
                        mts = {}
                        try:
                            for x in c.execute("SELECT kind, source, text FROM media_text WHERE content_id=? ORDER BY source",
                                               (r["id"],)):
                                mts.setdefault(x["kind"], []).append(f"[{x['source']}] {x['text']}")
                        except sqlite3.OperationalError:
                            pass
                        a = json.loads(r["analysis_json"] or "{}") if "analysis_json" in r.keys() else {}
                        seo_obj = a.get("seo") or {}
                        geo_obj = a.get("geo") or {}
                        imgs = media.get("images") or []
                        # 导出的 CSV 是离线用的：原始抖音/小红书直链几小时就过期，
                        # 写进去等于给一堆死链。已落地的改用七牛 CDN 稳定地址。
                        _lv, _ = local_media(r["platform"], r["url"], r["brand"], media, "video")
                        _lis = local_media_list(r["platform"], r["url"], r["brand"], "image")
                        _abs = lambda u: (CDN_BASE.rstrip("/") + u[4:]) if u.startswith("/cdn/") else u
                        if _lis:
                            imgs = [_abs(u) for u in _lis]
                        vid_out = _abs(_lv) if _lv else (media.get("video") or media.get("cover") or "")
                        m = json.loads(r["metrics_json"] or "{}")
                        cmts = [f"{x['body']}（赞{int(x['like_count'] or 0)}）" for x in c.execute(
                            "SELECT body, like_count FROM comment WHERE content_id=? "
                            "ORDER BY like_count DESC LIMIT 30", (r["id"],))]
                        w.writerow([
                            r["id"], r["brand"], r["platform"], _flat(r["title"]),
                            _flat((r["body"] or "").strip()), r["author"], r["publish_time"],
                            _flat("\n".join(imgs)), _flat(vid_out),
                            _flat("\n".join(mts.get("ocr", []))), _flat("\n".join(mts.get("asr", []))),
                            _flat("、".join(a.get("keywords") or [])),
                            seo_obj.get("score", ""),
                            _flat("、".join((a.get("ingredients") or []) + (a.get("efficacy") or []))),
                            geo_obj.get("score", ""),
                            _flat("、".join(a.get("intent") or [])),
                            a.get("content_type") or "",
                            r["url"],
                            m.get("likes", 0), m.get("collects", 0),
                            m.get("comments", 0), m.get("shares", 0),
                            _flat("\n".join(cmts)),
                        ])
                finally:
                    c.close()
                return self._send(200, s.getvalue().encode("utf-8-sig"), "text/csv",
                                  extra={"Content-Disposition":
                                         "attachment; filename=niling-raw-data.csv"})
            if p == "/api/export":
                # 原始数据快照：全表 CSV + schema + 版本信息，打包 zip 下载
                # 供朋友把数据拖进自己的 WorkBuddy / Excel / BI 做本地分析
                # 同样去掉单元格内换行，保证 Excel 里一行数据就是一行
                import csv, io, zipfile
                def _flat(v):
                    if v is None:
                        return ""
                    return " | ".join(str(v).replace("\r\n", "\n").replace("\r", "\n").split("\n"))
                c = conn()
                try:
                    buf = io.BytesIO()
                    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                        tables = [r[0] for r in c.execute(
                            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
                        for t in tables:
                            rows = c.execute(f"SELECT * FROM {t}").fetchall()
                            cols = rows[0].keys() if rows else [d[1] for d in c.execute(f"PRAGMA table_info({t})")]
                            s = io.StringIO()
                            w = csv.writer(s)
                            w.writerow(cols)
                            for r in rows:
                                w.writerow([_flat("" if r[k] is None else r[k]) for k in cols])
                            z.writestr(f"{t}.csv", s.getvalue().encode("utf-8-sig"))
                        v = version()
                        z.writestr("version.json", json_bytes(v))
                        schema = "\n".join(
                            f"## {t}\n" + "\n".join(f"- {d[1]} ({d[2]})" for d in c.execute(f"PRAGMA table_info({t})"))
                            for t in tables)
                        z.writestr("schema.md", f"# 逆龄观察 数据字典\n\n数据版本：{v.get('date','unknown')}\n\n{schema}\n")
                finally:
                    c.close()
                return self._send(200, buf.getvalue(), "application/zip",
                                  extra={"Content-Disposition": "attachment; filename=niling-data.zip"})
            return self._send(404, {"error": "not found"})
        except Exception as e:
            return self._send(500, {"error": str(e)[:200]})

    def do_POST(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""

        if u.path == "/mcp":
            if not self._auth_ok(q):
                return self._deny()
            try:
                msg = json.loads(raw or b"{}")
            except Exception:
                return self._send(400, {"jsonrpc": "2.0", "id": None,
                                        "error": {"code": -32700, "message": "parse error"}})
            return self._mcp(msg, q)

        if u.path == "/api/sql":
            if not self._auth_ok(q):
                return self._deny()
            try:
                body = json.loads(raw or b"{}")
            except Exception:
                return self._send(400, {"error": "invalid json"})
            return self._send(200, call_tool("sql_query", {"sql": body.get("sql", "")}))

        return self._send(404, {"error": "not found"})

    # ---- MCP 无状态传输
    def _mcp(self, msg, q):
        if isinstance(msg, list):
            out = [x for x in (self._mcp_one(m) for m in msg) if x is not None]
            return self._send(200, out)
        resp = self._mcp_one(msg)
        if resp is None:
            return self._send(202, b"", "text/plain")
        accept = (self.headers.get("Accept") or "")
        if "text/event-stream" in accept:
            payload = f"event: message\ndata: {json.dumps(resp, ensure_ascii=False)}\n\n".encode()
            return self._send(200, payload, "text/event-stream",
                              extra={"Mcp-Session-Id": uuid.uuid4().hex})
        return self._send(200, resp, extra={"Mcp-Session-Id": uuid.uuid4().hex})

    def _mcp_one(self, msg):
        mid, method = msg.get("id"), msg.get("method")
        if method == "initialize":
            return {"jsonrpc": "2.0", "id": mid, "result": {
                "protocolVersion": (msg.get("params") or {}).get("protocolVersion", "2024-11-05"),
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "cosmobounty", "version": "1.0.0",
                               "title": "逆龄观察 · 每日大健康内容情报"}}}
        if method in ("notifications/initialized", "notifications/cancelled"):
            return None
        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}}
        if method == "tools/call":
            prm = msg.get("params") or {}
            try:
                result = {"content": [{"type": "text",
                                       "text": json.dumps(call_tool(prm.get("name"), prm.get("arguments") or {}),
                                                          ensure_ascii=False, indent=1)}]}
            except Exception as e:
                result = {"content": [{"type": "text", "text": json.dumps({"error": str(e)}, ensure_ascii=False)}],
                          "isError": True}
            return {"jsonrpc": "2.0", "id": mid, "result": result}
        if method == "ping":
            return {"jsonrpc": "2.0", "id": mid, "result": {}}
        if mid is None:
            return None
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"method not found: {method}"}}

if __name__ == "__main__":
    if not os.path.exists(DB):
        sys.exit(f"数据库不存在: {DB}")
    HOST = os.environ.get("CB_HOST", "0.0.0.0")
    print(f"[cosmobounty] {DB}  {HOST}:{PORT}  token={TOKEN[:6]}…", flush=True)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
