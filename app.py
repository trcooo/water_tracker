import os
import hmac
import hashlib
import json
import sqlite3
import calendar as cal
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, date
from io import BytesIO, StringIO
from typing import Optional, Dict, Any, List, Tuple
from urllib.parse import parse_qsl

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# ---------------------------
# Config
# ---------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN", "")  # recommended: set in Railway Variables
DB_PATH = os.getenv("DB_PATH", "water.db")
APP_NAME = "AquaFlow"

# Ensure DB directory exists (important for Railway Volume mount like /data)
db_dir = os.path.dirname(DB_PATH)
if db_dir:
    os.makedirs(db_dir, exist_ok=True)

# ---------------------------
# FastAPI setup
# ---------------------------
app = FastAPI(title=APP_NAME)
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")


# ---------------------------
# DB helpers
# ---------------------------
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
            date TEXT NOT NULL,              -- YYYY-MM-DD (user local)
            ts TEXT NOT NULL,                -- ISO timestamp (client)
            drink_type TEXT DEFAULT 'water', -- water/tea/coffee
            raw_ml INTEGER NOT NULL,
            effective_ml INTEGER NOT NULL,
            FOREIGN KEY (telegram_id) REFERENCES users(telegram_id) ON DELETE CASCADE
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS daily_stats (
            telegram_id INTEGER NOT NULL,
            date TEXT NOT NULL,              -- YYYY-MM-DD
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
# Telegram initData verification (recommended)
# ---------------------------
def _tg_secret_key(bot_token: str) -> bytes:
    return hashlib.sha256(bot_token.encode("utf-8")).digest()


def verify_init_data(init_data: str, bot_token: str) -> Dict[str, Any]:
    """
    Verifies Telegram WebApp initData and returns parsed fields.
    If BOT_TOKEN is missing, we still parse but skip verification (dev-friendly).
    """
    if not init_data:
        raise HTTPException(status_code=401, detail="Missing initData")

    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    provided_hash = pairs.get("hash", "")
    if not provided_hash:
        raise HTTPException(status_code=401, detail="Missing hash in initData")

    # Build data-check-string
    data_items = []
    for k, v in pairs.items():
        if k == "hash":
            continue
        data_items.append(f"{k}={v}")
    data_items.sort()
    data_check_string = "\n".join(data_items)

    if bot_token:
        key = _tg_secret_key(bot_token)
        calc_hash = hmac.new(key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calc_hash, provided_hash):
            raise HTTPException(status_code=401, detail="Invalid initData hash")

        # Optional: auth_date freshness (48h)
        try:
            auth_date = int(pairs.get("auth_date", "0"))
            now = int(datetime.now(timezone.utc).timestamp())
            if auth_date and now - auth_date > 48 * 3600:
                raise HTTPException(status_code=401, detail="initData expired")
        except ValueError:
            pass

    # Parse user JSON
    user_json = pairs.get("user", "{}")
    try:
        user_obj = json.loads(user_json)
    except Exception:
        user_obj = {}

    return {"pairs": pairs, "user": user_obj}


def get_user_identity(init_data: str) -> Tuple[int, str, str]:
    data = verify_init_data(init_data, BOT_TOKEN)
    user = data.get("user") or {}
    tg_id = int(user.get("id", 0))
    if not tg_id:
        raise HTTPException(status_code=401, detail="No Telegram user id in initData")
    first_name = user.get("first_name", "") or ""
    username = user.get("username", "") or ""
    return tg_id, first_name, username


# ---------------------------
# Business logic
# ---------------------------
DRINK_MULTIPLIERS = {
    "water": 1.0,
    "tea": 0.8,
    "coffee": 0.6,
}

DRINK_ICONS = {
    "water": "💧",
    "tea": "🍵",
    "coffee": "☕",
}

ACHIEVEMENTS = [
    {"id": "streak7", "title": "7 дней подряд", "subtitle": "Первая серьёзная серия", "threshold": 7, "icon": "🏅"},
    {"id": "streak14", "title": "14 дней подряд", "subtitle": "Ты в потоке", "threshold": 14, "icon": "🥈"},
    {"id": "streak30", "title": "30 дней подряд", "subtitle": "Режим PRO", "threshold": 30, "icon": "🥇"},
]


def calc_goal(weight_kg: int, factor_ml: int) -> int:
    if weight_kg <= 0:
        return 0
    factor_ml = max(30, min(35, factor_ml))
    return int(weight_kg * factor_ml)


def get_level(streak: int) -> str:
    if streak >= 60:
        return "👑 ГидроГуру"
    if streak >= 30:
        return "🐬 ГидроПро"
    if streak >= 7:
        return "🌊 Поток"
    if streak >= 1:
        return "💧 Новичок"
    return "—"


def next_level_target(streak: int) -> Optional[int]:
    if streak < 7:
        return 7
    if streak < 30:
        return 30
    if streak < 60:
        return 60
    return None


def ensure_user(conn: sqlite3.Connection, tg_id: int, first_name: str, username: str) -> sqlite3.Row:
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE telegram_id=?", (tg_id,))
    row = cur.fetchone()
    if row:
        # Update names if changed
        cur.execute(
            "UPDATE users SET first_name=?, username=? WHERE telegram_id=?",
            (first_name, username, tg_id),
        )
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
    cur.execute(
        "SELECT COALESCE(SUM(effective_ml),0) AS total FROM entries WHERE telegram_id=? AND date=?",
        (tg_id, day),
    )
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
    """
    Recompute current streak ending at today and best streak from daily_stats.
    Uses met_goal=1 days.
    """
    cur = conn.cursor()
    cur.execute(
        "SELECT date, met_goal FROM daily_stats WHERE telegram_id=? ORDER BY date ASC",
        (tg_id,),
    )
    rows = cur.fetchall()
    if not rows:
        cur.execute("UPDATE users SET current_streak=0, best_streak=0 WHERE telegram_id=?", (tg_id,))
        conn.commit()
        return 0, 0

    # best streak
    best = 0
    run = 0
    for r in rows:
        if int(r["met_goal"]) == 1:
            run += 1
            best = max(best, run)
        else:
            run = 0

    # current streak ending at today_str
    # Walk backwards from today to earlier dates
    cur.execute(
        "SELECT date, met_goal FROM daily_stats WHERE telegram_id=? ORDER BY date DESC",
        (tg_id,),
    )
    rows_desc = cur.fetchall()
    current = 0
    expected = date.fromisoformat(today_str)
    for r in rows_desc:
        d = date.fromisoformat(r["date"])
        if d != expected:
            # if there is a gap, streak breaks
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
    cur.execute(
        "SELECT * FROM entries WHERE telegram_id=? AND date=? ORDER BY ts DESC, id DESC",
        (tg_id, day),
    )
    out = []
    for r in cur.fetchall():
        out.append(
            {
                "id": int(r["id"]),
                "ts": r["ts"],
                "drink_type": r["drink_type"],
                "icon": DRINK_ICONS.get(r["drink_type"], "💧"),
                "raw_ml": int(r["raw_ml"]),
                "effective_ml": int(r["effective_ml"]),
            }
        )
    return out


def get_last_n_days(conn: sqlite3.Connection, tg_id: int, end_day: str, n: int, goal_ml: int) -> List[Dict[str, Any]]:
    end = date.fromisoformat(end_day)
    days = [(end - timedelta(days=i)).isoformat() for i in range(n - 1, -1, -1)]
    cur = conn.cursor()
    # preload daily_stats
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


def drink_breakdown(conn: sqlite3.Connection, tg_id: int, start_day: str, end_day: str) -> Dict[str, Any]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT drink_type,
               COALESCE(SUM(raw_ml),0) AS raw_sum,
               COALESCE(SUM(effective_ml),0) AS eff_sum
        FROM entries
        WHERE telegram_id=? AND date>=? AND date<=?
        GROUP BY drink_type
        """,
        (tg_id, start_day, end_day),
    )
    m = {"water": {"raw": 0, "effective": 0}, "tea": {"raw": 0, "effective": 0}, "coffee": {"raw": 0, "effective": 0}}
    for r in cur.fetchall():
        t = r["drink_type"]
        if t not in m:
            m[t] = {"raw": 0, "effective": 0}
        m[t]["raw"] = int(r["raw_sum"])
        m[t]["effective"] = int(r["eff_sum"])
    return m


def calendar_grid(conn: sqlite3.Connection, tg_id: int, month_ym: str, goal_ml: int) -> Dict[str, Any]:
    # month_ym: YYYY-MM
    y, m = map(int, month_ym.split("-"))
    first = date(y, m, 1)
    last_day = cal.monthrange(y, m)[1]
    last = date(y, m, last_day)

    # start from Monday
    start = first - timedelta(days=(first.weekday()))  # weekday Monday=0
    # 6 weeks grid
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
        ratio = 0.0
        if g > 0:
            ratio = min(2.0, total / g)  # cap
        in_month = (d.month == m)
        out_days.append(
            {
                "date": iso,
                "day": d.day,
                "in_month": in_month,
                "total_ml": total,
                "goal_ml": g,
                "ratio": ratio,  # 0..2
            }
        )

    return {"month": month_ym, "days": out_days}


def month_summary(conn: sqlite3.Connection, tg_id: int, month_ym: str, goal_ml_fallback: int) -> Dict[str, Any]:
    y, m = map(int, month_ym.split("-"))
    first = date(y, m, 1)
    last_day = cal.monthrange(y, m)[1]
    last = date(y, m, last_day)

    cur = conn.cursor()
    cur.execute(
        "SELECT date, total_ml, goal_ml, met_goal FROM daily_stats WHERE telegram_id=? AND date>=? AND date<=? ORDER BY date ASC",
        (tg_id, first.isoformat(), last.isoformat()),
    )
    rows = cur.fetchall()

    by_date = {r["date"]: r for r in rows}
    daily = []
    total_sum = 0
    goal_sum = 0
    met_days = 0
    for day in range(1, last_day + 1):
        d = date(y, m, day).isoformat()
        r = by_date.get(d)
        t = int(r["total_ml"]) if r else 0
        g = int(r["goal_ml"]) if r else goal_ml_fallback
        met = int(r["met_goal"]) if r else (1 if g > 0 and t >= g else 0)
        total_sum += t
        goal_sum += g if g > 0 else 0
        met_days += 1 if met == 1 else 0
        daily.append({"date": d, "total_ml": t, "goal_ml": g, "met_goal": met})

    pct_days = int(round((met_days / max(1, last_day)) * 100))
    return {
        "month": month_ym,
        "days": daily,
        "total_ml": total_sum,
        "goal_ml": goal_sum,
        "met_days": met_days,
        "pct_days": pct_days,
    }


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
    month = payload.get("month", "")
    client_date = payload.get("client_date") or datetime.now(timezone.utc).date().isoformat()

    tg_id, first_name, username = get_user_identity(init_data)

    conn = db_connect()
    user = ensure_user(conn, tg_id, first_name, username)

    # If goal not set but weight exists, compute it
    weight = int(user["weight_kg"] or 0)
    factor = int(user["factor_ml"] or 33)
    goal_ml = int(user["goal_ml"] or 0)
    if goal_ml <= 0 and weight > 0:
        goal_ml = calc_goal(weight, factor)
        conn.execute("UPDATE users SET goal_ml=? WHERE telegram_id=?", (goal_ml, tg_id))
        conn.commit()

    # Update daily stats for today to keep met_goal correct
    today_stats = upsert_daily_stats(conn, tg_id, client_date, goal_ml)
    cur_streak, best_streak = recompute_streaks(conn, tg_id, client_date)

    level = get_level(cur_streak)
    next_target = next_level_target(cur_streak)
    to_next = (next_target - cur_streak) if next_target else 0

    entries = get_today_entries(conn, tg_id, client_date)
    total_today = int(today_stats["total_ml"])
    pct_today = int(round((total_today / goal_ml) * 100)) if goal_ml > 0 else 0
    pct_today = max(0, min(100, pct_today))

    last7 = get_last_n_days(conn, tg_id, client_date, 7, goal_ml)

    # PRO stats
    last7_vals = [d["total_ml"] for d in last7]
    avg7 = int(round(sum(last7_vals) / 7)) if last7_vals else 0
    median7 = int(sorted(last7_vals)[len(last7_vals)//2]) if last7_vals else 0

    above = sum(1 for d in last7 if d["goal_ml"] > 0 and d["total_ml"] >= d["goal_ml"])
    below = 7 - above

    best_day = max(last7_vals) if last7_vals else 0

    # moving average (window=3) for chart overlay
    ma = []
    for i in range(len(last7_vals)):
        w = last7_vals[max(0, i - 2): i + 1]
        ma.append(int(round(sum(w) / len(w))) if w else 0)

    # Weekly totals
    week_total = sum(d["total_ml"] for d in last7)
    week_goal = sum((d["goal_ml"] or 0) for d in last7) or 0
    week_pct = int(round((week_total / week_goal) * 100)) if week_goal > 0 else 0
    week_pct = max(0, min(100, week_pct))

    # Drink breakdown for last7
    start7 = last7[0]["date"]
    drinks7 = drink_breakdown(conn, tg_id, start7, client_date)

    # Calendar
    if not month:
        # default to current month in client_date
        y, m = client_date.split("-")[:2]
        month = f"{y}-{m}"
    cal_data = calendar_grid(conn, tg_id, month, goal_ml)

    # Achievements
    ach = []
    for a in ACHIEVEMENTS:
        unlocked = best_streak >= a["threshold"]
        ach.append({**a, "unlocked": unlocked})

    # Best streak already stored; also show best day last 30 (optional)
    state = {
        "user": {
            "telegram_id": tg_id,
            "first_name": first_name,
            "username": username,
        },
        "profile": {
            "weight_kg": weight,
            "factor_ml": factor,
            "goal_ml": goal_ml,
            "level": level,
            "to_next": to_next,
        },
        "today": {
            "date": client_date,
            "total_ml": total_today,
            "goal_ml": goal_ml,
            "pct": pct_today,
            "entries": entries,
        },
        "stats": {
            "last7": last7,
            "avg7": avg7,
            "median7": median7,
            "best_day": best_day,
            "above": above,
            "below": below,
            "current_streak": cur_streak,
            "best_streak": best_streak,
            "moving_avg7": ma,
            "week_total": week_total,
            "week_goal": week_goal,
            "week_pct": week_pct,
            "drinks7": drinks7,
        },
        "calendar": cal_data,
        "achievements": ach,
        "drink_icons": DRINK_ICONS,
    }

    conn.close()
    return JSONResponse(state)


@app.post("/api/add")
async def api_add(payload: Dict[str, Any]):
    init_data = payload.get("initData", "")
    ml = int(payload.get("ml", 0) or 0)
    drink_type = (payload.get("type") or "water").strip().lower()
    client_date = payload.get("client_date") or datetime.now(timezone.utc).date().isoformat()
    client_ts = payload.get("client_ts") or datetime.now(timezone.utc).isoformat()

    if ml <= 0 or ml > 5000:
        raise HTTPException(status_code=400, detail="Invalid ml")

    if drink_type not in DRINK_MULTIPLIERS:
        drink_type = "water"

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

    mult = DRINK_MULTIPLIERS.get(drink_type, 1.0)
    effective = int(round(ml * mult))

    # before stats (to detect first-time goal reach)
    before = upsert_daily_stats(conn, tg_id, client_date, goal_ml)
    before_met = int(before["met_goal"])

    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO entries (telegram_id, date, ts, drink_type, raw_ml, effective_ml)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (tg_id, client_date, client_ts, drink_type, ml, effective),
    )
    entry_id = int(cur.lastrowid)
    conn.commit()

    # after stats
    after = upsert_daily_stats(conn, tg_id, client_date, goal_ml)
    after_met = int(after["met_goal"])

    # recompute streaks
    cur_streak, best_streak = recompute_streaks(conn, tg_id, client_date)

    # weekly recompute for confetti logic
    last7 = get_last_n_days(conn, tg_id, client_date, 7, goal_ml)
    week_total = sum(d["total_ml"] for d in last7)
    week_goal = sum((d["goal_ml"] or 0) for d in last7) or 0
    week_done = (week_goal > 0 and week_total >= week_goal)

    # If today goal reached now but not before -> goal_completed_today True
    goal_completed_today = (after_met == 1 and before_met == 0)

    conn.close()
    return JSONResponse(
        {
            "ok": True,
            "entry_id": entry_id,
            "today_total": int(after["total_ml"]),
            "today_goal": goal_ml,
            "today_met": int(after["met_goal"]),
            "current_streak": cur_streak,
            "best_streak": best_streak,
            "goal_completed_today": goal_completed_today,
            "week_completed": bool(week_done),
        }
    )


@app.post("/api/undo")
async def api_undo(payload: Dict[str, Any]):
    init_data = payload.get("initData", "")
    entry_id = int(payload.get("entry_id", 0) or 0)
    client_date = payload.get("client_date") or datetime.now(timezone.utc).date().isoformat()

    if entry_id <= 0:
        raise HTTPException(status_code=400, detail="Invalid entry_id")

    tg_id, first_name, username = get_user_identity(init_data)

    conn = db_connect()
    user = ensure_user(conn, tg_id, first_name, username)
    goal_ml = int(user["goal_ml"] or 0)

    cur = conn.cursor()
    cur.execute("SELECT * FROM entries WHERE id=? AND telegram_id=?", (entry_id, tg_id))
    r = cur.fetchone()
    if not r:
        conn.close()
        raise HTTPException(status_code=404, detail="Entry not found")

    day = r["date"]
    cur.execute("DELETE FROM entries WHERE id=? AND telegram_id=?", (entry_id, tg_id))
    conn.commit()

    # update stats for that day and today (in case)
    upsert_daily_stats(conn, tg_id, day, goal_ml)
    upsert_daily_stats(conn, tg_id, client_date, goal_ml)

    cur_streak, best_streak = recompute_streaks(conn, tg_id, client_date)

    # return updated today totals
    cur.execute("SELECT * FROM daily_stats WHERE telegram_id=? AND date=?", (tg_id, client_date))
    today_stats = cur.fetchone()
    today_total = int(today_stats["total_ml"]) if today_stats else 0
    today_met = int(today_stats["met_goal"]) if today_stats else 0

    conn.close()
    return JSONResponse(
        {
            "ok": True,
            "today_total": today_total,
            "today_goal": goal_ml,
            "today_met": today_met,
            "current_streak": cur_streak,
            "best_streak": best_streak,
        }
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

    # if weight or factor changed, recompute goal unless goal_ml explicitly set
    if goal_ml is None and (weight_kg is not None or factor_ml is not None):
        computed = calc_goal(new_weight, new_factor)
        if computed > 0:
            new_goal = computed

    conn.execute(
        "UPDATE users SET weight_kg=?, factor_ml=?, goal_ml=? WHERE telegram_id=?",
        (new_weight, new_factor, new_goal, tg_id),
    )
    conn.commit()

    # update today's daily_stats goal
    today = payload.get("client_date") or datetime.now(timezone.utc).date().isoformat()
    upsert_daily_stats(conn, tg_id, today, new_goal)

    conn.close()
    return JSONResponse({"ok": True, "weight_kg": new_weight, "factor_ml": new_factor, "goal_ml": new_goal})


# ---------------------------
# Export: CSV + PDF
# ---------------------------
def _export_auth(init_data: str) -> Tuple[int, str, str]:
    return get_user_identity(init_data)


@app.get("/export/csv")
def export_csv(initData: str, month: str):
    tg_id, first_name, username = _export_auth(initData)
    # month = YYYY-MM
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("SELECT goal_ml FROM users WHERE telegram_id=?", (tg_id,))
    goal_ml = int((cur.fetchone() or {"goal_ml": 0})["goal_ml"] or 0)

    summ = month_summary(conn, tg_id, month, goal_ml)

    # entries of that month
    y, m = map(int, month.split("-"))
    first = date(y, m, 1).isoformat()
    last = date(y, m, cal.monthrange(y, m)[1]).isoformat()
    cur.execute(
        """
        SELECT id, date, ts, drink_type, raw_ml, effective_ml
        FROM entries
        WHERE telegram_id=? AND date>=? AND date<=?
        ORDER BY date ASC, ts ASC
        """,
        (tg_id, first, last),
    )
    entries = cur.fetchall()
    conn.close()

    sio = StringIO()
    sio.write("date,total_ml,goal_ml,met_goal\n")
    for d in summ["days"]:
        sio.write(f"{d['date']},{d['total_ml']},{d['goal_ml']},{d['met_goal']}\n")

    sio.write("\nentries:\n")
    sio.write("id,date,ts,drink_type,raw_ml,effective_ml\n")
    for e in entries:
        sio.write(f"{e['id']},{e['date']},{e['ts']},{e['drink_type']},{e['raw_ml']},{e['effective_ml']}\n")

    data = sio.getvalue().encode("utf-8")
    filename = f"aquaflow_{month}.csv"
    return StreamingResponse(
        BytesIO(data),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/export/pdf")
def export_pdf(initData: str, month: str):
    tg_id, first_name, username = _export_auth(initData)

    conn = db_connect()
    cur = conn.cursor()
    cur.execute("SELECT goal_ml, best_streak FROM users WHERE telegram_id=?", (tg_id,))
    row = cur.fetchone() or {"goal_ml": 0, "best_streak": 0}
    goal_ml = int(row["goal_ml"] or 0)
    best_streak = int(row["best_streak"] or 0)

    summ = month_summary(conn, tg_id, month, goal_ml)
    conn.close()

    # Lazy import reportlab (keeps startup fast)
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    from reportlab.lib.units import mm

    buff = BytesIO()
    c = canvas.Canvas(buff, pagesize=A4)
    w, h = A4

    # Header
    c.setFont("Helvetica-Bold", 18)
    c.drawString(24*mm, h - 24*mm, f"{APP_NAME} — Отчёт за {month}")
    c.setFont("Helvetica", 11)
    c.drawString(24*mm, h - 32*mm, f"Пользователь: {first_name} @{username}" if username else f"Пользователь: {first_name}")
    c.drawString(24*mm, h - 38*mm, f"Лучший стрик: {best_streak} дней")
    c.drawString(24*mm, h - 44*mm, f"Выполнено дней: {summ['met_days']} / {len(summ['days'])} ({summ['pct_days']}%)")

    # Summary numbers
    c.setFont("Helvetica-Bold", 12)
    c.drawString(24*mm, h - 56*mm, f"Всего за месяц: {summ['total_ml']} мл")
    c.drawString(24*mm, h - 62*mm, f"План за месяц:  {summ['goal_ml']} мл")

    # Chart area
    chart_x = 24*mm
    chart_y = h - 200*mm
    chart_w = w - 48*mm
    chart_h = 110*mm

    c.setLineWidth(1)
    c.setStrokeColorRGB(1, 1, 1, alpha=0.15)
    c.roundRect(chart_x, chart_y, chart_w, chart_h, 10*mm, stroke=1, fill=0)

    # Draw bars
    days = summ["days"]
    max_val = max([d["total_ml"] for d in days] + [goal_ml, 1])
    bar_w = chart_w / max(1, len(days))
    for i, d in enumerate(days):
        t = d["total_ml"]
        g = d["goal_ml"] or goal_ml
        # bar height
        bh = (t / max_val) * (chart_h - 18*mm)
        bx = chart_x + i*bar_w + 0.3*mm
        by = chart_y + 8*mm

        # filled day vs not
        if g > 0 and t >= g:
            c.setFillColorRGB(0.2, 0.83, 0.6, alpha=0.85)  # green-ish
        else:
            c.setFillColorRGB(0.12, 0.61, 0.94, alpha=0.75) # blue-ish
        c.setStrokeColorRGB(1, 1, 1, alpha=0)
        c.rect(bx, by, max(0.5, bar_w - 0.6*mm), bh, stroke=0, fill=1)

    # Goal line (approx)
    if goal_ml > 0:
        gy = chart_y + 8*mm + (goal_ml / max_val) * (chart_h - 18*mm)
        c.setStrokeColorRGB(1, 1, 1, alpha=0.35)
        c.setLineWidth(1)
        c.line(chart_x + 2*mm, gy, chart_x + chart_w - 2*mm, gy)
        c.setFont("Helvetica", 9)
        c.setFillColorRGB(1, 1, 1, alpha=0.75)
        c.drawString(chart_x + 2*mm, gy + 2*mm, "Норма")

    # Footer
    c.setFont("Helvetica", 9)
    c.setFillColorRGB(1, 1, 1, alpha=0.55)
    c.drawString(24*mm, 14*mm, "AquaFlow • Telegram Mini App")

    c.showPage()
    c.save()

    buff.seek(0)
    filename = f"aquaflow_{month}.pdf"
    return StreamingResponse(
        buff,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
