#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把本地媒体（视频/封面/笔记图片）上传到七牛云，输出 CDN 映射表。

为什么需要：
  发布包装不下 3.2GB 媒体，抖音 CDN 签名链 24h 就过期，
  所以线上「视频/图片」列一直是死链。传到对象存储后才有稳定可播地址。

产物：
  data/media_cdn.json   {本地相对路径: CDN URL}
  service/data/media_cdn.json  同上（供线上服务读取）

用法：
  python3 crawler/upload_qiniu.py --probe            # 只试传 1 个文件验证配置
  python3 crawler/upload_qiniu.py                    # 全量上传（跳过已传）
  python3 crawler/upload_qiniu.py --kinds video      # 只传视频
密钥读取顺序：环境变量 QINIU_AK/QINIU_SK → config/qiniu.json
"""
import argparse
import base64
import hashlib
import hmac
import json
import mimetypes
import os
import queue
import sys
import threading
import time
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MEDIA = os.path.join(ROOT, "knowledge", "media")
OUT = os.path.join(ROOT, "data", "media_cdn.json")
OUT_SVC = os.path.join(ROOT, "service", "data", "media_cdn.json")
CFG = os.path.join(ROOT, "config", "qiniu.json")

BUCKET = "chunke"
DOMAIN = "http://tni044vir.hn-bkt.clouddn.com"   # 测试域名只有 HTTP，https 不通（别改回 https）
UPLOAD_HOST = "https://upload-z2.qiniup.com"   # 华南区


def creds():
    ak = os.environ.get("QINIU_AK", "").strip()
    sk = os.environ.get("QINIU_SK", "").strip()
    if ak and sk:
        return ak, sk
    if os.path.exists(CFG):
        d = json.load(open(CFG, encoding="utf-8"))
        return d.get("AK", "").strip(), d.get("SK", "").strip()
    raise SystemExit("缺少七牛密钥：设置 QINIU_AK/QINIU_SK 或写 config/qiniu.json")


def token_for(key, ak, sk, ttl=3600):
    policy = json.dumps({"scope": f"{BUCKET}:{key}", "deadline": int(time.time()) + ttl},
                        separators=(",", ":"))
    enc = base64.urlsafe_b64encode(policy.encode()).decode()
    sign = hmac.new(sk.encode(), enc.encode(), hashlib.sha1).digest()
    return f"{ak}:{base64.urlsafe_b64encode(sign).decode()}:{enc}"


def key_of(rel):
    """本地相对路径 → 存储 key（: 换成 _，避免 URL 编码问题）"""
    return "media/" + rel.replace(os.sep, "/").replace(":", "_")


def upload(path, rel, ak, sk):
    key = key_of(rel)
    tok = token_for(key, ak, sk)
    boundary = "----cb" + hashlib.md5(key.encode()).hexdigest()[:16]
    ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
    with open(path, "rb") as f:
        data = f.read()
    parts = []
    for name, val in (("key", key), ("token", tok)):
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{val}\r\n".encode())
    parts.append(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
        f"filename=\"{os.path.basename(path)}\"\r\nContent-Type: {ctype}\r\n\r\n".encode()
        + data + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    body = b"".join(parts)
    req = urllib.request.Request(UPLOAD_HOST, data=body, method="POST",
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=180) as r:
        r.read()
    return f"{DOMAIN}/{urllib.parse.quote(key)}"


def collect(kinds):
    """收集 knowledge/media 下所有要上云的媒体。

    2026-10-29 扩到全目录：以前只认 video/cover/post，产品图（product）、
    品牌 logo、ref 定稿图（含三级子目录）从来没传过 —— 页面上一显示就是本机路径。
    """
    out = []
    KIND = {"video": "video", "cover": "cover", "post": "image"}
    for brand in sorted(os.listdir(MEDIA)):
        bdir = os.path.join(MEDIA, brand)
        if not os.path.isdir(bdir):
            continue
        for root, _dirs, files in os.walk(bdir):
            sub = os.path.relpath(root, bdir).split(os.sep)[0]
            kind = KIND.get(sub, "asset")
            if kinds and kind not in kinds:
                continue
            for f in sorted(files):
                if f.startswith(".") or f.endswith(".json"):
                    continue
                p = os.path.join(root, f)
                if os.path.isfile(p):
                    out.append((p, os.path.relpath(p, MEDIA)))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--kinds", default="", help="video,cover,image 逗号分隔")
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()
    kinds = {k.strip() for k in args.kinds.split(",") if k.strip()}

    ak, sk = creds()
    files = collect(kinds)
    if args.probe:
        files = files[:2]
    print(f"待上传 {len(files)} 个文件", flush=True)

    mapping = {}
    if os.path.exists(OUT):
        try:
            mapping = json.load(open(OUT, encoding="utf-8"))
        except Exception:
            mapping = {}

    q = queue.Queue()
    for p, rel in files:
        if rel in mapping:
            continue
        q.put((p, rel))
    total = q.qsize()
    print(f"需新传 {total}（已传 {len(files) - total}）", flush=True)

    done = [0]
    lock = threading.Lock()
    fails = []

    def worker():
        while True:
            try:
                p, rel = q.get_nowait()
            except queue.Empty:
                return
            for attempt in range(3):
                try:
                    url = upload(p, rel, ak, sk)
                    with lock:
                        mapping[rel] = url
                        done[0] += 1
                        if done[0] % 10 == 0:
                            print(f"  {done[0]}/{total} 已传", flush=True)
                            json.dump(mapping, open(OUT, "w"), ensure_ascii=False, indent=0)
                    break
                except Exception as e:
                    if attempt == 2:
                        with lock:
                            fails.append((rel, str(e)[:100]))
                    else:
                        time.sleep(2 + attempt * 2)
            q.task_done()

    ths = [threading.Thread(target=worker, daemon=True) for _ in range(args.workers)]
    [t.start() for t in ths]
    [t.join() for t in ths]

    json.dump(mapping, open(OUT, "w"), ensure_ascii=False, indent=0)
    os.makedirs(os.path.dirname(OUT_SVC), exist_ok=True)
    json.dump(mapping, open(OUT_SVC, "w"), ensure_ascii=False, indent=0)

    print(f"\n完成：本次新传 {done[0]}，累计映射 {len(mapping)}，失败 {len(fails)}")
    for r, e in fails[:10]:
        print("  失败:", r, e)
    if mapping:
        sample = next(iter(mapping.values()))
        print("样例 CDN:", sample)


if __name__ == "__main__":
    main()
