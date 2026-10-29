#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
certbot manual hook：用阿里云云解析 API 完成 DNS-01 验证。

为什么不用 HTTP-01（项目里 acme_qiniu.py 那套）：
  那套是把挑战文件塞进源站 bucket，让 LE 经 CDN 回源读取 —— 前提是 bucket 公开可读。
  sr.pianai.love 的源站是 **sr-private 私有空间**，匿名访问一律 401，LE 取不到文件。
  所以只能走 DNS-01：往权威 DNS 写一条 TXT，LE 直接查 DNS 验证域名归属。

用法（由 certbot 调用，不要手动跑）：
  auth    : 加 TXT  _acme-challenge.<sub>  CERTBOT_VALIDATION
  cleanup : 删掉刚加的 TXT，别把 zone 弄脏

环境变量：CERTBOT_DOMAIN / CERTBOT_VALIDATION（certbot 注入）
可选：CB_ZONE 覆盖主域名（默认 pianai.love）
      CB_DNS_WAIT 授权后等待秒数（默认 15，阿里云权威一般 5-10s 生效）
"""
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CLI = os.path.join(ROOT, "scripts", "aliyun_dns.py")
PY = sys.executable

ZONE = os.environ.get("CB_ZONE", "pianai.love")


def run(*args):
    r = subprocess.run([PY, CLI, *args], capture_output=True, text=True, cwd=ROOT)
    out = (r.stdout or "") + (r.stderr or "")
    if r.returncode != 0:
        raise SystemExit(f"aliyun_dns.py {' '.join(args)} 失败：{out}")
    return out


def rr_for(domain):
    """sr.pianai.love → _acme-challenge.sr；pianai.love → _acme-challenge"""
    if domain == ZONE:
        return "_acme-challenge"
    sub = domain[: -(len(ZONE) + 1)]
    return "_acme-challenge." + sub


def find_records(domain):
    """找出该域名下所有 _acme-challenge TXT 的 RecordId（cleanup 用）"""
    rr = rr_for(domain)
    out = run("list", ZONE, "--rr", rr)
    ids = []
    for line in out.splitlines():
        parts = line.split()
        # 表头是 RR TYPE VALUE TTL ID；数据行最后一列是 RecordId
        if len(parts) >= 5 and parts[0] == rr and parts[1] == "TXT":
            ids.append(parts[-1])
    return ids


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    op = sys.argv[1]
    domain = os.environ.get("CERTBOT_DOMAIN", "")
    if not domain:
        raise SystemExit("缺少 CERTBOT_DOMAIN")

    if op == "auth":
        val = os.environ.get("CERTBOT_VALIDATION", "")
        if not val:
            raise SystemExit("缺少 CERTBOT_VALIDATION")
        rr = rr_for(domain)
        print(f"[acme] 添加 TXT {rr}.{ZONE} → {val[:12]}…")
        run("add", ZONE, rr, "TXT", val, "--ttl", "600")
        wait = int(os.environ.get("CB_DNS_WAIT", "15"))
        print(f"[acme] 等待 DNS 传播 {wait}s")
        time.sleep(wait)

    elif op == "cleanup":
        for rid in find_records(domain):
            print(f"[acme] 删除 TXT 记录 {rid}")
            try:
                run("del", ZONE, rid)
            except SystemExit as e:
                print(f"[acme] 删除失败（忽略）：{e}")

    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
