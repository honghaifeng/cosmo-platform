#!/bin/bash
# CosmoBounty 每日更新流水线
# 用法: bash scripts/daily_update.sh [--collect]
# 默认只做「补抓正文 + 分析 + 导出」；加 --collect 才跑小红书/抖音增量采集
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
TS=$(date +%Y-%m-%d)
LOG="$ROOT/logs/daily_$TS.log"
mkdir -p "$ROOT/logs"
PY="/Users/v/.workbuddy/binaries/python/versions/3.13.12/bin/python3"
MPY="$ROOT/tools/MediaCrawler/.venv/bin/python"
UV="$HOME/.local/bin/uv"
# 定时任务的 PATH 里没有 ~/.local/bin，这里显式兜底：优先 uv，退回 venv python
if [[ -x "$UV" ]]; then MCRUN=("$UV" run python); else MCRUN=("$MPY"); fi

echo "=== $TS $(date +%H:%M) 开始每日更新 ===" | tee -a "$LOG"

FAILS=()          # 需要人工介入的环节
NEWS=()           # 各平台本次新增条数

# 单平台采集：独立日志 + 失败识别（登录过期/CAPTCHA/无产出 都算失败）
run_platform() {
  local plat="$1" tag="$2"
  local plog="$ROOT/logs/collect_${tag}_${TS}.log"
  local prof="$ROOT/tools/MediaCrawler/browser_data/${tag}_user_data_dir"
  # 上一次崩溃/人工登录窗口留下的 Chrome 会占着 profile，新进程只会得到
  # 「正在现有的浏览器会话中打开」然后退出，这里先清干净
  if pgrep -f "user-data-dir=$prof" >/dev/null 2>&1; then
    echo "  · 清理占用 profile 的残留浏览器进程" | tee -a "$LOG"
    pkill -f "user-data-dir=$prof" 2>/dev/null
    sleep 2
    pkill -9 -f "user-data-dir=$prof" 2>/dev/null
    sleep 1
  fi
  rm -f "$prof"/Singleton* 2>/dev/null
  echo "  · 采集 $tag …" | tee -a "$LOG"
  ( cd "$ROOT/tools/MediaCrawler" && "${MCRUN[@]}" main.py --platform "$plat" --lt qrcode ) \
      >"$plog" 2>&1
  local rc=$?
  cat "$plog" >>"$LOG"
  if grep -qE "登录已过期|登录过期|captcha|CAPTCHA|请先登录|login required" "$plog"; then
    FAILS+=("$tag 登录态失效（需人工扫码）")
    echo "  ⚠️ $tag 登录态失效，本次采集中断" | tee -a "$LOG"
    return 0
  fi
  # 平台级封锁：账号无接口权限 / IP 风险。此时继续跑只会加重风控，直接中断并如实上报原因，
  # 不要伪装成「抓到 0 条（风控静默拦截）」——两种情况的处置动作完全不同。
  if grep -qE "没有权限访问|IP存在风险|IP 存在风险|IPBlockError" "$plog"; then
    FAILS+=("$tag 平台封锁：账号无接口权限 / IP 存在风险（需换网络或换账号，非登录过期）")
    echo "  🚫 $tag 平台封锁（账号权限/IP 风险），本次采集中断，勿重试" | tee -a "$LOG"
    return 0
  fi
  if [[ $rc -ne 0 ]]; then
    FAILS+=("$tag 采集进程异常退出 rc=$rc")
    echo "  ⚠️ $tag 进程异常退出 rc=$rc" | tee -a "$LOG"
    return 0
  fi
  local n
  # 日志真实格式： [store.xhs.update_xhs_note] / [store.douyin.update_douyin_aweme]
  # （旧正则 store.dy 已在 MediaCrawler 重构后失效，恒记 0 条造成假告警）
  n=$(grep -cE "\[store\.(xhs\.update_xhs_note|douyin\.update_douyin_aweme)\]" "$plog" 2>/dev/null || true)
  echo "  · $tag 本次抓取记录 $n 条" | tee -a "$LOG"
  if [[ "${n:-0}" -eq 0 ]]; then
    FAILS+=("$tag 抓到 0 条（关键词无新增或风控静默拦截）")
  fi
}

if [[ "${1:-}" == "--collect" ]]; then
  # 支持只补跑单平台：bash scripts/daily_update.sh --collect xhs
  if [[ -n "${2:-}" ]]; then
    PLATS=($2)
  else
    PLATS=(xhs douyin)
  fi
  echo "[1/4] 采集 ${PLATS[*]}（使用缓存登录态，失效会提示扫码）" | tee -a "$LOG"
  for p in "${PLATS[@]}"; do
    crawl="$p"; [[ "$p" == "douyin" ]] && crawl="dy"
    run_platform "$crawl" "$p"
  done

  echo "[2/4] 增量入库" | tee -a "$LOG"
  for p in "${PLATS[@]}"; do
    f=$(ls -t "$ROOT/tools/MediaCrawler/data/$p/jsonl/"search_contents_*.jsonl 2>/dev/null | head -1)
    c=$(ls -t "$ROOT/tools/MediaCrawler/data/$p/jsonl/"search_comments_*.jsonl 2>/dev/null | head -1)
    if [[ -n "$f" && -n "$c" ]]; then
      out=$("$PY" "$ROOT/crawler/mediacrawler_ingest.py" "$p" "$f" "$c" 2>&1)
      echo "$out" | tee -a "$LOG"
      NEWS+=("$(echo "$out" | grep -oE "新增内容 [0-9]+" | head -1 | grep -oE "[0-9]+" || echo 0)")
    elif [[ -n "$f" ]]; then
      # 当天没抓到评论文件也要入库内容，不能整段跳过
      out=$("$PY" "$ROOT/crawler/mediacrawler_ingest.py" "$p" "$f" "$c" 2>&1)
      echo "$out" | tee -a "$LOG"
      echo "  · $p 无当日评论文件，仅入库内容" | tee -a "$LOG"
      NEWS+=("$(echo "$out" | grep -oE "新增内容 [0-9]+" | head -1 | grep -oE "[0-9]+" || echo 0)")
    else
      echo "  ⚠️ $p 无可用 jsonl，跳过入库" | tee -a "$LOG"
      FAILS+=("$p 无采集文件")
      NEWS+=(0)
    fi
  done
else
  echo "[1/4] 跳过采集（未加 --collect）" | tee -a "$LOG"
  echo "[2/4] 跳过入库" | tee -a "$LOG"
fi

echo "[3/4] 补抓缺正文条目 + 重算营销分析" | tee -a "$LOG"
ft_out=$("$PY" "$ROOT/crawler/fetch_fulltext.py" 2>&1)
echo "$ft_out" | tee -a "$LOG"
if echo "$ft_out" | grep -qE "完成：成功 0，失败 [1-9]"; then
  FAILS+=("补正文全军覆没（详见日志 [3/4] 段）")
fi
"$MPY" "$ROOT/crawler/enrich_content.py" >>"$LOG" 2>&1

echo "[3.5/4] 媒体文字提取（OCR 图片文字 / ASR 视频口播，结果存 media_text 表）" | tee -a "$LOG"
MPY2="/Users/v/.workbuddy/binaries/python/envs/default/bin/python"
if [[ "${SKIP_MEDIA:-0}" == "1" ]]; then
  echo "  跳过（SKIP_MEDIA=1，仅用于快速验证链路）" | tee -a "$LOG"
elif [[ -x "$MPY2" ]]; then
  # 直链带签名会过期，只处理近 2 天入库的，历史库存补跑无意义
  ocr_out=$("$MPY2" "$ROOT/crawler/extract_media_text.py" ocr --all --fresh-days 2 2>&1)
  ocr_rc=$?
  echo "$ocr_out" | tee -a "$LOG"
  [[ $ocr_rc -eq 3 ]] && FAILS+=("OCR 全部失败：图片直链已过期")
  "$MPY2" "$ROOT/crawler/extract_media_text.py" asr --all --top 20 --fresh-days 2 >>"$LOG" 2>&1
else
  echo "  跳过：未找到 managed venv ($MPY2)" | tee -a "$LOG"
  FAILS+=("未找到 OCR/ASR 运行环境")
fi

echo "[3.8/5] 低频补抓（场景词 / 创作者主页 / 百度）" | tee -a "$LOG"
# 白天批量采集容易被风控，这三块放凌晨单独跑；失败不阻塞主流程
"$PY" "$ROOT/crawler/daily_backfill.py" --part all 2>&1 | tee -a "$LOG"

echo "[4/5] 生成数据交付包" | tee -a "$LOG"
"$MPY" "$ROOT/crawler/export_package.py" 2>&1 | tee -a "$LOG"

echo "[5/5] 同步到在线数据服务（service/data，供朋友 AI 查询）" | tee -a "$LOG"
"$PY" "$ROOT/scripts/sync_service.py" 2>&1 | tee -a "$LOG"

NEWS_JOIN=$(IFS=,; echo "${NEWS[*]:-}")
FAILS_JOIN=$(IFS='|'; echo "${FAILS[*]:-}")
COLLECTED="${1:-}"
NEWS_JOIN="$NEWS_JOIN" FAILS_JOIN="$FAILS_JOIN" COLLECTED="$COLLECTED" "$PY" - <<'EOF' | tee -a "$LOG"
import sqlite3, datetime, json, os
root = '/Users/v/Desktop/2026/秋峰/Demo/CosmoBounty'
con = sqlite3.connect(f'{root}/db/cosmobounty.db')
total_c = con.execute("SELECT COUNT(*) FROM content").fetchone()[0]
total_m = con.execute("SELECT COUNT(*) FROM comment").fetchone()[0]
plat = list(con.execute("SELECT platform, COUNT(*) FROM content GROUP BY 1"))
new = [int(x) for x in (os.getenv('NEWS_JOIN') or '').split(',') if x.strip().isdigit()]
fails = [x for x in (os.getenv('FAILS_JOIN') or '').split('|') if x.strip()]
print(f"当前库存：内容 {total_c} 条，评论 {total_m} 条")
print("平台分布：", *plat)
path = f"{root}/logs/summary_{datetime.date.today().isoformat()}.json"
# 非采集轮（补跑/修复）不能把当天已采到的新增数冲成 null，沿用上一版
prev = {}
if os.getenv('COLLECTED') != '--collect' and os.path.exists(path):
    try:
        prev = json.load(open(path))
    except Exception:
        prev = {}
summary = {
    "date": datetime.date.today().isoformat(),
    "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    "new_xhs": new[0] if len(new) > 0 else prev.get("new_xhs"),
    "new_douyin": new[1] if len(new) > 1 else prev.get("new_douyin"),
    "total_content": total_c,
    "total_comment": total_m,
    "by_platform": dict(plat),
    "failures": fails,
    "status": "PARTIAL" if fails else "OK",
}
# 库存骤降自检：内容表被整体重写/误删时，条数会明显低于历史，绝不能当正常完成
prev_total = prev.get("total_content")
if isinstance(prev_total, int) and prev_total > 0 and total_c < prev_total * 0.95:
    fails.append(f"库存骤降：内容 {total_c} 条，低于上次 {prev_total} 条的 95%，疑似表被重写或误删")
    summary["failures"] = fails
    summary["status"] = "PARTIAL"
    summary["total_drop_warning"] = {"prev_total": prev_total, "now_total": total_c}
json.dump(summary, open(path, "w"), ensure_ascii=False, indent=2)
print("摘要已写入 logs/" + os.path.basename(path))
EOF
echo "=== $(date +%H:%M) 完成 ===" | tee -a "$LOG"
if [[ ${#FAILS[@]} -gt 0 ]]; then
  echo "⚠️ 本次有 ${#FAILS[@]} 项需要处理：" | tee -a "$LOG"
  printf '  - %s\n' "${FAILS[@]}" | tee -a "$LOG"
  exit 1
fi
exit 0
