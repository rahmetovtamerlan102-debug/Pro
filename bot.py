#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio
import os
import json
import hashlib
import string
import random
import logging
import time
import signal
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from typing import Optional, Tuple

from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import aiohttp
from aiohttp import web
import asyncpg
from asyncpg.pool import Pool
import redis.asyncio as redis

load_dotenv()

# ==================== КОНФИГ ====================
BOT_TOKEN = os.getenv("BOT_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
CRYPTOBOT_TOKEN = os.getenv("CRYPTOBOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
ADMIN_IDS = [int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]
PORT = int(os.environ.get("PORT", 10000))

if not BOT_TOKEN or not GROQ_API_KEY:
    raise ValueError("BOT_TOKEN и GROQ_API_KEY обязательны")

# ==================== ЛОГИ ====================
os.makedirs("logs", exist_ok=True)
file_handler = RotatingFileHandler("logs/bot.log", maxBytes=10*1024*1024, backupCount=5)
file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
console_handler = logging.StreamHandler()
logging.basicConfig(level=logging.INFO, handlers=[file_handler, console_handler])
logger = logging.getLogger(__name__)

# ==================== ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ ====================
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
db_pool: Pool = None
redis_client = None
http_session: aiohttp.ClientSession = None

request_queue = asyncio.Queue(maxsize=200)
global_semaphore = asyncio.Semaphore(20)
queue_workers = 20

spam_tracker = {}
REFERRAL_BONUS = 20

# ==================== 6 МОДЕЛЕЙ ====================
MODEL_LIST = [
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
    "qwen/qwen3-32b",
    "openai/gpt-oss-20b",
    "openai/gpt-oss-120b",
]

MODEL_NAMES = {
    "meta-llama/llama-4-scout-17b-16e-instruct": "Llama 4 Scout",
    "llama-3.3-70b-versatile": "Llama 3.3 70B",
    "llama-3.1-8b-instant": "Llama 3.1 8B",
    "qwen/qwen3-32b": "Qwen 3 32B",
    "openai/gpt-oss-20b": "GPT-OSS 20B",
    "openai/gpt-oss-120b": "GPT-OSS 120B",
}

TIER_LIMITS = {"free": 30, "pro": 500, "ultra": 10000}
MAX_MESSAGE_LEN = 8000

# ==================== POSTGRESQL ====================
async def init_db():
    global db_pool
    db_pool = await asyncpg.create_pool(
        DATABASE_URL,
        min_size=2,
        max_size=20,
        command_timeout=30
    )
    
    async with db_pool.acquire() as conn:
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                model TEXT DEFAULT 'meta-llama/llama-4-scout-17b-16e-instruct',
                tier TEXT DEFAULT 'free',
                is_banned BOOLEAN DEFAULT FALSE,
                ban_reason TEXT,
                referrer_id BIGINT,
                referral_code TEXT UNIQUE,
                created_at TIMESTAMP DEFAULT NOW()
            )
        ''')
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS history (
                id SERIAL PRIMARY KEY,
                user_id BIGINT,
                role TEXT,
                content TEXT,
                timestamp TIMESTAMP DEFAULT NOW()
            )
        ''')
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS usage (
                user_id BIGINT,
                date DATE,
                count INTEGER,
                PRIMARY KEY (user_id, date)
            )
        ''')
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS referrals (
                user_id BIGINT PRIMARY KEY,
                invited_count INT DEFAULT 0,
                bonus_balance INT DEFAULT 0
            )
        ''')
        await conn.execute('CREATE INDEX IF NOT EXISTS idx_history_user_id_ts ON history(user_id, timestamp DESC)')
    
    logger.info("PostgreSQL готов")

async def db_add_user(user_id: int, referrer_code: str = None):
    async with db_pool.acquire() as conn:
        await conn.execute('INSERT INTO users (user_id) VALUES ($1) ON CONFLICT DO NOTHING', user_id)
        await conn.execute('INSERT INTO referrals (user_id) VALUES ($1) ON CONFLICT DO NOTHING', user_id)
        
        if referrer_code:
            referrer = await conn.fetchrow('SELECT user_id FROM users WHERE referral_code = $1', referrer_code)
            if referrer and referrer['user_id'] != user_id:
                existing = await conn.fetchval('SELECT referrer_id FROM users WHERE user_id = $1', user_id)
                if not existing:
                    await conn.execute('UPDATE users SET referrer_id = $1 WHERE user_id = $2', referrer['user_id'], user_id)
                    await conn.execute('UPDATE referrals SET bonus_balance = bonus_balance + $1 WHERE user_id = $2', REFERRAL_BONUS, referrer['user_id'])
                    await conn.execute('UPDATE referrals SET invited_count = invited_count + 1 WHERE user_id = $1', referrer['user_id'])

async def db_get_user_model(user_id: int) -> str:
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow('SELECT model FROM users WHERE user_id = $1', user_id)
        return row[0] if row else MODEL_LIST[0]

async def db_update_user_model(user_id: int, model: str):
    async with db_pool.acquire() as conn:
        await conn.execute('UPDATE users SET model = $1 WHERE user_id = $2', model, user_id)

async def db_add_history(user_id: int, role: str, content: str):
    async with db_pool.acquire() as conn:
        await conn.execute('INSERT INTO history (user_id, role, content) VALUES ($1, $2, $3)', user_id, role, content)
        await conn.execute('DELETE FROM history WHERE id IN (SELECT id FROM history WHERE user_id = $1 ORDER BY timestamp DESC OFFSET 100)', user_id)

async def db_get_history(user_id: int, limit: int = 20):
    if redis_client:
        redis_key = f"history:{user_id}"
        redis_data = await redis_client.lrange(redis_key, -limit, -1)
        if redis_data:
            history = [json.loads(x) for x in redis_data]
            history.reverse()
            return history
    
    async with db_pool.acquire() as conn:
        rows = await conn.fetch('SELECT role, content FROM history WHERE user_id = $1 ORDER BY timestamp DESC LIMIT $2', user_id, limit)
        rows.reverse()
        return [{"role": r[0], "content": r[1]} for r in rows]

async def db_save_history_to_redis(user_id: int, role: str, content: str):
    if redis_client:
        redis_key = f"history:{user_id}"
        await redis_client.rpush(redis_key, json.dumps({"role": role, "content": content[:500]}))
        await redis_client.ltrim(redis_key, -50, -1)
        await redis_client.expire(redis_key, 86400)

async def db_add_history_full(user_id: int, role: str, content: str):
    await db_add_history(user_id, role, content)
    await db_save_history_to_redis(user_id, role, content)

async def db_get_stats(user_id: int):
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow('SELECT COUNT(*) FROM history WHERE user_id = $1', user_id)
        return row[0] if row else 0

async def get_user_tier(user_id: int) -> str:
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow('SELECT tier FROM users WHERE user_id = $1', user_id)
        return row[0] if row else "free"

async def get_usage_stats(user_id: int):
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow('SELECT tier FROM users WHERE user_id = $1', user_id)
        tier = row[0] if row else "free"
        limit = TIER_LIMITS.get(tier, 30)
        today = datetime.now().strftime("%Y-%m-%d")
        row = await conn.fetchrow('SELECT count FROM usage WHERE user_id = $1 AND date = $2', user_id, today)
        used = row[0] if row else 0
        return tier, used, limit - used, limit

async def check_usage(user_id: int):
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow('SELECT tier FROM users WHERE user_id = $1', user_id)
        tier = row[0] if row else "free"
        limit = TIER_LIMITS.get(tier, 30)
        today = datetime.now().strftime("%Y-%m-%d")
        row = await conn.fetchrow('SELECT count FROM usage WHERE user_id = $1 AND date = $2', user_id, today)
        current = row[0] if row else 0
        
        if current >= limit:
            return False, 0
        
        if row:
            await conn.execute('UPDATE usage SET count = count + 1 WHERE user_id = $1 AND date = $2', user_id, today)
        else:
            await conn.execute('INSERT INTO usage (user_id, date, count) VALUES ($1, $2, 1)', user_id, today)
        
        return True, limit - (current + 1)

async def is_banned(user_id: int) -> Tuple[bool, str]:
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow('SELECT is_banned, ban_reason FROM users WHERE user_id = $1', user_id)
        if row and row['is_banned']:
            return True, row['ban_reason'] or "Нарушение правил"
        return False, ""

async def ban_user(user_id: int, reason: str = "Спам"):
    async with db_pool.acquire() as conn:
        await conn.execute('UPDATE users SET is_banned = TRUE, ban_reason = $1 WHERE user_id = $2', reason, user_id)
    logger.info(f"Пользователь {user_id} забанен: {reason}")

async def unban_user(user_id: int):
    async with db_pool.acquire() as conn:
        await conn.execute('UPDATE users SET is_banned = FALSE, ban_reason = NULL WHERE user_id = $1', user_id)
    logger.info(f"Пользователь {user_id} разбанен")

async def increment_spam_score(user_id: int) -> Optional[bool]:
    now = time.time()
    if user_id not in spam_tracker:
        spam_tracker[user_id] = {"requests": [], "warned": False}
    
    spam_tracker[user_id]["requests"] = [t for t in spam_tracker[user_id]["requests"] if now - t < 60]
    spam_tracker[user_id]["requests"].append(now)
    
    count = len(spam_tracker[user_id]["requests"])
    
    if count >= 50:
        await ban_user(user_id, "Автоматический бан: 50 запросов в минуту")
        return True
    if count >= 30 and not spam_tracker[user_id]["warned"]:
        spam_tracker[user_id]["warned"] = True
        return "warn"
    return False

async def cleanup_old_history():
    async with db_pool.acquire() as conn:
        await conn.execute('DELETE FROM history WHERE timestamp < NOW() - INTERVAL \'30 days\'')
        logger.info("Автоочистка БД выполнена")

# ==================== REDIS ====================
async def init_redis():
    global redis_client
    try:
        redis_client = await redis.from_url(REDIS_URL, decode_responses=True)
        await redis_client.ping()
        logger.info("Redis готов")
    except Exception as e:
        logger.warning(f"Redis недоступен: {e}")
        redis_client = None

async def rate_limit(user_id: int) -> bool:
    if not redis_client:
        return True
    key = f"rl:{user_id}"
    val = await redis_client.incr(key)
    if val == 1:
        await redis_client.expire(key, 1)
    return val <= 2

async def deduplicate_message(user_id: int, text: str) -> bool:
    if not redis_client or len(text) < 10:
        return False
    key = f"last_msg:{user_id}"
    last_msg = await redis_client.get(key)
    if last_msg and last_msg == text:
        return True
    await redis_client.setex(key, 5, text)
    return False

async def cache_get(key: str):
    if redis_client:
        return await redis_client.get(key)
    return None

async def cache_set(key: str, value: str, ttl: int = 86400):
    if redis_client:
        await redis_client.setex(key, ttl, value)

# ==================== GROQ API ====================
async def ask_groq(model: str, messages: list) -> Tuple[str, bool]:
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    system = {"role": "system", "content": "Ты русскоязычный ассистент. Отвечай ТОЛЬКО на русском языке. Никогда не используй английский."}
    
    temperature = 0.5
    max_tokens = 1500
    
    payload = {"model": model, "messages": [system] + messages, "temperature": temperature, "max_tokens": max_tokens}
    
    for attempt in range(2):
        try:
            async with http_session.post(url, json=payload, headers=headers, timeout=30) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data["choices"][0]["message"]["content"], True
                elif resp.status == 429:
                    await asyncio.sleep(1)
                    continue
        except:
            continue
    return "Сервис временно недоступен", False

async def ask_with_fallback(model: str, messages: list) -> Tuple[str, str]:
    response, success = await ask_groq(model, messages)
    if success:
        return response, model
    
    for fallback in MODEL_LIST:
        if fallback == model:
            continue
        response, success = await ask_groq(fallback, messages)
        if success:
            return f"[Переключено на {MODEL_NAMES[fallback]}]\n\n{response}", fallback
    
    return "Сервис недоступен. Попробуйте позже.", "error"

# ==================== CRYPTOBOT ====================
async def create_invoice(user_id: int, tier: str):
    if not CRYPTOBOT_TOKEN:
        return None
    amount = 3.0 if tier == "pro" else 10.0
    url = "https://pay.crypt.bot/api/createInvoice"
    headers = {"Crypto-Pay-API-Token": CRYPTOBOT_TOKEN, "Content-Type": "application/json"}
    payload = {"asset": "USDT", "amount": str(amount), "description": f"Повышение до {tier}", "payload": str(user_id), "expires_in": 3600}
    
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload, headers=headers) as resp:
            if resp.status == 200:
                data = await resp.json()
                if data.get("ok"):
                    return data["result"]["bot_invoice_url"]
    return None

async def crypto_webhook(request):
    raw_body = await request.text()
    try:
        data = json.loads(raw_body)
        if data.get("update_type") == "invoice_paid":
            payload = data.get("payload", {})
            user_id = int(payload.get("payload", 0))
            amount = payload.get("paid_amount")
            if user_id:
                tier = "pro" if amount == "3.00" else "ultra" if amount == "10.00" else None
                if tier:
                    async with db_pool.acquire() as conn:
                        await conn.execute('UPDATE users SET tier = $1 WHERE user_id = $2', tier, user_id)
                    await bot.send_message(user_id, f"Тариф повышен до {tier.upper()}!")
    except Exception as e:
        logger.error(f"Crypto webhook error: {e}")
    return web.Response(text="OK")

# ==================== ОЧЕРЕДЬ ====================
async def worker(worker_id: int):
    while True:
        future, user_id, model, messages = await request_queue.get()
        async with global_semaphore:
            try:
                result, used_model = await ask_with_fallback(model, messages)
                future.set_result((result, used_model))
            except Exception as e:
                future.set_exception(e)
        request_queue.task_done()

async def queue_request(user_id: int, model: str, messages: list):
    loop = asyncio.get_running_loop()
    future = loop.create_future()
    await request_queue.put((future, user_id, model, messages))
    return await future

# ==================== HEALTH CHECK ====================
async def health_check(request):
    status = {"status": "ok", "timestamp": datetime.now().isoformat()}
    try:
        async with db_pool.acquire() as conn:
            await conn.fetchval('SELECT 1')
        status["postgresql"] = "ok"
    except:
        status["postgresql"] = "error"
        status["status"] = "degraded"
    if redis_client:
        try:
            await redis_client.ping()
            status["redis"] = "ok"
        except:
            status["redis"] = "error"
    status["queue_size"] = request_queue.qsize()
    return web.Response(text=json.dumps(status, ensure_ascii=False), content_type="application/json")

# ==================== АДМИН-КОМАНДЫ ====================
@dp.message(Command("admin"))
async def cmd_admin(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("Доступ запрещён")
        return
    await message.answer("Админ-команды:\n/ban\n/unban\n/givepro\n/broadcast\n/stats")

@dp.message(Command("ban"))
async def cmd_ban(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    args = message.text.split()
    if len(args) < 2:
        await message.answer("Использование: /ban <user_id> [причина]")
        return
    target = int(args[1])
    reason = " ".join(args[2:]) if len(args) > 2 else "Нарушение"
    await ban_user(target, reason)
    await message.answer(f"Пользователь {target} забанен")

@dp.message(Command("unban"))
async def cmd_unban(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    args = message.text.split()
    if len(args) < 2:
        await message.answer("Использование: /unban <user_id>")
        return
    target = int(args[1])
    await unban_user(target)
    await message.answer(f"Пользователь {target} разбанен")

@dp.message(Command("givepro"))
async def cmd_givepro(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    args = message.text.split()
    if len(args) < 2:
        await message.answer("Использование: /givepro <user_id> [pro/ultra]")
        return
    target = int(args[1])
    tier = args[2] if len(args) > 2 else "pro"
    async with db_pool.acquire() as conn:
        await conn.execute('UPDATE users SET tier = $1 WHERE user_id = $2', tier, target)
    await message.answer(f"Пользователю {target} выдан тариф {tier.upper()}")

@dp.message(Command("broadcast"))
async def cmd_broadcast(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    text = message.text.replace("/broadcast", "").strip()
    if not text:
        await message.answer("Использование: /broadcast <текст>")
        return
    async with db_pool.acquire() as conn:
        rows = await conn.fetch('SELECT user_id FROM users')
    sent = 0
    for row in rows:
        try:
            await bot.send_message(row['user_id'], f"Рассылка:\n\n{text}")
            sent += 1
            await asyncio.sleep(0.05)
        except:
            pass
    await message.answer(f"Отправлено {sent} пользователям")

@dp.message(Command("stats"))
async def cmd_stats(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    async with db_pool.acquire() as conn:
        users = await conn.fetchval('SELECT COUNT(*) FROM users')
        today = datetime.now().strftime("%Y-%m-%d")
        reqs = await conn.fetchval('SELECT SUM(count) FROM usage WHERE date = $1', today) or 0
        banned = await conn.fetchval('SELECT COUNT(*) FROM users WHERE is_banned = TRUE') or 0
    await message.answer(f"Статистика\n\nПользователей: {users}\nЗабанено: {banned}\nЗапросов сегодня: {reqs}\nВ очереди: {request_queue.qsize()}")

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    args = message.text.split()
    ref_code = args[1] if len(args) > 1 else None
    await db_add_user(user_id, ref_code)
    
    await message.answer(
        "🤖 LLM Hub\n\n"
        "Доступ к AI моделям\n\n"
        "Доступные модели:\n"
        "• Llama 4 Scout\n"
        "• Llama 3.3 70B\n"
        "• Llama 3.1 8B\n"
        "• Qwen 3 32B\n"
        "• GPT-OSS 20B\n"
        "• GPT-OSS 120B\n\n"
        "Просто напишите сообщение, и я отвечу",
        reply_markup=main_kb()
    )

# ==================== КЛАВИАТУРЫ ====================
def main_kb():
    buttons = [
        [InlineKeyboardButton(text="Личный кабинет", callback_data="profile")],
        [InlineKeyboardButton(text="Сменить модель", callback_data="show_models")],
    ]
    if CRYPTOBOT_TOKEN:
        buttons.append([InlineKeyboardButton(text="Купить премиум", callback_data="premium")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def models_kb():
    buttons = []
    row = []
    for model_id in MODEL_LIST:
        name = MODEL_NAMES[model_id]
        row.append(InlineKeyboardButton(text=name, callback_data=f"model_{model_id}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton(text="Назад", callback_data="back")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def premium_kb():
    buttons = []
    if CRYPTOBOT_TOKEN:
        buttons.append([InlineKeyboardButton(text="PRO (500 запросов/день)", callback_data="buy_pro")])
        buttons.append([InlineKeyboardButton(text="ULTRA (10000 запросов/день)", callback_data="buy_ultra")])
    buttons.append([InlineKeyboardButton(text="Назад", callback_data="back")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def profile_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Купить премиум", callback_data="premium")],
        [InlineKeyboardButton(text="Реферальная ссылка", callback_data="show_ref")],
        [InlineKeyboardButton(text="Назад", callback_data="back")]
    ])

# ==================== CALLBACK ====================
@dp.callback_query()
async def handle_callback(call: types.CallbackQuery):
    user_id = call.from_user.id
    data = call.data
    
    if data.startswith("model_"):
        model = data.replace("model_", "")
        await db_update_user_model(user_id, model)
        await call.message.edit_text(f"Модель изменена на: {MODEL_NAMES[model]}", reply_markup=main_kb())
        await call.answer()
        return
    
    if data == "show_models":
        await call.message.edit_text("Выберите модель:", reply_markup=models_kb())
        await call.answer()
        return
    
    if data == "back":
        await call.message.edit_text("Главное меню", reply_markup=main_kb())
        await call.answer()
        return
    
    if data == "profile":
        tier, used, remaining, limit = await get_usage_stats(user_id)
        hist = await db_get_stats(user_id)
        model = await db_get_user_model(user_id)
        banned, reason = await is_banned(user_id)
        
        async with db_pool.acquire() as conn:
            invited = await conn.fetchval('SELECT COUNT(*) FROM users WHERE referrer_id = $1', user_id) or 0
            bonus = await conn.fetchval('SELECT bonus_balance FROM referrals WHERE user_id = $1', user_id) or 0
        
        text = (
            f"Личный кабинет\n\n"
            f"Тариф: {tier.upper()}\n"
            f"Запросов сегодня: {used}/{limit}\n"
            f"Осталось: {remaining}\n"
            f"Всего сообщений: {hist}\n"
            f"Модель: {MODEL_NAMES[model]}\n\n"
            f"Рефералы: {invited} друзей | {bonus} бонусов"
        )
        if banned:
            text += f"\n\nЗабанен: {reason}"
        
        await call.message.edit_text(text, reply_markup=profile_kb())
        await call.answer()
        return
    
    if data == "show_ref":
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow('SELECT referral_code FROM users WHERE user_id = $1', user_id)
            code = row['referral_code'] if row else None
            if not code:
                code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
                await conn.execute('UPDATE users SET referral_code = $1 WHERE user_id = $2', code, user_id)
            
            invited = await conn.fetchval('SELECT COUNT(*) FROM users WHERE referrer_id = $1', user_id) or 0
            bonus = await conn.fetchval('SELECT bonus_balance FROM referrals WHERE user_id = $1', user_id) or 0
        
        await call.message.edit_text(
            f"Реферальная программа\n\n"
            f"Приглашено друзей: {invited}\n"
            f"Бонусов на счету: {bonus}\n"
            f"За каждого друга: +{REFERRAL_BONUS} запросов\n\n"
            f"Ваша ссылка:\n"
            f"<code>https://t.me/{bot.username}?start={code}</code>\n\n"
            f"Отправьте её другу, и вы оба получите бонусы!",
            reply_markup=profile_kb(),
            parse_mode="HTML",
            disable_web_page_preview=True
        )
        await call.answer()
        return
    
    if data == "premium" and CRYPTOBOT_TOKEN:
        await call.message.edit_text("Выберите тариф:", reply_markup=premium_kb())
        await call.answer()
        return
    
    if data == "buy_pro" and CRYPTOBOT_TOKEN:
        url = await create_invoice(user_id, "pro")
        if url:
            await call.message.edit_text(f"Оплата PRO:\n{url}", reply_markup=main_kb(), disable_web_page_preview=True)
        else:
            await call.message.edit_text("Ошибка при создании счёта", reply_markup=main_kb())
        await call.answer()
        return
    
    if data == "buy_ultra" and CRYPTOBOT_TOKEN:
        url = await create_invoice(user_id, "ultra")
        if url:
            await call.message.edit_text(f"Оплата ULTRA:\n{url}", reply_markup=main_kb(), disable_web_page_preview=True)
        else:
            await call.message.edit_text("Ошибка при создании счёта", reply_markup=main_kb())
        await call.answer()
        return

# ==================== ОБРАБОТЧИК СООБЩЕНИЙ ====================
@dp.message()
async def handle_message(msg: types.Message):
    user_id = msg.from_user.id
    
    if not await rate_limit(user_id):
        await msg.answer("Слишком часто! Подождите 1 секунду.")
        return
    
    banned, reason = await is_banned(user_id)
    if banned:
        await msg.answer(f"Вы забанены.\nПричина: {reason}")
        return
    
    spam_result = await increment_spam_score(user_id)
    if spam_result is True:
        await msg.answer("Вы забанены за спам (50 запросов в минуту).")
        return
    elif spam_result == "warn":
        await msg.answer("Предупреждение: слишком много запросов!")
    
    allowed, remaining = await check_usage(user_id)
    if not allowed:
        tier = await get_user_tier(user_id)
        await msg.answer(f"Лимит {tier.upper()} исчерпан")
        return
    
    user_text = msg.text or msg.caption
    if not user_text:
        return
    
    if await deduplicate_message(user_id, user_text):
        return
    
    if len(user_text) > MAX_MESSAGE_LEN:
        user_text = user_text[:MAX_MESSAGE_LEN]
    
    history = await db_get_history(user_id, 15)
    await db_add_history_full(user_id, "user", user_text)
    history.append({"role": "user", "content": user_text})
    
    model = await db_get_user_model(user_id)
    cache_key = hashlib.md5(f"{model}:{user_text[:100]}".encode()).hexdigest()
    cached = await cache_get(cache_key)
    if cached:
        await msg.answer(cached[:4000], reply_markup=main_kb())
        return
    
    status = await msg.answer("Думаю...")
    
    try:
        result = await asyncio.wait_for(queue_request(user_id, model, history), timeout=40)
        reply, used_model = result
    except asyncio.TimeoutError:
        reply = "Превышено время ожидания"
        used_model = "timeout"
    except Exception as e:
        logger.error(f"Queue error: {e}")
        reply = "Ошибка обработки запроса"
        used_model = "error"
    
    if used_model not in ["timeout", "error"] and len(user_text) < 100 and len(reply) < 500:
        await cache_set(cache_key, reply[:1000])
    
    await db_add_history_full(user_id, "assistant", reply[:2000])
    
    await status.delete()
    await msg.answer(reply[:4000], reply_markup=main_kb())
    
    if remaining <= 5:
        await msg.answer(f"Осталось запросов сегодня: {remaining}")

# ==================== ЗАПУСК ====================
async def schedule_cleanup():
    while True:
        now = datetime.now()
        next_run = now.replace(hour=3, minute=0, second=0, microsecond=0)
        if now >= next_run:
            next_run += timedelta(days=1)
        await asyncio.sleep((next_run - now).total_seconds())
        await cleanup_old_history()

async def on_startup():
    global http_session
    http_session = aiohttp.ClientSession()
    await init_db()
    await init_redis()
    
    for i in range(queue_workers):
        asyncio.create_task(worker(i))
    
    asyncio.create_task(schedule_cleanup())
    
    app = web.Application()
    app.router.add_get("/health", health_check)
    app.router.add_post("/crypto", crypto_webhook)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    
    logger.info(f"Бот запущен | Модели: 6 | Workers: {queue_workers} | Порт: {PORT}")
    logger.info(f"Админы: {ADMIN_IDS}")

async def on_shutdown():
    global http_session, db_pool, redis_client
    if http_session:
        await http_session.close()
    if db_pool:
        await db_pool.close()
    if redis_client:
        await redis_client.close()
    logger.info("Бот остановлен")

async def main():
    loop = asyncio.get_running_loop()
    for sig in [signal.SIGTERM, signal.SIGINT]:
        loop.add_signal_handler(sig, lambda: asyncio.create_task(on_shutdown()))
    await on_startup()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
