# AquaFlow — Water Tracker (Telegram Mini App)

Фичи:
- трекинг воды
- формула нормы (вес × 30–35 мл)
- стрик + лучший стрик
- достижения 7/14/30
- статистика за 7 дней
- календарь (фикс для Telegram iOS)

## Почему «пропадает прогресс» на Railway
Railway деплоит приложение в контейнере с **эфемерной файловой системой**. Если ты пишешь SQLite в `water.db` внутри контейнера, то при редеплое/рестарте файл может исчезать → данные «сбрасываются».

## Решение A (рекомендовано): PostgreSQL на Railway
1) В проекте Railway добавь базу **PostgreSQL** (Project Canvas → `+ New` → Database → PostgreSQL).
2) В сервисе приложения открой **Variables** и добавь переменную `DATABASE_URL` как **Database Reference Variable** на `DATABASE_URL` твоей Postgres-сервиса.
3) Деплойни приложение — таблицы создадутся автоматически на старте.

Старт-команда (как в Procfile):
```
uvicorn app:app --host 0.0.0.0 --port $PORT
```

## Решение B (быстрый фикс): SQLite + Volume
Если хочешь оставить SQLite:
1) Добавь **Volume/Storage** к сервису приложения
   - Mount path: `/data`
2) Variables:
   - `DB_PATH=/data/water.db`

