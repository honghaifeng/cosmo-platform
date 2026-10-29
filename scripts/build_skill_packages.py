#!/usr/bin/env python3
"""打包两个技能安装包
  1) 内部版 niling-guancha-skill.zip      —— 内置服务地址与密钥，朋友装完即用
  2) 公开版 niling-guancha-skill-public.zip —— 不含密钥，供开放平台上架审核用
用法: python3 scripts/build_skill_packages.py
"""
import os
import shutil
import tempfile
import zipfile

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL = os.path.join(PROJ, "skills", "cosmobounty-insight")
OUT = os.path.join(PROJ, "exports")


def build(dst_name, drop_service_json=False):
    tmp = tempfile.mkdtemp()
    root = os.path.join(tmp, "cosmobounty-insight")
    shutil.copytree(SKILL, root, ignore=shutil.ignore_patterns("__pycache__", ".DS_Store"))
    if drop_service_json:
        p = os.path.join(root, "scripts", "service.json")
        if os.path.exists(p):
            os.remove(p)
    os.makedirs(OUT, exist_ok=True)
    dst = os.path.join(OUT, dst_name)
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as z:
        for base, _, files in os.walk(root):
            for f in files:
                fp = os.path.join(base, f)
                z.write(fp, os.path.relpath(fp, tmp))
    shutil.rmtree(tmp)
    return dst


if __name__ == "__main__":
    a = build("niling-guancha-skill.zip")
    b = build("niling-guancha-skill-public.zip", drop_service_json=True)
    for p in (a, b):
        with zipfile.ZipFile(p) as z:
            names = z.namelist()
        print(f"{os.path.basename(p):38s} {os.path.getsize(p)/1024:6.1f} KB  {len(names)} 个文件")
        assert names[0].startswith("cosmobounty-insight/"), names[0]
    print("两个包根目录结构均为 cosmobounty-insight/，可直接上传")
