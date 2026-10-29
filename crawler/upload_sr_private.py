#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
星河私有素材 → 七牛私有空间 sr-private

为什么单独一个脚本：
  星河里的图/视频/附件是客户私有素材（AI 生成图、上传的原始件），
  不能丢进 chunke 这个公开空间（一上传就等于对外裸奔）。
  所以走 sr-private 私有空间，页面访问时由 server.js 现签 URL。

用法：
  python3 crawler/upload_sr_private.py              # 增量上传（已传的跳过）
  python3 crawler/upload_sr_private.py --force      # 全量重传
  python3 crawler/upload_sr_private.py --verify     # 只校验签名可达，不上传

产物：data/sr_media.json   {"/img/xxx.jpeg": "sr/img/xxx.jpeg", ...}
      server.js 的 lib/sr-cdn.js 读它做路径映射。
"""
import os
import sys
import json
import time
import base64
import hashlib
import hmac
import mimetypes
import urllib.request
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _starriver_dir():
    """星河代码目录。它在另一个仓库里，不假定目录名。
    历史目录叫 starbox-clone，后来叫 starriver，两种都认；
    显式指定用环境变量 STARRIVER_PATH。"""
    env = os.environ.get("STARRIVER_PATH")
    if env:
        return env
    for name in ("starbox-clone", "starriver"):
        cand = os.path.join(ROOT, "..", name)
        if os.path.isdir(cand):
            return os.path.abspath(cand)
    return os.path.abspath(os.path.join(ROOT, "..", "starriver"))


STARBOX = _starriver_dir()
BUCKET = "sr-private"
# z0（华东）的上传入口；换空间要一起换 region
UP_HOSTS = ["https://upload.qiniup.com", "https://upload-xs.qiniup.com", "https://upload-jjh.qiniup.com"]
# 自有备案域名 + Let's Encrypt 证书（2026-10-29 接入）。
# 原来是测试域名 http://tnnrc27pt.hd-bkt.clouddn.com —— 只支持 HTTP、七牛不给签证书，
# 站点一旦上 HTTPS 就会全量混合内容告警，所以换成自有域名。改回测试域名 = 退回混内容状态。
# 证书由 certbot + scripts/acme/acme_aliyun.py（阿里云 DNS-01）签发，续期见 scripts/acme/renew.sh
HOST = "https://sr.pianai.love"
KINDS = [("images", "img"), ("videos", "vid"), ("uploads", "file")]
OUT = os.path.join(ROOT, "data", "sr_media.json")
# server.js 读这份：host + 密钥 + 路径映射。权限 600，且在 starbox 的 data/ 下（已被 gitignore）
CONF_OUT = os.path.join(STARBOX, "data", "sr-cdn.json")


def creds():
    cfg = json.load(open(os.path.join(ROOT, "config", "qiniu.json"), encoding="utf-8"))
    return cfg["AK"], cfg["SK"]


def b64(b):
    return base64.urlsafe_b64encode(b).decode()


def uptoken(ak, sk, key):
    policy = json.dumps({"scope": f"{BUCKET}:{key}", "deadline": int(time.time()) + 3600},
                        separators=(",", ":"))
    e = b64(policy.encode())
    return f"{ak}:{b64(hmac.new(sk.encode(), e.encode(), hashlib.sha1).digest())}:{e}"


def upload(ak, sk, key, path):
    data = open(path, "rb").read()
    bd = "----srb" + hashlib.md5(key.encode()).hexdigest()[:16]
    ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
    parts = []
    for n, v in (("key", key), ("token", uptoken(ak, sk, key))):
        parts.append(f"--{bd}\r\nContent-Disposition: form-data; name=\"{n}\"\r\n\r\n{v}\r\n".encode())
    parts.append((f"--{bd}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{os.path.basename(path)}\"\r\n"
                  f"Content-Type: {ctype}\r\n\r\n").encode() + data + b"\r\n")
    parts.append(f"--{bd}--\r\n".encode())
    body = b"".join(parts)
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    last = None
    for host in UP_HOSTS:
        try:
            req = urllib.request.Request(host, data=body, method="POST",
                                         headers={"Content-Type": f"multipart/form-data; boundary={bd}"})
            with op.open(req, timeout=180) as r:
                return r.status, json.loads(r.read().decode())
        except Exception as e:
            last = getattr(e, "code", 0), (e.read().decode()[:200] if hasattr(e, "read") else str(e))
    return 0, {"error": str(last)}


def sign_url(ak, sk, key, ttl=7200, window=3600):
    """签一个下载地址。e 按 window 对齐 —— 同一窗口内 URL 完全一致，
    这样七牛 CDN 才能命中缓存，否则每个请求都是回源。"""
    now = int(time.time())
    e = (now // window + 1) * window + ttl
    to_sign = f"/{key}?e={e}\n"
    tok = ak + ":" + b64(hmac.new(sk.encode(), to_sign.encode(), hashlib.sha1).digest())
    return f"{HOST}/{urllib.parse.quote(key)}?e={e}&token={tok}"


def collect():
    out = []
    for sub, kind in KINDS:
        d = os.path.join(STARBOX, "data", sub)
        if not os.path.isdir(d):
            continue
        for f in sorted(os.listdir(d)):
            p = os.path.join(d, f)
            if os.path.isfile(p) and not f.startswith("."):
                out.append((kind, f, p))
    return out


def main():
    ak, sk = creds()
    files = collect()
    total = sum(os.path.getsize(p) for _, _, p in files)
    print(f"[sr-private] {len(files)} 个文件 / {total / 1e6:.1f} MB", flush=True)

    mapping = {}
    if os.path.exists(OUT):
        try:
            mapping = json.load(open(OUT, encoding="utf-8"))
        except Exception:
            mapping = {}

    if "--verify" in sys.argv:
        ok = fail = 0
        for kind, f, _ in files[:20]:
            key = mapping.get(f"/{kind}/{f}") or f"sr/{kind}/{f}"
            u = sign_url(ak, sk, key)
            try:
                op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                with op.open(u, timeout=20) as r:
                    r.read(64)
                    ok += 1
            except Exception as e:
                fail += 1
                print("  FAIL", key, getattr(e, "code", e))
        print(f"verify: ok={ok} fail={fail}")
        return

    force = "--force" in sys.argv
    done = [0]
    fail = []
    lock = __import__("threading").Lock()

    def work(item):
        kind, f, p = item
        rel = f"{kind}/{f}"
        key = f"sr/{rel}"
        if not force and mapping.get(f"/{kind}/{f}") == key:
            with lock:
                done[0] += 1
            return
        st, body = upload(ak, sk, key, p)
        with lock:
            if st == 200 and body.get("key"):
                mapping[f"/{kind}/{f}"] = key
                done[0] += 1
                if done[0] % 25 == 0:
                    print(f"  {done[0]}/{len(files)}", flush=True)
            else:
                fail.append((rel, st, body))

    with ThreadPoolExecutor(max_workers=6) as ex:
        list(ex.map(work, files))

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(mapping, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    write_conf(ak, sk, mapping)
    print(f"完成 {done[0]}/{len(files)}，失败 {len(fail)}，映射表 {len(mapping)} 条 → {OUT}")
    for x in fail[:10]:
        print("  FAIL", x)


def write_conf(ak, sk, mapping):
    """写出 server.js 用的签名配置。密钥文件权限 600，别提交进仓库。"""
    os.makedirs(os.path.dirname(CONF_OUT), exist_ok=True)
    conf = {"bucket": BUCKET, "host": HOST, "ak": ak, "sk": sk,
            "ttl": 7200, "window": 3600, "map": mapping}
    tmp = CONF_OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(conf, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, CONF_OUT)
    os.chmod(CONF_OUT, 0o600)
    print(f"签名配置 → {CONF_OUT}（{len(mapping)} 条，权限 600）")


if __name__ == "__main__":
    main()
