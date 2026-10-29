/* BrandDesk v4 — 品牌为根的对象树：品牌 → 产品 → 渠道/内容 → 数据 → 策略 → 成本效果 */

const BRANDS = [
  { key: "MitoQ",       name: "MitoQ",        cn: "MitoQ",     color: "var(--b-MitoQ)",       note: "新西兰·线粒体抗衰" },
  { key: "LAIFE",       name: "LAIFE",        cn: "乐梵",       color: "var(--b-LAIFE)",       note: "美国·护肝/抗衰" },
  { key: "GRANVER",     name: "GRANVER",      cn: "GRANVER",   color: "var(--b-GRANVER)",     note: "美国·器官养护矩阵" },
  { key: "塞乐瑞",       name: "塞乐瑞",        cn: "塞乐瑞",     color: "var(--b-塞乐瑞)",       note: "日本·干细胞概念" },
  { key: "W+端粒塔",     name: "W+端粒塔",      cn: "W+端粒塔",   color: "var(--b-W)",           note: "NMN 品类" },
  { key: "COSMOBOUNTY", name: "科诗博缇",      cn: "科诗博缇",   color: "var(--b-COSMOBOUNTY)", note: "自有品牌·分时抗衰" },
];

/* 官号 vs 达人 效率：不再写死在代码里，直接取 data.js 里现算的 eff
   （写死过一次，结果数据库更新后页面三处数字打架——这条教训记在这） */
const EFF = (function () {
  const o = {};
  const bs = (typeof DATA !== 'undefined' && DATA.brands) || {};
  Object.keys(bs).forEach(k => { o[k] = bs[k].eff || { off_n: 0, off_avg: 0, off_viral: 0, kol_n: 0, kol_avg: 0, kol_viral: 0, verdict: "—", vkey: "none" }; });
  return o;
})();

/* 平台属性：社媒看互动，媒体看收录 —— 混算会把「百度 0 赞」误读成没效果 */
const PLAT_META = {
  xhs:       { name: "小红书",  kind: "社媒", metric: "互动" },
  douyin:    { name: "抖音",    kind: "社媒", metric: "互动" },
  shipinhao: { name: "视频号",  kind: "社媒", metric: "互动" },
  baidu:     { name: "百度",    kind: "媒体", metric: "收录" },
  toutiao:   { name: "今日头条", kind: "媒体", metric: "收录" },
  web:       { name: "新闻源",  kind: "媒体", metric: "收录" },
};

const NAV_SECTIONS = [
  { title: "总盘", items: [
    { id: "index",   label: "六品牌总盘", href: "index.html" },
    { id: "compare", label: "跨品牌横评", href: "compare.html" },
  ]},
  { title: "品牌", brandList: true },
  { title: "作业", detail: "对品牌执行的动作", items: [
    { id: "collect", label: "采集中心", href: "collect.html", carry: true },
    { id: "create",  label: "创作中心", href: "create.html",  carry: true },
    { id: "review",  label: "审核队列", href: "review.html",  carry: true },
    { id: "publish", label: "发布管理", href: "publish.html", carry: true },
  ]},
  { title: "参照系", items: [
    { id: "category", label: "品类大盘", href: "brand.html?brand=__category" },
  ]},
  { title: "形态", items: [
    { id: "mobile", label: "移动端", href: "mobile.html" },
    { id: "about",  label: "口径与边界", href: "about.html" },
  ]},
];

/* 品牌 → 数据文件 slug（list-<slug>.js / detail-<slug>.js） */
const SLUG = {
  "MitoQ": "mitoq", "LAIFE": "laife", "GRANVER": "granver",
  "塞乐瑞": "sailerui", "W+端粒塔": "duanlita", "COSMOBOUNTY": "cosmobounty",
  "__category": "category",
};
function slugOf(brand) { return SLUG[brand] || "laife"; }
function brandCn(key) {
  const b = BRANDS.find(x => x.key === key);
  return b ? b.cn : key;
}
function brandColor(key) {
  const b = BRANDS.find(x => x.key === key);
  return b ? b.color : "var(--ink-4)";
}
/* 内容明细注册表：list-*.js / detail-*.js 加载后自注册 */
var WB_REG = { list: {}, detail: {} };
function brandList(brand) { return WB_REG.list[slugOf(brand)] || null; }
function brandDetail(brand) { return WB_REG.detail[slugOf(brand)] || null; }
function contentHref(brand, id) {
  return `content.html?brand=${encodeURIComponent(brand)}&id=${encodeURIComponent(id)}`;
}
/* 归档媒体路径：优先用七牛云 CDN（media-cdn.js 映射表），未命中才回落本地 media/
   文件名里的冒号需逐段编码；兼容带 knowledge/media/ 前缀或已编码的入参 */
function mediaPath(rel) {
  const raw = String(rel || "");
  if (!raw) return "";
  if (raw.startsWith("data:")) return raw; // 内嵌图片不走路径拼接
  if (/^https?:\/\//i.test(raw)) return raw; // 已是绝对地址
  const M = window.MEDIA_CDN || {};
  const variants = [raw];
  const stripped = raw.replace(/^knowledge\/media\//, "").replace(/^\.?\/?media\//, "").replace(/^\/+/, "");
  if (stripped !== raw) variants.push(stripped);
  try { const dec = decodeURIComponent(stripped); if (dec !== stripped) variants.push(dec); } catch (e) { }
  for (let i = 0; i < variants.length; i++) {
    const hit = M[variants[i]];
    if (hit) return hit;
  }
  const base = stripped || raw;
  return "media/" + base.split("/").map(encodeURIComponent).join("/");
}

function fmt(n) {
  if (n === null || n === undefined || isNaN(n)) return "—";
  if (n >= 1000000) return (n / 10000).toFixed(0) + "万";
  if (n >= 10000) return (n / 10000).toFixed(1) + "万";
  return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}
function pct(a, b) { return b ? Math.round(a / b * 100) + "%" : "—"; }
function esc(s) { return String(s == null ? "" : s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }

function renderRail(activeId, activeBrand) {
  const el = document.querySelector(".rail");
  if (!el) return;
  let h = `<div class="rail-hd">
      <div class="logo">逆龄工作台</div>
      <div class="sub">BRANDDESK · v4</div>
    </div>`;
  NAV_SECTIONS.forEach(sec => {
    h += `<div class="rail-sec">${sec.title}</div>`;
    if (sec.brandList) {
      BRANDS.forEach(b => {
        const d = (typeof DATA !== 'undefined' && DATA.brands[b.key]) || {};
        const on = activeBrand === b.key ? " on" : "";
        h += `<a class="nav${on}" href="brand.html?brand=${encodeURIComponent(b.key)}">
            <span style="display:flex;align-items:center;gap:8px;min-width:0">
              <i class="dot" style="background:${b.color}"></i>
              <span style="overflow:hidden;text-overflow:ellipsis">${esc(b.cn)}</span>
            </span>
            <span class="cnt">${d.total ? fmt(d.total) : ""}</span>
          </a>`;
      });
    } else {
      sec.items.forEach(it => {
        const on = activeId === it.id ? " on" : "";
        let href = it.href;
        /* 作业类入口携带当前品牌上下文：从「LAIFE 的产品页」点「创作中心」→ 直接进 LAIFE 的创作视图 */
        if (it.carry && activeBrand) href += `?brand=${encodeURIComponent(activeBrand)}`;
        h += `<a class="nav${on}" href="${href}">${esc(it.label)}</a>`;
      });
    }
  });
  const bs = (typeof DATA !== 'undefined' && DATA.brands) || {};
  /* 口径与总盘页一致：六品牌 + 品类大盘 */
  const cat = (typeof DATA !== "undefined" && DATA.category) || { total: 0 };
  const sumT = Object.values(bs).reduce((s, b) => s + (b.total || 0), 0) + (cat.total || 0);
  const sumV = Object.values(bs).reduce((s, b) => s + (b.verified || 0), 0) + (cat.total || 0);
  h += `<div class="rail-ft">
      数据版本 ${(typeof DATA !== "undefined" && DATA.generated) || "—"}<br>
      内容 ${fmt(sumT)} · 已验讫 ${fmt(sumV)}<br>
      <span style="color:var(--ink-3)">仅展示 verified=1</span>
    </div>`;
  el.innerHTML = h;
}

function topbar(crumbHtml, stampText) {
  const t = document.querySelector(".topbar");
  if (!t) return;
  t.innerHTML = `<div class="crumb">${crumbHtml}</div>
    <div class="spacer"></div>
    ${stampText ? `<span class="stamp">${stampText}</span>` : ""}`;
}

function barRow(label, value, max, color, suffix, href) {
  const w = max ? Math.max(1.5, value / max * 100) : 0;
  const inner = `
    <div class="lab" title="${esc(label)}">${esc(label)}</div>
    <div class="bar-track"><div class="bar-fill" style="width:${w}%;background:${color}"></div></div>
    <div class="val">${fmt(value)}${suffix || ""}</div>`;
  /* 有 href 时可点击 → 带着这一档的条件下钻到内容库 */
  return href
    ? `<a class="bar-row click" href="${href}" title="看这一档的具体内容">${inner}</a>`
    : `<div class="bar-row">${inner}</div>`;
}
