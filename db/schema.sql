-- =============================================================================
-- Cosmo 平台 · 内容数据库建表语句
--
-- 这张库是三个产品共用的唯一数据源：
--   工作台（/wb/）读它做六品牌横评
--   逆龄观察（/niling/）读它做内容情报浏览
--   星河（/）通过底座 API 间接读它，决定写什么稿
--
-- 用法：
--   sqlite3 db/cosmobounty.db < base/db/schema.sql
--
-- 只放结构，不放数据。真实库 2400 万字节、8 张业务表，库文件本身不进仓库。
-- 表结构以真实库导出为准（早期手写版本漏了 xhs_note / creator / fetch_skip）。
-- =============================================================================

CREATE TABLE comment (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  content_id INTEGER NOT NULL REFERENCES content(id),
  body TEXT NOT NULL,
  like_count INTEGER DEFAULT 0,
  publish_time TEXT,
  sentiment TEXT,
  text_hash TEXT UNIQUE
);

CREATE TABLE content (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  brand TEXT NOT NULL,
  platform TEXT NOT NULL,            -- baidu | toutiao | xhs | douyin | shipinhao | web
  content_type TEXT NOT NULL,        -- article | note | video | comment | listing
  title TEXT NOT NULL,
  body TEXT,
  author TEXT,
  publish_time TEXT,
  collect_time TEXT NOT NULL,
  url TEXT NOT NULL,
  metrics_json TEXT,                 -- {"likes":0,"comments":0,...}
  sentiment TEXT,                    -- pos | neg | mixed | neutral
  tags TEXT,                         -- 逗号分隔主题标签
  raw_path TEXT,
  text_hash TEXT UNIQUE
, media_json TEXT, analysis_json TEXT, author_id TEXT, verified INTEGER DEFAULT 0, verify_note TEXT, pub_time_src TEXT);

CREATE TABLE crawl_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  platform TEXT, brand TEXT, keyword TEXT,
  fetched_at TEXT, status TEXT, note TEXT
);

CREATE TABLE creator (
  platform TEXT NOT NULL,
  creator_id TEXT NOT NULL,
  nickname TEXT,
  profile_url TEXT,
  note_count INTEGER DEFAULT 0,
  total_likes INTEGER DEFAULT 0,
  total_collects INTEGER DEFAULT 0,
  total_comments INTEGER DEFAULT 0,
  first_publish TEXT,
  last_publish TEXT,
  brands TEXT,
  updated_at TEXT,
  PRIMARY KEY (platform, creator_id)
);

CREATE TABLE daily_metric (
  brand TEXT NOT NULL,
  platform TEXT NOT NULL,
  date TEXT NOT NULL,
  post_count INTEGER DEFAULT 0,
  engagement_total INTEGER DEFAULT 0,
  PRIMARY KEY (brand, platform, date)
);

CREATE TABLE fetch_skip (
        content_id INTEGER PRIMARY KEY,
        attempts   INTEGER NOT NULL DEFAULT 0,
        reason     TEXT,
        updated_at TEXT
    );

CREATE TABLE media_text (id INTEGER PRIMARY KEY AUTOINCREMENT, content_id INTEGER NOT NULL, kind TEXT NOT NULL, source TEXT NOT NULL, text TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(content_id, source));

CREATE TABLE xhs_note (
        note_id TEXT PRIMARY KEY, title TEXT, body TEXT, publish_time TEXT,
        author TEXT, author_id TEXT, images_json TEXT, tags_json TEXT,
        likes INTEGER, collects INTEGER, comments INTEGER, shares INTEGER,
        source_keyword TEXT, brand TEXT, brand_mention INTEGER,
        note_url TEXT, xsec_token TEXT, updated_at TEXT, note_type TEXT, video_url TEXT, evidence TEXT, verified INTEGER DEFAULT 0, verify_note TEXT);

CREATE INDEX idx_content_brand_platform ON content(brand, platform);

CREATE INDEX idx_creator_nickname ON creator(nickname);

CREATE INDEX idx_media_text_content ON media_text(content_id);
