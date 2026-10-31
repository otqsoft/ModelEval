"""SQLite 持久化层：连接管理 + 建表 + 通用 CRUD。"""
import os
import sqlite3
import threading
import datetime

from .config import PROVIDERS, DEFAULT_MODEL_PARAMS

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # backend/
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "modeleval.db")

_local = threading.local()


def now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def conn() -> sqlite3.Connection:
    c = getattr(_local, "conn", None)
    if c is None:
        os.makedirs(DATA_DIR, exist_ok=True)
        c = sqlite3.connect(DB_PATH, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        _local.conn = c
    return c


SCHEMA = """
CREATE TABLE IF NOT EXISTS api_keys(
    provider   TEXT PRIMARY KEY,
    api_key    TEXT NOT NULL,
    base_url   TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS config_templates(
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT UNIQUE,
    config     TEXT NOT NULL,          -- 完整评测配置 JSON 快照
    created_at TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS runs(
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    task            TEXT,
    provider        TEXT,
    model           TEXT,
    config_snapshot TEXT,               -- 本次运行的完整配置（可复现）
    n_cases         INTEGER,
    seed            INTEGER,
    status          TEXT DEFAULT 'running',  -- running/finished/stopped/error
    summary         TEXT,               -- 评估汇总 JSON
    error           TEXT,
    created_at      TEXT,
    finished_at     TEXT
);
CREATE TABLE IF NOT EXISTS cases(
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id           INTEGER,
    idx              INTEGER,
    scramble         TEXT,
    initial_state    TEXT,
    status           TEXT DEFAULT 'running', -- running/success/failed
    stop_reason      TEXT,               -- solved/max_steps/max_calls/max_tokens/api_error/invalid_output/stopped
    moves_used       INTEGER DEFAULT 0,
    calls            INTEGER DEFAULT 0,
    prompt_tokens    INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    reasoning_tokens INTEGER DEFAULT 0,
    cached_tokens    INTEGER DEFAULT 0,
    total_tokens     INTEGER DEFAULT 0,
    latency_ms       INTEGER DEFAULT 0,
    cost             REAL DEFAULT 0,    -- 评测费用（元，按模型价格配置计算）
    score            TEXT,               -- 任务自定义评分 JSON
    created_at       TEXT,
    finished_at      TEXT
);
CREATE TABLE IF NOT EXISTS interactions(
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id     INTEGER,
    case_id    INTEGER,
    seq        INTEGER,                 -- case 内序号
    kind       TEXT,                    -- llm / tool
    request    TEXT,                    -- 完整请求 JSON（不截断）
    response   TEXT,                    -- 完整响应 JSON（含原始报文/思考链，不截断）
    latency_ms INTEGER,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS events(
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id     INTEGER,
    seq        INTEGER,                 -- run 内单调递增（与内存流一致）
    case_id    INTEGER,
    type       TEXT,                    -- run_started/case_started/case_finished/run_finished/run_error
    payload    TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS providers(
    id           TEXT PRIMARY KEY,       -- 稳定标识：glm / deepseek / 自定义 slug
    name         TEXT NOT NULL,          -- 显示名
    protocol     TEXT NOT NULL DEFAULT 'openai',  -- openai | anthropic
    base_url     TEXT,
    api_key      TEXT,
    created_at   TEXT,
    updated_at   TEXT
);
CREATE TABLE IF NOT EXISTS models(
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    provider_id             TEXT NOT NULL,
    name                    TEXT NOT NULL,        -- 模型标识，如 glm-4.6
    cache_hit_input_price   REAL DEFAULT 0,      -- 元 / 百万 Token（命中缓存输入）
    cache_miss_input_price  REAL DEFAULT 0,      -- 元 / 百万 Token（未命中缓存输入）
    output_price            REAL DEFAULT 0,      -- 元 / 百万 Token（输出）
    context_length          INTEGER DEFAULT 1000000,
    temperature             REAL DEFAULT 0.7,
    top_p                   REAL DEFAULT 1.0,
    max_tokens              INTEGER DEFAULT 4096,
    multimodal              INTEGER DEFAULT 0,    -- 0/1
    created_at              TEXT,
    updated_at              TEXT,
    UNIQUE(provider_id, name)
);
CREATE INDEX IF NOT EXISTS idx_interactions_case ON interactions(case_id);
CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id, seq);
CREATE INDEX IF NOT EXISTS idx_cases_run ON cases(run_id);
"""


def init():
    conn().executescript(SCHEMA)
    _ensure_column("cases", "cached_tokens", "INTEGER DEFAULT 0")
    _ensure_column("cases", "cost", "REAL DEFAULT 0")
    _seed_builtin_providers()


def _ensure_column(table: str, col: str, decl: str):
    """轻量迁移：已存在的表补列（CREATE TABLE IF NOT EXISTS 不会更新旧表结构）。"""
    cols = [r["name"] for r in query(f"PRAGMA table_info({table})")]
    if col not in cols:
        execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")


def _seed_builtin_providers():
    """首次启动用内置 PROVIDERS 预填 providers / models 表，之后以数据库为准。"""
    if query_one("SELECT COUNT(*) AS c FROM providers")["c"] > 0:
        return
    ts = now()
    for pid, p in PROVIDERS.items():
        execute(
            "INSERT INTO providers(id, name, protocol, base_url, api_key, created_at, updated_at) "
            "VALUES(?,?, 'openai', ?, NULL, ?, ?)",
            (pid, p["label"], p.get("base_url") or None, ts, ts),
        )
        for m in p.get("models") or []:
            execute(
                "INSERT INTO models(provider_id, name, temperature, top_p, max_tokens, created_at, updated_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (pid, m, DEFAULT_MODEL_PARAMS["temperature"], DEFAULT_MODEL_PARAMS["top_p"],
                 DEFAULT_MODEL_PARAMS["max_tokens"], ts, ts),
            )


def execute(sql: str, params=()) -> int:
    cur = conn().execute(sql, params)
    conn().commit()
    return cur.lastrowid


def query(sql: str, params=()) -> list:
    return [dict(r) for r in conn().execute(sql, params).fetchall()]


def query_one(sql: str, params=()):
    r = conn().execute(sql, params).fetchone()
    return dict(r) if r else None


init()
