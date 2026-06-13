#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio
import os
import re
import json
import sqlite3
import hashlib
from datetime import datetime
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import aiohttp
from aiohttp import web

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
PORT = int(os.environ.get("PORT", 10000))
CRYPTOBOT_TOKEN = os.getenv("CRYPTOBOT_TOKEN")

if not BOT_TOKEN or not GROQ_API_KEY or not CRYPTOBOT_TOKEN:
    raise ValueError("BOT_TOKEN, GROQ_API_KEY и CRYPTOBOT_TOKEN обязательны")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ==================== БАЗА ДАННЫХ ====================
db_lock = asyncio.Lock()

def init_db():
    with sqlite3.connect("bot.db", timeout=30) as conn:
        c = conn.cursor()
        c.execute("PRAGMA journal_mode=WAL;")
        c.execute("PRAGMA synchronous=NORMAL;")
        c.execute('''CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            model TEXT DEFAULT 'meta-llama/llama-4-scout-17b-16e-instruct',
            tier TEXT DEFAULT 'free',
            created_at TIMESTAMP
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS history (
            user_id INTEGER,
            role TEXT,
            content TEXT,
            timestamp TIMESTAMP
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS cache (
            key TEXT PRIMARY KEY,
            response TEXT,
            created_at TIMESTAMP
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS usage (
            user_id INTEGER,
            date TEXT,
            count INTEGER,
            PRIMARY KEY (user_id, date)
        )''')
        conn.commit()

init_db()

# ==================== 7 МОДЕЛЕЙ ====================
MODEL_LIST = [
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
    "qwen/qwen3-32b",
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
    "openai/gpt-oss-safeguard-20b"
]

MODEL_DISPLAY_NAMES = {
    "meta-llama/llama-4-scout-17b-16e-instruct": "Llama 4 Scout",
    "llama-3.3-70b-versatile": "Llama 3.3 70B",
    "llama-3.1-8b-instant": "Llama 3.1 8B",
    "qwen/qwen3-32b": "Qwen 3 32B",
    "openai/gpt-oss-120b": "GPT-OSS 120B",
    "openai/gpt-oss-20b": "GPT-OSS 20B",
    "openai/gpt-oss-safeguard-20b": "GPT-OSS Safeguard"
}

def get_model_display_name(model_id):
    return MODEL_DISPLAY_NAMES.get(model_id, model_id.split("/")[-1][:20])

async def get_models():
    return MODEL_LIST

# ==================== ЛИМИТЫ ПОЛЬЗОВАТЕЛЕЙ ====================
TIER_LIMITS = {"free": 20, "pro": 300, "ultra": 999999}

async def check_and_increment_usage(user_id):
    async with db_lock:
        with sqlite3.connect("bot.db", timeout=30) as conn:
            c = conn.cursor()
            c.execute("SELECT tier FROM users WHERE user_id = ?", (user_id,))
            row = c.fetchone()
            tier = row[0] if row else "free"
            limit = TIER_LIMITS.get(tier, 20)
            today = datetime.now().strftime("%Y-%m-%d")
            c.execute("SELECT count FROM usage WHERE user_id = ? AND date = ?", (user_id, today))
            row = c.fetchone()
            current = row[0] if row else 0
            if current >= limit:
                return False, 0
            if row:
                c.execute("UPDATE usage SET count = count + 1 WHERE user_id = ? AND date = ?", (user_id, today))
            else:
                c.execute("INSERT INTO usage (user_id, date, count) VALUES (?, ?, 1)", (user_id, today))
            conn.commit()
            remaining = limit - (current + 1)
            return True, remaining

async def get_user_tier(user_id):
    async with db_lock:
        with sqlite3.connect("bot.db", timeout=30) as conn:
            c = conn.cursor()
            c.execute("SELECT tier FROM users WHERE user_id = ?", (user_id,))
            row = c.fetchone()
            return row[0] if row else "free"

async def set_user_tier(user_id, tier):
    if tier not in TIER_LIMITS:
        return False
    async with db_lock:
        with sqlite3.connect("bot.db", timeout=30) as conn:
            c = conn.cursor()
            c.execute("UPDATE users SET tier = ? WHERE user_id = ?", (tier, user_id))
            conn.commit()
    return True

# ==================== ФУНКЦИИ ДЛЯ CRYPTOBOT ====================
async def create_crypto_invoice(user_id: int, tier: str):
    amount = 3.0 if tier == "pro" else 10.0
    url = "https://pay.crypt.bot/api/createInvoice"
    headers = {
        "Crypto-Pay-API-Token": CRYPTOBOT_TOKEN,
        "Content-Type": "application/json"
    }
    payload = {
        "asset": "USDT",
        "amount": str(amount),
        "description": f"Upgrade to {tier.upper()}",
        "payload": str(user_id),
        "expires_in": 3600
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload, headers=headers) as resp:
            if resp.status == 200:
                data = await resp.json()
                if data.get("ok"):
                    return data["result"]["bot_invoice_url"]
    return None

# ==================== ВЕБХУК ДЛЯ ОБРАБОТКИ ПЛАТЕЖЕЙ ====================
async def crypto_webhook(request):
    raw_body = await request.text()
    try:
        data = json.loads(raw_body)
    except:
        return web.Response(text="Bad JSON", status=400)
    if data.get("update_type") == "invoice_paid":
        payload_data = data.get("payload", {})
        user_id_str = payload_data.get("payload")
        if user_id_str and user_id_str.isdigit():
            user_id = int(user_id_str)
            paid_amount = payload_data.get("paid_amount")
            if paid_amount == "3.00":
                tier = "pro"
            elif paid_amount == "10.00":
                tier = "ultra"
            else:
                tier = None
            if tier:
                await set_user_tier(user_id, tier)
                try:
                    await bot.send_message(
                        user_id,
                        f"Тариф повышен до {tier.upper()}!\nЛимит: {TIER_LIMITS[tier]} запросов в день.\nСпасибо за поддержку!"
                    )
                except Exception as e:
                    print(f"Ошибка уведомления: {e}")
    return web.Response(text="OK")

# ==================== КЛАВИАТУРЫ (без эмодзи, без кнопки очистки) ====================
async def get_models_keyboard():
    models = await get_models()
    buttons = []
    row = []
    for model_id in models:
        name = get_model_display_name(model_id)
        row.append(InlineKeyboardButton(text=name, callback_data=f"model_{model_id}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton(text="Назад", callback_data="back_to_main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def main_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Сменить модель", callback_data="show_models")],
        [InlineKeyboardButton(text="Статистика", callback_data="stats")],
        [InlineKeyboardButton(text="Купить премиум", callback_data="premium")]
    ])

def premium_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="PRO (300 запросов/день)", callback_data="buy_pro")],
        [InlineKeyboardButton(text="ULTRA (безлимит)", callback_data="buy_ultra")],
        [InlineKeyboardButton(text="Назад", callback_data="back_to_main")]
    ])

# ==================== РАБОТА С БД (ОСТАЛЬНЫЕ ФУНКЦИИ) ====================
def db_retry(func):
    async def wrapper(*args, **kwargs):
        for attempt in range(3):
            try:
                return await func(*args, **kwargs)
            except sqlite3.OperationalError as e:
                if "locked" in str(e) and attempt < 2:
                    await asyncio.sleep(0.1 * (attempt + 1))
                else:
                    raise
    return wrapper

@db_retry
async def db_get_user_model(user_id):
    async with db_lock:
        with sqlite3.connect("bot.db", timeout=30) as conn:
            c = conn.cursor()
            c.execute("SELECT model FROM users WHERE user_id = ?", (user_id,))
            row = c.fetchone()
            return row[0] if row else MODEL_LIST[0]

@db_retry
async def db_update_user_model(user_id, model_id):
    async with db_lock:
        with sqlite3.connect("bot.db", timeout=30) as conn:
            c = conn.cursor()
            c.execute("UPDATE users SET model = ? WHERE user_id = ?", (model_id, user_id))
            conn.commit()

@db_retry
async def db_add_user(user_id):
    async with db_lock:
        with sqlite3.connect("bot.db", timeout=30) as conn:
            c = conn.cursor()
            c.execute("INSERT OR IGNORE INTO users (user_id, model, tier, created_at) VALUES (?, ?, ?, ?)",
                      (user_id, MODEL_LIST[0], "free", datetime.now()))
            conn.commit()

@db_retry
async def db_add_history(user_id, role, content):
    async with db_lock:
        with sqlite3.connect("bot.db", timeout=30) as conn:
            c = conn.cursor()
            c.execute("INSERT INTO history (user_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
                      (user_id, role, content, datetime.now()))
            conn.commit()

@db_retry
async def db_get_history(user_id, limit=10):
    async with db_lock:
        with sqlite3.connect("bot.db", timeout=30) as conn:
            c = conn.cursor()
            c.execute("SELECT role, content FROM history WHERE user_id = ? ORDER BY timestamp ASC LIMIT ?", (user_id, limit))
            rows = c.fetchall()
            return [{"role": r[0], "content": r[1]} for r in rows]

@db_retry
async def db_cache_get(key):
    async with db_lock:
        with sqlite3.connect("bot.db", timeout=30) as conn:
            c = conn.cursor()
            c.execute("SELECT response FROM cache WHERE key = ?", (key,))
            row = c.fetchone()
            return row[0] if row else None

@db_retry
async def db_cache_set(key, response):
    async with db_lock:
        with sqlite3.connect("bot.db", timeout=30) as conn:
            c = conn.cursor()
            c.execute("INSERT OR REPLACE INTO cache (key, response, created_at) VALUES (?, ?, ?)",
                      (key, response[:1500], datetime.now()))
            conn.commit()

@db_retry
async def db_get_stats(user_id):
    async with db_lock:
        with sqlite3.connect("bot.db", timeout=30) as conn:
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM history WHERE user_id = ?", (user_id,))
            hist_count = c.fetchone()[0]
            c.execute("SELECT COUNT(*) FROM cache")
            cache_count = c.fetchone()[0]
            return hist_count, cache_count

# ==================== API ЗАПРОС К GROQ (С УСИЛЕННЫМ ПРОМПТОМ ДЛЯ QWEN) ====================
session = None
request_queue = asyncio.Queue(maxsize=50)
queue_workers = 2
user_semaphores = {}

async def worker(worker_id):
    while True:
        future, model, messages, is_code_request = await request_queue.get()
        try:
            result = await ask_groq_raw(model, messages, is_code_request)
            future.set_result(result)
        except Exception as e:
            future.set_exception(e)
        finally:
            request_queue.task_done()

async def ask_groq_with_queue(model, messages, is_code_request=False):
    loop = asyncio.get_running_loop()
    future = loop.create_future()
    await request_queue.put((future, model, messages, is_code_request))
    return await future

async def ask_groq_raw(model, messages, is_code_request=False):
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }

    is_qwen = (model == "qwen/qwen3-32b")

    if is_code_request:
        system_prompt = {
            "role": "system",
            "content": (
                "Ты профессиональный разработчик. Сначала кратко объясни, что будет делать код, "
                "а потом напиши готовый рабочий код. Отвечай строго на русском языке. "
                "Никогда не используй английский, даже если вопрос на английском. "
                "Код пиши на английском (синтаксис), но пояснения — только на русском. "
                "Не используй эмодзи и смайлики."
            )
        }
        temperature = 0.5
    else:
        if is_qwen:
            system_prompt = {
                "role": "system",
                "content": (
                    "Ты русскоязычный ассистент. Твоя задача — отвечать на вопросы пользователя ИСКЛЮЧИТЕЛЬНО на русском языке. "
                    "Ты НИКОГДА не используешь английский, китайский или другие языки, даже если вопрос задан на них. "
                    "Ты НИКОГДА не переходишь на английский. Если пользователь пишет на английском, ты всё равно отвечаешь на русском. "
                    "Твои ответы должны быть краткими, полезными и точными. Не используй эмодзи и смайлики. "
                    "Всегда проверяй, что твой ответ не содержит ни одного слова не на русском языке."
                )
            }
            temperature = 0.3
        else:
            system_prompt = {
                "role": "system",
                "content": (
                    "Ты русскоязычный ассистент. Отвечай ТОЛЬКО на русском языке. "
                    "НЕ ИСПОЛЬЗУЙ АНГЛИЙСКИЙ НИ ПРИ КАКИХ ОБСТОЯТЕЛЬСТВАХ. "
                    "Даже если вопрос задан на английском или китайском, ты отвечаешь строго на русском. "
                    "Будь кратким, полезным и точным. Не используй эмодзи и смайлики."
                )
            }
            temperature = 0.5

    api_messages = [system_prompt] + messages
    payload = {
        "model": model,
        "messages": api_messages,
        "temperature": temperature,
        "max_tokens": 2000
    }

    global session
    if session is None or session.closed:
        session = aiohttp.ClientSession()

    for attempt in range(3):
        try:
            async with session.post(url, json=payload, headers=headers, timeout=30) as resp:
                if resp.status == 429:
                    await asyncio.sleep(2 ** attempt)
                    continue
                if resp.status == 401:
                    return "Ошибка: недействительный API-ключ Groq"
                if resp.status != 200:
                    return f"Ошибка API: {resp.status}"
                data = await resp.json()
                if "error" in data:
                    return f"Ошибка: {data['error'].get('message', 'Неизвестная ошибка')}"
                return data["choices"][0]["message"]["content"]
        except asyncio.TimeoutError:
            await asyncio.sleep(2 ** attempt)
            continue
        except Exception:
            await asyncio.sleep(2 ** attempt)
            continue
    return "Слишком много запросов, попробуйте позже."

async def ask_ai(model, messages, is_code_request=False):
    return await ask_groq_with_queue(model, messages, is_code_request)

# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====================
def detect_language(text):
    if 'python' in text.lower() or 'питон' in text.lower():
        return 'python'
    if 'javascript' in text.lower() or 'js' in text.lower():
        return 'javascript'
    return 'python'

def format_code_response(text, lang):
    return text

def split_long_message(text, max_len=4000):
    if len(text) <= max_len:
        return [text]
    parts = []
    lines = text.split('\n')
    current = ""
    for line in lines:
        if len(current) + len(line) + 1 > max_len:
            parts.append(current)
            current = line + '\n'
        else:
            current += line + '\n'
    if current:
        parts.append(current)
    return parts

async def send_long_answer(message, text, keyboard):
    if len(text) > 800:
        msg = await message.answer("...")
        for i in range(0, len(text), 20):
            chunk = text[:i+20]
            try:
                await msg.edit_text(chunk, reply_markup=keyboard if i+20 >= len(text) else None)
            except:
                pass
            await asyncio.sleep(0.03)
        return msg
    else:
        return await message.answer(text, reply_markup=keyboard)

# ==================== ХЕНДЛЕРЫ ====================
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    await db_add_user(user_id)
    await message.answer(
        f"LLM Hub\n\nБыстрый доступ к AI моделям\nВыберите модель ниже",
        reply_markup=main_keyboard()
    )

@dp.message(Command("tier"))
async def cmd_tier(message: types.Message):
    user_id = message.from_user.id
    tier = await get_user_tier(user_id)
    limit = TIER_LIMITS.get(tier, 20)
    await message.answer(f"Ваш тариф: {tier.upper()}\nЛимит: {limit} запросов/день")

@dp.callback_query()
async def handle_callback(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    data = callback.data

    if data.startswith("model_"):
        model_id = data.replace("model_", "")
        if model_id in MODEL_DISPLAY_NAMES:
            await db_update_user_model(user_id, model_id)
            name = get_model_display_name(model_id)
            await callback.message.edit_text(f"Модель изменена: {name}", reply_markup=main_keyboard())
        await callback.answer()
        return

    if data == "show_models":
        keyboard = await get_models_keyboard()
        await callback.message.edit_text("Выберите модель:", reply_markup=keyboard)
        await callback.answer()
        return

    if data == "back_to_main":
        current = await db_get_user_model(user_id)
        current_name = get_model_display_name(current)
        tier = await get_user_tier(user_id)
        await callback.message.edit_text(
            f"Главное меню\nТекущая модель: {current_name}\nТариф: {tier.upper()}",
            reply_markup=main_keyboard()
        )
        await callback.answer()
        return

    if data == "stats":
        hist, cache = await db_get_stats(user_id)
        await callback.message.edit_text(f"Статистика\n\nСообщений: {hist}\nКэш: {cache} записей", reply_markup=main_keyboard())
        await callback.answer()
        return

    if data == "premium":
        await callback.message.edit_text("Выберите тариф:", reply_markup=premium_keyboard())
        await callback.answer()
        return

    if data == "buy_pro":
        invoice_url = await create_crypto_invoice(user_id, "pro")
        if invoice_url:
            await callback.message.edit_text(
                f"Для оплаты тарифа PRO (300 запросов/день) перейдите по ссылке:\n{invoice_url}\n\n"
                f"После оплаты подписка активируется автоматически.",
                reply_markup=main_keyboard(),
                disable_web_page_preview=True
            )
        else:
            await callback.message.edit_text("Ошибка при создании счёта. Попробуйте позже.", reply_markup=main_keyboard())
        await callback.answer()
        return

    if data == "buy_ultra":
        invoice_url = await create_crypto_invoice(user_id, "ultra")
        if invoice_url:
            await callback.message.edit_text(
                f"Для оплаты тарифа ULTRA (безлимит) перейдите по ссылке:\n{invoice_url}\n\n"
                f"После оплаты подписка активируется автоматически.",
                reply_markup=main_keyboard(),
                disable_web_page_preview=True
            )
        else:
            await callback.message.edit_text("Ошибка при создании счёта. Попробуйте позже.", reply_markup=main_keyboard())
        await callback.answer()
        return

# ==================== ОБРАБОТЧИК СООБЩЕНИЙ ====================
@dp.message()
async def handle_message(message: types.Message):
    user_id = message.from_user.id
    user_text = message.text.strip()
    if not user_text:
        return

    allowed, remaining = await check_and_increment_usage(user_id)
    if not allowed:
        tier = await get_user_tier(user_id)
        limit = TIER_LIMITS.get(tier, 20)
        await message.answer(
            f"Лимит запросов исчерпан.\n"
            f"Ваш тариф: {tier.upper()} – {limit} запросов в день.\n"
            f"Повысьте тариф через кнопку Купить премиум"
        )
        return

    if user_id not in user_semaphores:
        user_semaphores[user_id] = asyncio.Semaphore(2)
    async with user_semaphores[user_id]:
        is_code_request = bool(re.search(r'(код|скрипт|программу|функцию|напиши код)', user_text, re.I))
        lang = detect_language(user_text) if is_code_request else None

        history = await db_get_history(user_id, limit=10)
        await db_add_history(user_id, "user", user_text)
        history.append({"role": "user", "content": user_text})
        messages_api = history.copy()

        user_model_id = await db_get_user_model(user_id)
        model_display = get_model_display_name(user_model_id)

        cache_key = hashlib.md5((user_model_id + "|" + json.dumps(messages_api[-4:], sort_keys=True)).encode()).hexdigest()
        cached = await db_cache_get(cache_key)
        if cached:
            await message.answer(cached, reply_markup=main_keyboard())
            return

        status_msg = await message.answer(f"{model_display} анализирует...")
        reply = await ask_ai(user_model_id, messages_api, is_code_request)

        if is_code_request and not reply.startswith('```'):
            reply = format_code_response(reply, lang if lang else 'python')

        await db_cache_set(cache_key, reply)
        await db_add_history(user_id, "assistant", reply[:2000])

        try:
            await status_msg.delete()
        except:
            pass
        await send_long_answer(message, reply, main_keyboard())
        if remaining <= 5:
            await message.answer(f"Осталось запросов сегодня: {remaining}")

# ==================== HEALTH CHECK И ВЕБХУК ====================
async def health_check(request):
    return web.Response(text="OK")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/health", health_check)
    app.router.add_post("/crypto_webhook", crypto_webhook)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    print(f"Health check server listening on 0.0.0.0:{PORT}")
    print(f"CryptoBot webhook: /crypto_webhook")

async def on_shutdown():
    global session
    if session and not session.closed:
        await session.close()
        session = None

async def main():
    global session, request_queue
    print("=== MAIN START ===")
    print(f"PORT = {PORT}")

    await start_web_server()
    await asyncio.sleep(5)

    session = aiohttp.ClientSession()
    request_queue = asyncio.Queue(maxsize=50)
    for i in range(queue_workers):
        asyncio.create_task(worker(i))

    print("LLM Hub started | 7 models | Qwen forced Russian | No clear button | CryptoBot integrated")
    await dp.start_polling(bot, on_shutdown=on_shutdown)

if __name__ == "__main__":
    asyncio.run(main())
