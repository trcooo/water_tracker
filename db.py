import sqlite3
from datetime import datetime, timedelta
from typing import Optional, Any, Dict, List

class Database:
    def __init__(self, path: str):
        self.path = path
        self._init()

    def _conn(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self):
        with self._conn() as c:
            c.execute("""
            CREATE TABLE IF NOT EXISTS users (
                tg_id INTEGER PRIMARY KEY,
                weight_kg INTEGER,
                ml_per_kg INTEGER NOT NULL DEFAULT 33,
                goal_ml INTEGER NOT NULL DEFAULT 2000
            )
            """)
            c.execute("""
            CREATE TABLE IF NOT EXISTS water_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_id INTEGER NOT NULL,
                ts TEXT NOT NULL,
                amount_ml INTEGER NOT NULL,
                FOREIGN KEY (tg_id) REFERENCES users(tg_id)
            )
            """)
            c.commit()

    def ensure_user(self, tg_id: int, default_ml_per_kg: int = 33):
        with self._conn() as c:
            c.execute(
                "INSERT OR IGNORE INTO users (tg_id, ml_per_kg, goal_ml) VALUES (?, ?, ?)",
                (tg_id, default_ml_per_kg, 2000),
            )
            c.commit()

    def get_profile(self, tg_id: int) -> Dict[str, Any]:
        with self._conn() as c:
            row = c.execute("SELECT tg_id, weight_kg, ml_per_kg, goal_ml FROM users WHERE tg_id=?", (tg_id,)).fetchone()
        if not row:
            return {"tg_id": tg_id, "weight_kg": None, "ml_per_kg": 33, "goal_ml": 2000}
        return dict(row)

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
            return prof.get("goal_ml", 2000)
        goal = int(w) * int(k)
        self.set_goal(tg_id, goal)
        return goal

    def add_water(self, tg_id: int, amount_ml: int, ts: Optional[str] = None):
        ts = ts or datetime.utcnow().isoformat()
        with self._conn() as c:
            c.execute(
                "INSERT INTO water_log (tg_id, ts, amount_ml) VALUES (?, ?, ?)",
                (tg_id, ts, amount_ml),
            )
            c.commit()

    def today_total(self, tg_id: int, tz_offset_min: int = 0) -> int:
        now_utc = datetime.utcnow()
        now_user = now_utc + timedelta(minutes=tz_offset_min)
        d = now_user.date()

        start_user = datetime(d.year, d.month, d.day)
        end_user = start_user + timedelta(days=1)

        start_utc = start_user - timedelta(minutes=tz_offset_min)
        end_utc = end_user - timedelta(minutes=tz_offset_min)

        with self._conn() as c:
            row = c.execute("""
                SELECT COALESCE(SUM(amount_ml), 0) AS total
                FROM water_log
                WHERE tg_id=? AND ts>=? AND ts<?
            """, (tg_id, start_utc.isoformat(), end_utc.isoformat())).fetchone()
        return int(row["total"]) if row else 0

    def today_entries(self, tg_id: int, tz_offset_min: int = 0, limit: int = 20) -> List[Dict[str, Any]]:
        now_utc = datetime.utcnow()
        now_user = now_utc + timedelta(minutes=tz_offset_min)
        d = now_user.date()

        start_user = datetime(d.year, d.month, d.day)
        end_user = start_user + timedelta(days=1)

        start_utc = start_user - timedelta(minutes=tz_offset_min)
        end_utc = end_user - timedelta(minutes=tz_offset_min)

        with self._conn() as c:
            rows = c.execute("""
                SELECT ts, amount_ml
                FROM water_log
                WHERE tg_id=? AND ts>=? AND ts<?
                ORDER BY ts DESC
                LIMIT ?
            """, (tg_id, start_utc.isoformat(), end_utc.isoformat(), limit)).fetchall()
        return [{"ts": r["ts"], "amount_ml": int(r["amount_ml"])} for r in rows]
