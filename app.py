import os
import json
import sqlite3
from datetime import datetime, timedelta, timezone, date
from typing import Dict, Any, List, Tuple
from urllib.parse import parse_qsl

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

APP_NAME = "AquaFlow"

# Опционально: если задан BOT_TOKEN — можно включить строгую проверку initData (не используется в этой версии).
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

# SQLite путь. Для Railway Volume ставь: /data/water.db
DB_PATH = os.getenv("DB_PATH", "water.db").strip()

db_dir = os.path.dirname(DB_PATH)
if db_dir:
    os.makedirs(db_dir, exist_ok=True)

app = FastAPI(title=APP_NAME)
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")


def db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def db_init() -> None:
    conn = db_connect()
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            telegram_id INTEGER PRIMARY KEY,
            first_name TEXT,
            username TEXT,
            weight_kg INTEGER DEFAULT 0,
            factor_ml INTEGER DEFAULT 33,
            goal_ml INTEGER DEFAULT 0,
            best_streak INTEGER DEFAULT 0,
            current_streak INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER NOT NULL,
            date TEXT NOT NULL,     -- YYYY-MM-DD (локальная дата клиента)
            ts TEXT NOT NULL,       -- ISO timestamp (клиент)
            ml INTEGER NOT NULL,
            FOREIGN KEY (telegram_id) REFERENCES users(telegram_id) ON DELETE CASCADE
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS daily_stats (
            telegram_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            total_ml INTEGER NOT NULL DEFAULT 0,
            goal_ml INTEGER NOT NULL DEFAULT 0,
            met_goal INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (telegram_id, date),
            FOREIGN KEY (telegram_id) REFERENCES users(telegram_id) ON DELETE CASCADE
        )
        """
    )

    conn.commit()
    conn.close()


@app.on_event("startup")
def _startup():
    db_init()


# ---------------------------
# Telegram WebApp initData (упрощённо, без проверки подписи)
# ---------------------------
def parse_init_data(init_data: str) -> Dict[str, Any]:
    if not init_data:
        raise HTTPException(status_code=401, detail="Missing initData")
    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    user_json = pairs.get("user", "{}")
    try:
        user_obj = json.loads(user_json)
    except Exception:
        user_obj = {}
    return {"pairs": pairs, "user": user_obj}


def get_user_identity(init_data: str) -> Tuple[int, str, str]:
    data = parse_init_data(init_data)
    user = data.get("user") or {}
    tg_id = int(user.get("id", 0))
    if not tg_id:
        raise HTTPException(status_code=401, detail="No Telegram user id in initData")
    first_name = (user.get("first_name") or "").strip()
    username = (user.get("username") or "").strip()
    return tg_id, first_name, username


# ---------------------------
# Logic
# ---------------------------
def calc_goal(weight_kg: int, factor_ml: int) -> int:
    if weight_kg <= 0:
        return 0
    factor_ml = max(30, min(35, int(factor_ml)))
    return int(weight_kg * factor_ml)


def ensure_user(conn: sqlite3.Connection, tg_id: int, first_name: str, username: str) -> sqlite3.Row:
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE telegram_id=?", (tg_id,))
    row = cur.fetchone()
    if row:
        cur.execute("UPDATE users SET first_name=?, username=? WHERE telegram_id=?", (first_name, username, tg_id))
        conn.commit()
        cur.execute("SELECT * FROM users WHERE telegram_id=?", (tg_id,))
        return cur.fetchone()

    cur.execute(
        """
        INSERT INTO users (telegram_id, first_name, username, weight_kg, factor_ml, goal_ml, best_streak, current_streak)
        VALUES (?, ?, ?, 0, 33, 0, 0, 0)
        """,
        (tg_id, first_name, username),
    )
    conn.commit()
    cur.execute("SELECT * FROM users WHERE telegram_id=?", (tg_id,))
    return cur.fetchone()


def upsert_daily_stats(conn: sqlite3.Connection, tg_id: int, day: str, goal_ml: int) -> sqlite3.Row:
    cur = conn.cursor()
    cur.execute("SELECT COALESCE(SUM(ml),0) AS total FROM entries WHERE telegram_id=? AND date=?", (tg_id, day))
    total = int(cur.fetchone()["total"])
    met_goal = 1 if (goal_ml > 0 and total >= goal_ml) else 0

    cur.execute(
        """
        INSERT INTO daily_stats (telegram_id, date, total_ml, goal_ml, met_goal)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(telegram_id, date) DO UPDATE SET
          total_ml=excluded.total_ml,
          goal_ml=excluded.goal_ml,
          met_goal=excluded.met_goal
        """,
        (tg_id, day, total, goal_ml, met_goal),
    )
    conn.commit()

    cur.execute("SELECT * FROM daily_stats WHERE telegram_id=? AND date=?", (tg_id, day))
    return cur.fetchone()


def recompute_streaks(conn: sqlite3.Connection, tg_id: int, today_str: str) -> Tuple[int, int]:
    cur = conn.cursor()
    cur.execute("SELECT date, met_goal FROM daily_stats WHERE telegram_id=? ORDER BY date ASC", (tg_id,))
    rows = cur.fetchall()

    best = 0
    run = 0
    for r in rows:
        if int(r["met_goal"]) == 1:
            run += 1
            best = max(best, run)
        else:
            run = 0

    # current streak ending at today
    cur.execute("SELECT date, met_goal FROM daily_stats WHERE telegram_id=? ORDER BY date DESC", (tg_id,))
    rows_desc = cur.fetchall()
    current = 0
    expected = date.fromisoformat(today_str)
    for r in rows_desc:
        d = date.fromisoformat(r["date"])
        if d != expected:
            break
        if int(r["met_goal"]) == 1:
            current += 1
            expected = expected - timedelta(days=1)
        else:
            break

    cur.execute(
        "UPDATE users SET current_streak=?, best_streak=MAX(best_streak, ?) WHERE telegram_id=?",
        (current, best, tg_id),
    )
    conn.commit()

    cur.execute("SELECT current_streak, best_streak FROM users WHERE telegram_id=?", (tg_id,))
    u = cur.fetchone()
    return int(u["current_streak"]), int(u["best_streak"])


def get_today_entries(conn: sqlite3.Connection, tg_id: int, day: str) -> List[Dict[str, Any]]:
    cur = conn.cursor()
    cur.execute("SELECT * FROM entries WHERE telegram_id=? AND date=? ORDER BY ts DESC, id DESC", (tg_id, day))
    out = []
    for r in cur.fetchall():
        out.append({"id": int(r["id"]), "ts": r["ts"], "ml": int(r["ml"])})
    return out


def get_last_n_days(conn: sqlite3.Connection, tg_id: int, end_day: str, n: int, goal_ml: int) -> List[Dict[str, Any]]:
    end = date.fromisoformat(end_day)
    days = [(end - timedelta(days=i)).isoformat() for i in range(n - 1, -1, -1)]
    cur = conn.cursor()
    cur.execute(
        "SELECT date, total_ml, goal_ml, met_goal FROM daily_stats WHERE telegram_id=? AND date>=? AND date<=?",
        (tg_id, days[0], days[-1]),
    )
    stats_map = {r["date"]: r for r in cur.fetchall()}

    out = []
    for d in days:
        r = stats_map.get(d)
        total = int(r["total_ml"]) if r else 0
        g = int(r["goal_ml"]) if r else goal_ml
        met = int(r["met_goal"]) if r else (1 if (g > 0 and total >= g) else 0)
        out.append({"date": d, "total_ml": total, "goal_ml": g, "met_goal": met})
    return out


def calendar_grid(conn: sqlite3.Connection, tg_id: int, month_ym: str, goal_ml: int) -> Dict[str, Any]:
    y, m = map(int, month_ym.split("-"))
    first = date(y, m, 1)
    start = first - timedelta(days=first.weekday())  # Monday=0
    days = [start + timedelta(days=i) for i in range(42)]

    cur = conn.cursor()
    cur.execute(
        "SELECT date, total_ml, goal_ml FROM daily_stats WHERE telegram_id=? AND date>=? AND date<=?",
        (tg_id, days[0].isoformat(), days[-1].isoformat()),
    )
    stats_map = {r["date"]: r for r in cur.fetchall()}

    out_days = []
    for d in days:
        iso = d.isoformat()
        r = stats_map.get(iso)
        total = int(r["total_ml"]) if r else 0
        g = int(r["goal_ml"]) if r else goal_ml
        ratio = (total / g) if g > 0 else 0.0
        out_days.append(
            {"date": iso, "day": d.day, "in_month": (d.month == m), "total_ml": total, "goal_ml": g, "ratio": min(2.0, max(0.0, ratio))}
        )
    return {"month": month_ym, "days": out_days}


# ---------------------------
# Routes
# ---------------------------
@app.get("/healthz")
def healthz():
    return {"ok": True, "app": APP_NAME}


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request, "app_name": APP_NAME})


@app.post("/api/state")
async def api_state(payload: Dict[str, Any]):
    init_data = payload.get("initData", "")
    client_date = payload.get("client_date") or datetime.now(timezone.utc).date().isoformat()
    month = payload.get("month") or client_date[:7]

    tg_id, first_name, username = get_user_identity(init_data)

    conn = db_connect()
    user = ensure_user(conn, tg_id, first_name, username)

    weight = int(user["weight_kg"] or 0)
    factor = int(user["factor_ml"] or 33)
    goal_ml = int(user["goal_ml"] or 0)
    if goal_ml <= 0 and weight > 0:
        goal_ml = calc_goal(weight, factor)
        conn.execute("UPDATE users SET goal_ml=? WHERE telegram_id=?", (goal_ml, tg_id))
        conn.commit()

    today_stats = upsert_daily_stats(conn, tg_id, client_date, goal_ml)
    cur_streak, best_streak = recompute_streaks(conn, tg_id, client_date)

    entries = get_today_entries(conn, tg_id, client_date)
    total_today = int(today_stats["total_ml"])
    pct_today = int(round((total_today / goal_ml) * 100)) if goal_ml > 0 else 0
    pct_today = max(0, min(100, pct_today))

    last7 = get_last_n_days(conn, tg_id, client_date, 7, goal_ml)
    avg7 = int(round(sum(d["total_ml"] for d in last7) / 7))

    achievements = [
        {"id": "streak7", "title": "7 дней подряд", "threshold": 7, "icon": "🏅", "unlocked": best_streak >= 7},
        {"id": "streak14", "title": "14 дней подряд", "threshold": 14, "icon": "🥈", "unlocked": best_streak >= 14},
        {"id": "streak30", "title": "30 дней подряд", "threshold": 30, "icon": "🥇", "unlocked": best_streak >= 30},
    ]

    cal_data = calendar_grid(conn, tg_id, month, goal_ml)
    conn.close()

    return JSONResponse(
        {
            "user": {"telegram_id": tg_id, "first_name": first_name, "username": username},
            "profile": {"weight_kg": weight, "factor_ml": factor, "goal_ml": goal_ml},
            "today": {"date": client_date, "total_ml": total_today, "goal_ml": goal_ml, "pct": pct_today, "entries": entries},
            "stats": {"last7": last7, "avg7": avg7, "current_streak": cur_streak, "best_streak": best_streak},
            "calendar": cal_data,
            "achievements": achievements,
        }
    )


@app.post("/api/add")
async def api_add(payload: Dict[str, Any]):
    init_data = payload.get("initData", "")
    ml = int(payload.get("ml", 0) or 0)
    client_date = payload.get("client_date") or datetime.now(timezone.utc).date().isoformat()
    client_ts = payload.get("client_ts") or datetime.now(timezone.utc).isoformat()

    if ml <= 0 or ml > 5000:
        raise HTTPException(status_code=400, detail="Invalid ml")

    tg_id, first_name, username = get_user_identity(init_data)

    conn = db_connect()
    user = ensure_user(conn, tg_id, first_name, username)

    weight = int(user["weight_kg"] or 0)
    factor = int(user["factor_ml"] or 33)
    goal_ml = int(user["goal_ml"] or 0)
    if goal_ml <= 0 and weight > 0:
        goal_ml = calc_goal(weight, factor)
        conn.execute("UPDATE users SET goal_ml=? WHERE telegram_id=?", (goal_ml, tg_id))
        conn.commit()

    before = upsert_daily_stats(conn, tg_id, client_date, goal_ml)
    before_met = int(before["met_goal"])

    cur = conn.cursor()
    cur.execute("INSERT INTO entries (telegram_id, date, ts, ml) VALUES (?, ?, ?, ?)", (tg_id, client_date, client_ts, ml))
    entry_id = int(cur.lastrowid)
    conn.commit()

    after = upsert_daily_stats(conn, tg_id, client_date, goal_ml)
    after_met = int(after["met_goal"])

    cur_streak, best_streak = recompute_streaks(conn, tg_id, client_date)
    conn.close()

    goal_completed_today = (after_met == 1 and before_met == 0)
    return JSONResponse(
        {"ok": True, "entry_id": entry_id, "today_total": int(after["total_ml"]), "today_goal": goal_ml, "today_met": int(after["met_goal"]),
         "current_streak": cur_streak, "best_streak": best_streak, "goal_completed_today": goal_completed_today}
    )


@app.post("/api/profile")
async def api_profile(payload: Dict[str, Any]):
    init_data = payload.get("initData", "")
    tg_id, first_name, username = get_user_identity(init_data)

    weight_kg = payload.get("weight_kg")
    factor_ml = payload.get("factor_ml")
    goal_ml = payload.get("goal_ml")

    conn = db_connect()
    user = ensure_user(conn, tg_id, first_name, username)

    new_weight = int(user["weight_kg"] or 0)
    new_factor = int(user["factor_ml"] or 33)
    new_goal = int(user["goal_ml"] or 0)

    if weight_kg is not None:
        new_weight = max(0, min(300, int(weight_kg)))
    if factor_ml is not None:
        new_factor = max(30, min(35, int(factor_ml)))
    if goal_ml is not None:
        new_goal = max(0, min(10000, int(goal_ml)))

    if goal_ml is None and (weight_kg is not None or factor_ml is not None):
        computed = calc_goal(new_weight, new_factor)
        if computed > 0:
            new_goal = computed

    conn.execute("UPDATE users SET weight_kg=?, factor_ml=?, goal_ml=? WHERE telegram_id=?", (new_weight, new_factor, new_goal, tg_id))
    conn.commit()

    today = payload.get("client_date") or datetime.now(timezone.utc).date().isoformat()
    upsert_daily_stats(conn, tg_id, today, new_goal)

    conn.close()
    return JSONResponse({"ok": True, "weight_kg": new_weight, "factor_ml": new_factor, "goal_ml": new_goal})
