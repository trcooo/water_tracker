# AquaFlow — previous stable version

Стабильная версия Mini App:
- трекинг воды
- формула нормы (вес × 30–35 мл)
- стрик + лучший стрик
- достижения 7/14/30
- статистика за 7 дней
- календарь (с фикс-обрезкой, чтобы не "съезжал" в Telegram iOS)
- конфетти + уведомление "Цель выполнена"

## Railway: чтобы данные не слетали
1) Добавь Volume/Storage к сервису FastAPI:
   - Mount path: /data
2) Variables:
   - DB_PATH=/data/water.db
3) Start Command:
   - uvicorn app:app --host 0.0.0.0 --port $PORT

BOT_TOKEN в этой версии не обязателен.
