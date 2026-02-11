# AquaFlow (Telegram Mini App)

Это финальная сборка с:
- Геймификация 2.0 (уровни + прогресс)
- Реактивная анимация (подпрыгивание воды)
- Weekly WOW (вода заполняет экран за неделю, ускорение волн >80%, конфетти на 100%)
- Календарь-теплокарта (как GitHub)
- Типы напитков (вода 100%, чай 80%, кофе 60%)
- Undo (5 секунд) после добавления
- PRO-статы (среднее, медиана, дни норма/ниже, скользящее среднее)
- Экспорт CSV и PDF (месяц)

## Railway: чтобы данные НЕ слетали при деплоях

1) В Railway открой Service `water_tracker`
2) Добавь **Volume** (Storage / Persistent Volume)
   - Mount path: `/data`
3) В Variables добавь:
   - `DB_PATH=/data/water.db`
4) Рекомендуется добавить:
   - `BOT_TOKEN=...` (токен Telegram бота)
5) Redeploy

## Запуск локально

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
export BOT_TOKEN="YOUR_TOKEN"
export DB_PATH="water.db"
uvicorn app:app --reload --port 8000
```

Открой: http://127.0.0.1:8000

> Для полноценной работы Telegram Mini App нужен запуск из Telegram, чтобы приходил `initData`.
