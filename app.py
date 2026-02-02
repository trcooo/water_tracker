import logging
from contextlib import asynccontextmanager
from http import HTTPStatus
from datetime import datetime

from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ConversationHandler,
    ContextTypes, filters
)

from config import (
    BOT_TOKEN, WEBAPP_URL, WEBHOOK_PATH, WEBHOOK_SECRET, DB_PATH, DEFAULT_ML_PER_KG
)
from db import Database, local_date_str_from_utc
from security import verify_telegram_webapp_init_data

log = logging.getLogger("aquaflow")
logging.basicConfig(level=logging.INFO)

db = Database(DB_PATH)
templates = Jinja2Templates(directory="templates")

ASK_WEIGHT = 1

def webapp_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💧 Открыть AquaFlow", web_app=WebAppInfo(url=WEBAPP_URL + "/"))],
    ])

# --- BOT ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db.ensure_user(user.id, default_ml_per_kg=DEFAULT_ML_PER_KG)
    prof = db.get_profile(user.id)

    if not prof.get("weight_kg"):
        await update.message.reply_text(
            "Привет! Я AquaFlow 💧\n\n"
            "Чтобы рассчитать дневную норму воды, напиши свой вес в кг (например: 70)."
        )
        return ASK_WEIGHT

    goal = db.recompute_goal_from_formula(user.id)
    prof = db.get_profile(user.id)

    await update.message.reply_text(
        f"Готово ✅\nНорма: {prof['weight_kg']} × {prof['ml_per_kg']} = {goal} мл/день.\n\n"
        "Открывай Mini App:",
        reply_markup=webapp_keyboard()
    )
    return ConversationHandler.END

async def weight_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = (update.message.text or "").strip()
    try:
        w = int(text)
        if w < 20 or w > 300:
            raise ValueError()
    except ValueError:
        await update.message.reply_text("Введи вес числом от 20 до 300 (например 70).")
        return ASK_WEIGHT

    db.ensure_user(user.id, default_ml_per_kg=DEFAULT_ML_PER_KG)
    db.set_weight(user.id, w)
    goal = db.recompute_goal_from_formula(user.id)
    prof = db.get_profile(user.id)

    await update.message.reply_text(
        f"Супер! Запомнил: {w} кг.\nНорма: {w} × {prof['ml_per_kg']} = {goal} мл/день.\n\n"
        "Открывай AquaFlow:",
        reply_markup=webapp_keyboard()
    )
    return ConversationHandler.END

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Команды AquaFlow:\n"
        "/start — старт и установка веса\n"
        "/setweight 70 — изменить вес\n"
        "/setfactor 33 — коэффициент 30..35 мл/кг\n"
        "/water — открыть Mini App\n"
        "/stats — сколько выпито сегодня"
    )

async def setweight(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db.ensure_user(user.id, default_ml_per_kg=DEFAULT_ML_PER_KG)

    if not context.args:
        await update.message.reply_text("Использование: /setweight 70")
        return

    try:
        w = int(context.args[0])
        if w < 20 or w > 300:
            raise ValueError()
    except ValueError:
        await update.message.reply_text("Вес должен быть 20..300. Пример: /setweight 70")
        return

    db.set_weight(user.id, w)
    goal = db.recompute_goal_from_formula(user.id)
    prof = db.get_profile(user.id)
    await update.message.reply_text(
        f"Обновил ✅\nНорма: {w} × {prof['ml_per_kg']} = {goal} мл/день.",
        reply_markup=webapp_keyboard()
    )

async def setfactor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db.ensure_user(user.id, default_ml_per_kg=DEFAULT_ML_PER_KG)

    if not context.args:
        await update.message.reply_text("Использование: /setfactor 30..35 (например /setfactor 33)")
        return

    try:
        k = int(context.args[0])
        if k < 30 or k > 35:
            raise ValueError()
    except ValueError:
        await update.message.reply_text("Коэффициент должен быть 30..35. Пример: /setfactor 33")
        return

    db.set_factor(user.id, k)
    prof = db.get_profile(user.id)
    if prof.get("weight_kg"):
        goal = db.recompute_goal_from_formula(user.id)
        await update.message.reply_text(
            f"Готово ✅\nНорма: {prof['weight_kg']} × {k} = {goal} мл/день.",
            reply_markup=webapp_keyboard()
        )
    else:
        await update.message.reply_text(
            f"Поставил {k} мл/кг ✅\nТеперь укажи вес: /setweight 70"
        )

async def water(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Открывай AquaFlow:", reply_markup=webapp_keyboard())

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db.ensure_user(user.id, default_ml_per_kg=DEFAULT_ML_PER_KG)
    prof = db.get_profile(user.id)
    # В боте без TZ — считаем по UTC-дате
    today_local = datetime.utcnow().date().isoformat()
    db.refresh_daily_stats_for_date(user.id, today_local)
    total = db.get_total_for_date(user.id, today_local)
    goal = prof.get("goal_ml", 2000)
    await update.message.reply_text(
        f"Сегодня (UTC): {total} мл из {goal} мл.\n🔥 Стрик: {prof.get('current_streak', 0)}",
        reply_markup=webapp_keyboard()
    )

def build_telegram_app() -> Application:
    tg_app = Application.builder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={ASK_WEIGHT: [MessageHandler(filters.TEXT & ~filters.COMMAND, weight_input)]},
        fallbacks=[],
        allow_reentry=True,
    )

    tg_app.add_handler(conv)
    tg_app.add_handler(CommandHandler("help", help_cmd))
    tg_app.add_handler(CommandHandler("setweight", setweight))
    tg_app.add_handler(CommandHandler("setfactor", setfactor))
    tg_app.add_handler(CommandHandler("water", water))
    tg_app.add_handler(CommandHandler("stats", stats))
    return tg_app

telegram_app = build_telegram_app()

# --- FASTAPI LIFESPAN ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    await telegram_app.initialize()
    await telegram_app.start()

    webhook_url = WEBAPP_URL + WEBHOOK_PATH
    await telegram_app.bot.set_webhook(
        url=webhook_url,
        secret_token=WEBHOOK_SECRET or None,
        drop_pending_updates=True,
    )
    log.info("Webhook set: %s", webhook_url)

    try:
        yield
    finally:
        await telegram_app.stop()
        await telegram_app.shutdown()

app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")

# --- MINI APP PAGE ---
@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

def _auth_webapp(init_data: str) -> int:
    try:
        parsed = verify_telegram_webapp_init_data(init_data, BOT_TOKEN)
        user = parsed.get("user")
        if not user or "id" not in user:
            raise ValueError("No user id")
        tg_id = int(user["id"])
        db.ensure_user(tg_id, default_ml_per_kg=DEFAULT_ML_PER_KG)
        return tg_id
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Auth failed: {str(e)}")

def _build_state(tg_id: int, tz_offset_min: int) -> dict:
    prof = db.get_profile(tg_id)

    # если есть вес — держим цель по формуле
    if prof.get("weight_kg"):
        db.recompute_goal_from_formula(tg_id)
        prof = db.get_profile(tg_id)

    today_local_date = local_date_str_from_utc(datetime.utcnow(), tz_offset_min)
    db.refresh_daily_stats_for_date(tg_id, today_local_date)

    today_local_date, total, goal = db.today_state(tg_id, tz_offset_min)
    entries = db.recent_entries_today(tg_id, tz_offset_min)
    stats = db.compute_stats(tg_id, today_local_date)

    return {
        "tg_id": tg_id,
        "today_local_date": today_local_date,
        "today_ml": total,
        "goal_ml": goal,
        "entries": entries,
        "weight_kg": prof.get("weight_kg"),
        "ml_per_kg": prof.get("ml_per_kg"),
        "current_streak": prof.get("current_streak", 0),
        "best_streak": prof.get("best_streak", 0),
        "stats": stats
    }

# --- API ---
@app.post("/api/state")
async def api_state(request: Request):
    body = await request.json()
    tg_id = _auth_webapp(body.get("initData", ""))
    tz_offset_min = int(body.get("tzOffsetMin", 0))
    return JSONResponse(_build_state(tg_id, tz_offset_min))

@app.post("/api/add")
async def api_add(request: Request):
    body = await request.json()
    tg_id = _auth_webapp(body.get("initData", ""))
    tz_offset_min = int(body.get("tzOffsetMin", 0))
    amount_ml = int(body.get("amountMl", 0))

    if amount_ml <= 0 or amount_ml > 5000:
        raise HTTPException(status_code=400, detail="amountMl must be 1..5000")

    db.add_water(tg_id, amount_ml, tz_offset_min)

    return JSONResponse({"ok": True, "state": _build_state(tg_id, tz_offset_min)})

@app.post("/api/goal")
async def api_goal(request: Request):
    body = await request.json()
    tg_id = _auth_webapp(body.get("initData", ""))
    tz_offset_min = int(body.get("tzOffsetMin", 0))
    goal_ml = int(body.get("goalMl", 0))

    if goal_ml < 500 or goal_ml > 10000:
        raise HTTPException(status_code=400, detail="goalMl must be 500..10000")

    db.set_goal(tg_id, goal_ml)

    # обновим daily_stats на сегодня
    today_local_date = local_date_str_from_utc(datetime.utcnow(), tz_offset_min)
    db.refresh_daily_stats_for_date(tg_id, today_local_date)

    return JSONResponse({"ok": True, "state": _build_state(tg_id, tz_offset_min)})

@app.post("/api/profile")
async def api_profile(request: Request):
    body = await request.json()
    tg_id = _auth_webapp(body.get("initData", ""))
    tz_offset_min = int(body.get("tzOffsetMin", 0))

    weight = body.get("weightKg", None)
    factor = body.get("mlPerKg", None)

    if weight is not None:
        weight = int(weight)
        if weight < 20 or weight > 300:
            raise HTTPException(status_code=400, detail="weightKg must be 20..300")
        db.set_weight(tg_id, weight)

    if factor is not None:
        factor = int(factor)
        if factor < 30 or factor > 35:
            raise HTTPException(status_code=400, detail="mlPerKg must be 30..35")
        db.set_factor(tg_id, factor)

    # пересчёт цели по формуле если есть вес
    prof = db.get_profile(tg_id)
    if prof.get("weight_kg"):
        db.recompute_goal_from_formula(tg_id)

    today_local_date = local_date_str_from_utc(datetime.utcnow(), tz_offset_min)
    db.refresh_daily_stats_for_date(tg_id, today_local_date)

    return JSONResponse({"ok": True, "state": _build_state(tg_id, tz_offset_min)})

@app.post("/api/stats")
async def api_stats(request: Request):
    body = await request.json()
    tg_id = _auth_webapp(body.get("initData", ""))
    tz_offset_min = int(body.get("tzOffsetMin", 0))
    today_local_date = local_date_str_from_utc(datetime.utcnow(), tz_offset_min)
    return JSONResponse(db.compute_stats(tg_id, today_local_date))

@app.post("/api/calendar")
async def api_calendar(request: Request):
    body = await request.json()
    tg_id = _auth_webapp(body.get("initData", ""))
    year = int(body.get("year", 0))
    month = int(body.get("month", 0))

    if year < 2000 or year > 2100 or month < 1 or month > 12:
        raise HTTPException(status_code=400, detail="year/month invalid")

    days = db.get_month_calendar(tg_id, year, month)
    return JSONResponse({"days": days})

# --- TELEGRAM WEBHOOK ---
@app.post(WEBHOOK_PATH)
async def telegram_webhook(request: Request) -> Response:
    if WEBHOOK_SECRET:
        got = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if got != WEBHOOK_SECRET:
            return Response(status_code=HTTPStatus.FORBIDDEN)

    data = await request.json()
    update = Update.de_json(data=data, bot=telegram_app.bot)
    await telegram_app.process_update(update)
    return Response(status_code=HTTPStatus.OK)
