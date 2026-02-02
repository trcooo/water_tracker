import sqlite3
from datetime import datetime, timedelta, date
from typing import Optional, Any, Dict, List, Tuple

def utcnow_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat()

def local_date_str_from_utc(now_utc: datetime, tz_offset_min: int) -> str:
    d = (now_utc + timedelta(minutes=tz_offset_min)).date()
    return d.isoformat()

def parse_date(s: str) -> date:
    y, m, d = s.split("-")
    return date(int(y), int(m), int(d))

class Database:
    def __init__(self, path: str):
        self.path = path
        self._init()

    def _conn(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _try_alter(self, sql: str):
        try:
            with self._conn() as c:
                c.execute(sql)
                c.commit()
        except sqlite3.OperationalError:
            pass  # колонка уже существует или ALTER недоступен

    def _init(self):
        with self._conn() as c:
            c.execute("""
            CREATE TABLE IF NOT EXISTS users (
                tg_id INTEGER PRIMARY KEY,
                weight_kg INTEGER,
                ml_per_kg INTEGER NOT NULL DEFAULT 33,
                goal_ml INTEGER NOT NULL DEFAULT 2000,
                current_streak INTEGER NOT NULL DEFAULT 0,
                best_streak INTEGER NOT NULL DEFAULT 0,
                last_streak_date TEXT
            )
            """)

            c.execute("""
            CREATE TABLE IF NOT EXISTS water_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_id INTEGER NOT NULL,
                ts_utc TEXT NOT NULL,
                local_date TEXT NOT NULL,
                amount_ml INTEGER NOT NULL,
                FOREIGN KEY (tg_id) REFERENCES users(tg_id)
            )
            """)

            c.execute("""
            CREATE TABLE IF NOT EXISTS daily_stats (
                tg_id INTEGER NOT NULL,
                local_date TEXT NOT NULL,
                total_ml INTEGER NOT NULL,
                goal_ml INTEGER NOT NULL,
                updated_utc TEXT NOT NULL,
                PRIMARY KEY (tg_id, local_date),
                FOREIGN KEY (tg_id) REFERENCES users(tg_id)
            )
            """)
            c.commit()

        # миграции (если у тебя старая схема)
        self._try_alter("ALTER TABLE water_log ADD COLUMN local_date TEXT")
        self._try_alter("ALTER TABLE water_log ADD COLUMN ts_utc TEXT")
        self._try_alter("ALTER TABLE users ADD COLUMN current_streak INTEGER DEFAULT 0")
        self._try_alter("ALTER TABLE users ADD COLUMN best_streak INTEGER DEFAULT 0")
        self._try_alter("ALTER TABLE users ADD COLUMN last_streak_date TEXT")

    def ensure_user(self, tg_id: int, default_ml_per_kg: int = 33):
        with self._conn() as c:
            c.execute("""
                INSERT OR IGNORE INTO users (tg_id, ml_per_kg, goal_ml, current_streak, best_streak)
                VALUES (?, ?, ?, 0, 0)
            """, (tg_id, default_ml_per_kg, 2000))
            c.commit()

    def get_profile(self, tg_id: int) -> Dict[str, Any]:
        with self._conn() as c:
            row = c.execute("""
                SELECT tg_id, weight_kg, ml_per_kg, goal_ml, current_streak, best_streak, last_streak_date
                FROM users WHERE tg_id=?
            """, (tg_id,)).fetchone()
        return dict(row) if row else {
            "tg_id": tg_id, "weight_kg": None, "ml_per_kg": 33, "goal_ml": 2000,
            "current_streak": 0, "best_streak": 0, "last_streak_date": None
        }

    def set_weight(self, tg_id: int, weight_kg: int):
        with self._conn() as c:
            c.execute("UPDATE users SET weight_kg=? WHERE tg_id=?", (weight_kg, tg_id))
            c.commit()

    def set_factor(self, tg_id: int, ml_per_kg: int):
        with self._conn() as c:
            c.execute("UPDATE users SET ml_per_kg=? WHERE tg_id=?", (ml_per_kg, tg_id))
            c.commit()

    def set_goal(self, tg_id: int, goal_ml: int):
        with self._conn() as c:
            c.execute("UPDATE users SET goal_ml=? WHERE tg_id=?", (goal_ml, tg_id))
            c.commit()

    def recompute_goal_from_formula(self, tg_id: int) -> int:
        prof = self.get_profile(tg_id)
        w = prof.get("weight_kg")
        k = prof.get("ml_per_kg", 33)
        if not w:
            return int(prof.get("goal_ml", 2000))
        goal = int(w) * int(k)
        self.set_goal(tg_id, goal)
        return goal

    # --- water log / daily stats ---

    def add_water(self, tg_id: int, amount_ml: int, tz_offset_min: int):
        now_utc = datetime.utcnow().replace(microsecond=0)
        local_date = local_date_str_from_utc(now_utc, tz_offset_min)
        ts_utc = now_utc.isoformat()

        with self._conn() as c:
            c.execute("""
                INSERT INTO water_log (tg_id, ts_utc, local_date, amount_ml)
                VALUES (?, ?, ?, ?)
            """, (tg_id, ts_utc, local_date, amount_ml))
            c.commit()

        # обновим daily_stats и streak
        self.refresh_daily_stats_for_date(tg_id, local_date)

    def get_total_for_date(self, tg_id: int, local_date: str) -> int:
        with self._conn() as c:
            row = c.execute("""
                SELECT COALESCE(SUM(amount_ml), 0) AS total
                FROM water_log
                WHERE tg_id=? AND local_date=?
            """, (tg_id, local_date)).fetchone()
        return int(row["total"]) if row else 0

    def refresh_daily_stats_for_date(self, tg_id: int, local_date: str):
        prof = self.get_profile(tg_id)
        goal = int(prof.get("goal_ml", 2000))
        total = self.get_total_for_date(tg_id, local_date)
        now = utcnow_iso()

        with self._conn() as c:
            c.execute("""
                INSERT INTO daily_stats (tg_id, local_date, total_ml, goal_ml, updated_utc)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(tg_id, local_date) DO UPDATE SET
                    total_ml=excluded.total_ml,
                    goal_ml=excluded.goal_ml,
                    updated_utc=excluded.updated_utc
            """, (tg_id, local_date, total, goal, now))
            c.commit()

        self.update_streak(tg_id, local_date)

    def today_state(self, tg_id: int, tz_offset_min: int) -> Tuple[str, int, int]:
        now_utc = datetime.utcnow().replace(microsecond=0)
        local_date = local_date_str_from_utc(now_utc, tz_offset_min)
        total = self.get_total_for_date(tg_id, local_date)
        goal = int(self.get_profile(tg_id).get("goal_ml", 2000))
        return local_date, total, goal

    def recent_entries_today(self, tg_id: int, tz_offset_min: int, limit: int = 15) -> List[Dict[str, Any]]:
        now_utc = datetime.utcnow().replace(microsecond=0)
        local_date = local_date_str_from_utc(now_utc, tz_offset_min)

        with self._conn() as c:
            rows = c.execute("""
                SELECT ts_utc, amount_ml
                FROM water_log
                WHERE tg_id=? AND local_date=?
                ORDER BY ts_utc DESC
                LIMIT ?
            """, (tg_id, local_date, limit)).fetchall()

        return [{"ts": r["ts_utc"], "amount_ml": int(r["amount_ml"])} for r in rows]

    # --- streak logic ---

    def get_day_done(self, tg_id: int, local_date: str) -> bool:
        with self._conn() as c:
            row = c.execute("""
                SELECT total_ml, goal_ml FROM daily_stats
                WHERE tg_id=? AND local_date=?
            """, (tg_id, local_date)).fetchone()
        if not row:
            return False
        return int(row["total_ml"]) >= int(row["goal_ml"])

    def update_streak(self, tg_id: int, local_date: str):
        prof = self.get_profile(tg_id)
        last = prof.get("last_streak_date")
        current = int(prof.get("current_streak", 0))
        best = int(prof.get("best_streak", 0))

        done_today = self.get_day_done(tg_id, local_date)
        today = parse_date(local_date)

        # если сегодня не выполнено — не обнуляем моментально (чтобы не “ломалось” утром),
        # но текущий стрик по факту считается как “последовательность завершённых дней”.
        # Мы обновляем стрик только когда день выполнен.
        if not done_today:
            return

        if last:
            last_d = parse_date(last)
            delta = (today - last_d).days
            if delta == 0:
                # уже обновляли сегодня
                return
            elif delta == 1:
                current += 1
            else:
                current = 1
        else:
            current = 1

        best = max(best, current)

        with self._conn() as c:
            c.execute("""
                UPDATE users
                SET current_streak=?, best_streak=?, last_streak_date=?
                WHERE tg_id=?
            """, (current, best, local_date, tg_id))
            c.commit()

    # --- calendar & stats ---

    def get_month_calendar(self, tg_id: int, year: int, month: int) -> Dict[str, Dict[str, int]]:
        """
        Возвращает словарь по дням месяца:
        { "YYYY-MM-DD": {"total_ml":.., "goal_ml":..} }
        """
        start = date(year, month, 1)
        if month == 12:
            end = date(year + 1, 1, 1)
        else:
            end = date(year, month + 1, 1)

        with self._conn() as c:
            rows = c.execute("""
                SELECT local_date, total_ml, goal_ml
                FROM daily_stats
                WHERE tg_id=? AND local_date>=? AND local_date<?
            """, (tg_id, start.isoformat(), end.isoformat())).fetchall()

        out: Dict[str, Dict[str, int]] = {}
        for r in rows:
            out[r["local_date"]] = {"total_ml": int(r["total_ml"]), "goal_ml": int(r["goal_ml"])}
        return out

    def get_last_n_days(self, tg_id: int, end_local_date: str, n: int = 7) -> List[Dict[str, Any]]:
        end_d = parse_date(end_local_date)
        start_d = end_d - timedelta(days=n - 1)

        with self._conn() as c:
            rows = c.execute("""
                SELECT local_date, total_ml, goal_ml
                FROM daily_stats
                WHERE tg_id=? AND local_date>=? AND local_date<=?
                ORDER BY local_date ASC
            """, (tg_id, start_d.isoformat(), end_d.isoformat())).fetchall()

        # заполним пропуски нулями (чтобы график был ровным)
        by_date = {r["local_date"]: (int(r["total_ml"]), int(r["goal_ml"])) for r in rows}
        out = []
        for i in range(n):
            d = (start_d + timedelta(days=i)).isoformat()
            total, goal = by_date.get(d, (0, int(self.get_profile(tg_id).get("goal_ml", 2000))))
            out.append({"date": d, "total_ml": total, "goal_ml": goal})
        return out

    def compute_stats(self, tg_id: int, today_local_date: str) -> Dict[str, Any]:
        prof = self.get_profile(tg_id)
        last7 = self.get_last_n_days(tg_id, today_local_date, 7)
        totals = [x["total_ml"] for x in last7]
        avg7 = int(round(sum(totals) / 7)) if totals else 0

        # лучший день за 30 дней
        end_d = parse_date(today_local_date)
        start_d = end_d - timedelta(days=29)
        with self._conn() as c:
            row = c.execute("""
                SELECT local_date, total_ml
                FROM daily_stats
                WHERE tg_id=? AND local_date>=? AND local_date<=?
                ORDER BY total_ml DESC
                LIMIT 1
            """, (tg_id, start_d.isoformat(), end_d.isoformat())).fetchone()

        best_day = {"date": None, "total_ml": 0}
        if row:
            best_day = {"date": row["local_date"], "total_ml": int(row["total_ml"])}

        return {
            "avg_7": avg7,
            "best_day": best_day,
            "current_streak": int(prof.get("current_streak", 0)),
            "best_streak": int(prof.get("best_streak", 0)),
            "last7": last7
        }
