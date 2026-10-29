#!/usr/bin/env python3
"""把主库 + 知识库同步到服务目录 + 写版本信息 + 生成/复用分享密钥

用法：python3 scripts/sync_service.py
日常更新脚本（daily_update.sh）最后一步自动调用它。
"""
import json, os, shutil, sqlite3, datetime, secrets

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(BASE, "db", "cosmobounty.db")
KB_SRC = os.path.join(BASE, "knowledge")
KB_DST = os.path.join(BASE, "service", "knowledge")
DST_DIR = os.path.join(BASE, "service", "data")
DST = os.path.join(DST_DIR, "cosmobounty.db")
TOKEN_FILE = os.path.join(DST_DIR, "token.txt")
VERSION_FILE = os.path.join(DST_DIR, "version.json")


def main():
    os.makedirs(DST_DIR, exist_ok=True)

    # 用 sqlite backup API 做一致性拷贝（主库可能正被写入）
    src = sqlite3.connect(SRC)
    if os.path.exists(DST):
        os.remove(DST)
    dst = sqlite3.connect(DST)
    src.backup(dst)
    src.close()

    n_content = dst.execute("SELECT COUNT(*) FROM content").fetchone()[0]
    n_comment = dst.execute("SELECT COUNT(*) FROM comment").fetchone()[0]
    by = {r[0]: r[1] for r in dst.execute("SELECT platform, COUNT(*) FROM content GROUP BY 1")}
    dst.close()

    if not os.path.exists(TOKEN_FILE):
        open(TOKEN_FILE, "w", encoding="utf-8").write("cb-" + secrets.token_urlsafe(12))
    token = open(TOKEN_FILE, encoding="utf-8").read().strip()

    ver = {"date": datetime.date.today().isoformat(),
           "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
           "contents": n_content, "comments": n_comment, "by_platform": by}
    json.dump(ver, open(VERSION_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    # 知识库同步（文档全量覆盖；媒体按 mtime+大小增量拷贝，省时间）
    # 超过 50MB 的原件（如两份 PDF 手册）不上传：OCR 文字已在 ocr_text.json，按需线下传
    # 视频（.mp4/.mov）一律不同步：本地素材库视频已达 6GB，发布包装不下；
    #   线上播放走七牛 CDN（crawler/upload_qiniu.py 上传 → media_cdn.json 映射 → /cdn/ 同源代理）
    SKIP_SIZE = 50 * 1024 * 1024
    VIDEO_EXT = (".mp4", ".mov", ".m4v", ".webm")
    n_docs = n_media = n_skip = n_video = 0
    if os.path.isdir(KB_SRC):
        os.makedirs(KB_DST, exist_ok=True)
        for root, _dirs, files in os.walk(KB_SRC):
            rel = os.path.relpath(root, KB_SRC)
            target_root = os.path.join(KB_DST, rel) if rel != "." else KB_DST
            os.makedirs(target_root, exist_ok=True)
            for f in files:
                s, t = os.path.join(root, f), os.path.join(target_root, f)
                if os.path.getsize(s) > SKIP_SIZE:
                    if os.path.exists(t):
                        os.remove(t)
                    continue
                # 视频不同步（走 CDN）；顺手清掉历史遗留的副本，避免发布包膨胀
                if f.lower().endswith(VIDEO_EXT):
                    n_video += 1
                    if os.path.exists(t):
                        os.remove(t)
                    continue
                ext = os.path.splitext(f)[1].lower()
                if os.path.exists(s) and os.path.exists(t) and \
                   os.path.getsize(s) == os.path.getsize(t) and \
                   int(os.path.getmtime(s)) == int(os.path.getmtime(t)):
                    n_skip += 1
                    continue
                shutil.copy2(s, t)
                if ext in (".md", ".json"):
                    n_docs += 1
                else:
                    n_media += 1

    print(f"已同步：{n_content} 条内容 / {n_comment} 条评论 → {DST}")
    print(f"知识库：文档 {n_docs} 个 / 媒体 {n_media} 个（未变化跳过 {n_skip} 个，视频跳过 {n_video} 个）→ {KB_DST}")
    print(f"分享密钥：{token}")
    print(f"版本：{ver['date']}")


if __name__ == "__main__":
    main()
