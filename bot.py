#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio
import os
import re
import json
import sqlite3
import hashlib
import time
import math
from datetime import datetime
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import aiohttp

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
PORT = int(os.environ.get("PORT", 10000))

if not BOT_TOKEN or not OPENROUTER_API_KEY:
    raise ValueError("BOT_TOKEN и OPENROUTER_API_KEY обязательны")

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
            model TEXT DEFAULT 'auto',
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
        c.execute('''CREATE TABLE IF NOT EXISTS model_scores (
            model_id TEXT PRIMARY KEY,
            score INTEGER DEFAULT 0,
            uses INTEGER DEFAULT 0,
            errors INTEGER DEFAULT 0,
            last_used TIMESTAMP
        )''')
        conn.commit()

init_db()

# ==================== ДИНАМИЧЕСКОЕ ПОЛУЧЕНИЕ БЕСПЛАТНЫХ МОДЕЛЕЙ ====================
free_models_cache = []
cache_timestamp = 0
CACHE_TTL = 1800  # 30 минут (вместо 1 часа, чтобы быстрее видеть новые модели)

async def fetch_free_models():
    """Получает актуальный список бесплатных моделей через API OpenRouter"""
    global free_models_cache, cache_timestamp
    now = time.time()
    if free_models_cache and (now - cache_timestamp) < CACHE_TTL:
        return free_models_cache

    url = "https://openrouter.ai/api/v1/models"
    headers = {"Authorization": f"Bearer {OPENROUTER_API_KEY}"}
    async with aiohttp.ClientSession() as sess:
        try:
            async with sess.get(url, headers=headers, timeout=10) as resp:
                if resp.status != 200:
                    # fallback на небольшой встроенный список
                    return get_fallback_models()
                data = await resp.json()
                models = []
                for m in data.get("data", []):
                    model_id = m.get("id", "")
                    pricing = m.get("pricing", {})
                    # Бесплатно, если цена 0
                    if pricing.get("prompt", 1) == 0 and pricing.get("completion", 1) == 0:
                        models.append(model_id)
                if models:
                    free_models_cache = models
                    cache_timestamp = now
                    return models
                else:
                    return get_fallback_models()
        except Exception:
            return get_fallback_models()

def get_fallback_models():
    """Статический список на случай, если API недоступен"""
    return [
        "deepseek/deepseek-chat:free",
        "meta-llama/llama-3.1-8b-instruct:free",
        "qwen/qwen3-coder:free",
        "mistralai/devstral-2:free"
    ]

# ==================== УМНЫЙ ВЫБОР МОДЕЛИ ПО ЗАДАЧЕ ====================
async def smart_route_model(user_text):
    """Возвращает лучшую модель для задачи (без UCB1, только по типу)"""
    text_lower = user_text.lower()
    # Код
    if re.search(r'(код|скрипт|программу|функцию|напиши|сделай)', text_lower):
        return "qwen/qwen3-coder:free"
    # Рассуждения
    if re.search(r'(почему|объясни|как работает|докажи|рассуди)', text_lower):
        return "deepseek/deepseek-chat:free"   # можно deepseek-r1, но он не всегда бесплатен
    # По умолчанию – быстрая Llama
    return "meta-llama/llama-3.1-8b-instruct:free"

# ==================== UCB1 С FALLBACK ====================
async def get_best_model():
    """Выбирает модель с максимальным UCB1, обновляя статистику"""
    models = await fetch_free_models()
    if not models:
        models = get_fallback_models()
    async with db_lock:
        with sqlite3.connect("bot.db", timeout=30) as conn:
            c = conn.cursor()
            scores = {}
            for model in models:
                c.execute("SELECT score, uses FROM model_scores WHERE model_id = ?", (model,))
                row = c.fetchone()
                if row:
                    scores[model] = {"score": row[0], "uses": row[1]}
                else:
                    scores[model] = {"score": 0, "uses": 0}
    total_uses = sum(d["uses"] for d in scores.values())
    best_model = None
    best_ucb = -float('inf')
    for model, data in scores.items():
        if data["uses"] == 0:
            ucb = float('inf')
        else:
            avg = data["score"] / data["uses"]
            exploration = math.sqrt(2 * math.log(total_uses + 1) / data["uses"])
            ucb = avg + exploration
        if ucb > best_ucb:
            best_ucb = ucb
            best_model = model
    return best_model if best_model else models[0]

async def update_model_score(model_id, delta):
    async with db_lock:
        with sqlite3.connect("bot.db", timeout=30) as conn:
            c = conn.cursor()
            c.execute("INSERT INTO model_scores (model_id, score, uses, last_used) VALUES (?, ?, 1, ?) ON CONFLICT(model_id) DO UPDATE SET score = score + ?, uses = uses + 1, last_used = ?",
                      (model_id, delta, datetime.now(), delta, datetime.now()))
            conn.commit()

# ==================== НАДЁЖНЫЙ ЗАПРОС К OPENROUTER (RETRY + FALLBACK) ====================
async def ask_ai_with_retry(model, messages, retries=3):
    """Выполняет запрос с exponential backoff при ошибках 429, 500, таймауте"""
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }
    system_prompt = {"role": "system", "content": "Ты краткий, точный ассистент. Не повторяйся."}
    api_messages = [system_prompt] + messages
    payload = {
        "model": model,
        "messages": api_messages,
        "temperature": 0.7,
        "max_tokens": 2000
    }
    global session
    if session is None or session.closed:
        session = aiohttp.ClientSession()
    for attempt in range(retries):
        try:
            async with session.post(url, json=payload, headers=headers, timeout=30) as resp:
                if resp.status == 429:
                    await asyncio.sleep(2 ** attempt)  # экспоненциальная задержка
                    continue
                if resp.status >= 500:
                    await asyncio.sleep(2 ** attempt)
                    continue
                if resp.status != 200:
                    error_text = await resp.text()
                    return None, f"Ошибка API: {resp.status}\n{error_text[:200]}"
                data = await resp.json()
                return data["choices"][0]["message"]["content"], None
        except asyncio.TimeoutError:
            await asyncio.sleep(2 ** attempt)
            continue
        except Exception:
            await asyncio.sleep(2 ** attempt)
            continue
    return None, "Не удалось получить ответ после нескольких попыток."

async def ask_with_fallback(messages, user_text):
    """Пробует несколько моделей по цепочке: умный выбор → UCB1 → список fallback"""
    # 1. Умный роутинг
    smart_model = await smart_route_model(user_text)
    reply, err = await ask_ai_with_retry(smart_model, messages)
    if reply is not None:
        return reply, smart_model

    # 2. UCB1 лучшая модель
    best_model = await get_best_model()
    if best_model != smart_model:
        reply, err = await ask_ai_with_retry(best_model, messages)
        if reply is not None:
            return reply, best_model

    # 3. Жёсткий fallback список
    fallback_models = get_fallback_models()
    for fb in fallback_models:
        if fb == smart_model or fb == best_model:
            continue
        reply, err = await ask_ai_with_retry(fb, messages)
        if reply is not None:
            return reply, fb

    return "❌ Все модели временно недоступны. Попробуйте позже.", None

# ==================== КЛАВИАТУРА ВЫБОРА МОДЕЛЕЙ ====================
async def get_models_keyboard():
    """Создаёт инлайн-клавиатуру со всеми бесплатными моделями (максимум 12)"""
    models = await fetch_free_models()
    if not models:
        models = get_fallback_models()
    models = models[:12]   # не перегружаем интерфейс
    buttons = []
    row = []
    for m in models:
        short_name = m.split('/')[-1].replace(':free', '').replace('-instruct', '')[:14]
        row.append(InlineKeyboardButton(text=short_name, callback_data=f"model_{m}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton(text="🤖 Авто (UCB1)", callback_data="model_auto")])
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def main_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🧠 Выбрать модель", callback_data="show_models")],
        [InlineKeyboardButton(text="🗑 Очистить диалог", callback_data="clear")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="stats")]
    ])

# ==================== ХЕНДЛЕРЫ БОТА ====================
user_semaphores = {}
session = None
request_queue = asyncio.Queue(maxsize=50)
queue_workers = 2

async def worker(worker_id):
    while True:
        future, messages, user_text = await request_queue.get()
        try:
            reply, model_used = await ask_with_fallback(messages, user_text)
            future.set_result((reply, model_used))
        except Exception as e:
            future.set_exception(e)
        finally:
            request_queue.task_done()

async def ask_ai_thread(messages, user_text):
    loop = asyncio.get_running_loop()
    future = loop.create_future()
    await request_queue.put((future, messages, user_text))
    return await future

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    async with db_lock:
        with sqlite3.connect("bot.db", timeout=30) as conn:
            c = conn.cursor()
            c.execute("INSERT OR IGNORE INTO users (user_id, model, created_at) VALUES (?, ?, ?)",
                      (user_id, "auto", datetime.now()))
    await message.answer(
        "🤖 AI Router v2 | Динамические модели\n\n"
        "• Бесплатные модели обновляются из OpenRouter каждые 30 минут\n"
        "• Умный роутинг: код → Qwen Coder, рассуждения → DeepSeek\n"
        "• Автовыбор (UCB1) + ручной выбор\n"
        "• Автоматический fallback при сбоях\n\n"
        "Используй кнопки 👇",
        reply_markup=main_keyboard()
    )

@dp.callback_query()
async def handle_callback(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    data = callback.data

    if data.startswith("model_"):
        model_id = data.replace("model_", "")
        async with db_lock:
            with sqlite3.connect("bot.db", timeout=30) as conn:
                c = conn.cursor()
                if model_id == "auto":
                    c.execute("UPDATE users SET model = 'auto' WHERE user_id = ?", (user_id,))
                    text = "✅ Включён автоматический режим (UCB1)."
                else:
                    c.execute("UPDATE users SET model = ? WHERE user_id = ?", (model_id, user_id))
                    text = f"✅ Выбрана модель: {model_id}"
                conn.commit()
        await callback.message.edit_text(text, reply_markup=main_keyboard())
        await callback.answer()
        return

    if data == "show_models":
        kb = await get_models_keyboard()
        await callback.message.edit_text("Выберите модель (только бесплатные):", reply_markup=kb)
        await callback.answer()
        return

    if data == "back_to_main":
        await callback.message.edit_text("Главное меню", reply_markup=main_keyboard())
        await callback.answer()
        return

    if data == "clear":
        async with db_lock:
            with sqlite3.connect("bot.db", timeout=30) as conn:
                c = conn.cursor()
                c.execute("DELETE FROM history WHERE user_id = ?", (user_id,))
                conn.commit()
        await callback.message.edit_text("🗑 История диалога очищена.", reply_markup=main_keyboard())
        await callback.answer()
        return

    if data == "stats":
        async with db_lock:
            with sqlite3.connect("bot.db", timeout=30) as conn:
                c = conn.cursor()
                c.execute("SELECT COUNT(*) FROM history WHERE user_id = ?", (user_id,))
                hist = c.fetchone()[0]
                c.execute("SELECT COUNT(*) FROM cache")
                cache = c.fetchone()[0]
                c.execute("SELECT model_id, score, uses FROM model_scores ORDER BY score DESC LIMIT 5")
                top = c.fetchall()
        msg = f"📊 Статистика\n\nИстория: {hist} сообщ.\nКэш: {cache} записей.\n\n🏆 Лучшие модели:\n"
        for m in top:
            short = m[0].split('/')[-1][:25]
            msg += f"• {short}: {m[1]} очков ({m[2]} раз)\n"
        await callback.message.edit_text(msg, reply_markup=main_keyboard())
        await callback.answer()
        return

@dp.message()
async def handle_message(message: types.Message):
    user_id = message.from_user.id
    user_text = message.text.strip()
    if not user_text:
        return

    if user_id not in user_semaphores:
        user_semaphores[user_id] = asyncio.Semaphore(2)
    async with user_semaphores[user_id]:
        # История
        async with db_lock:
            with sqlite3.connect("bot.db", timeout=30) as conn:
                c = conn.cursor()
                c.execute("SELECT role, content FROM history WHERE user_id = ? ORDER BY timestamp DESC LIMIT 8", (user_id,))
                rows = c.fetchall()
                history = [{"role": r[0], "content": r[1]} for r in reversed(rows)]
                c.execute("INSERT INTO history (user_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
                          (user_id, "user", user_text, datetime.now()))
                conn.commit()
        history.append({"role": "user", "content": user_text})
        messages_api = [{"role": h["role"], "content": h["content"]} for h in history]

        # Выбор модели: ручная или авто
        async with db_lock:
            with sqlite3.connect("bot.db", timeout=30) as conn:
                c = conn.cursor()
                c.execute("SELECT model FROM users WHERE user_id = ?", (user_id,))
                row = c.fetchone()
                user_model = row[0] if row else "auto"
        if user_model == "auto":
            model_to_use = await get_best_model()
            model_display = "🤖 Авто (UCB1)"
        else:
            model_to_use = user_model
            model_display = user_model.split('/')[-1][:20]

        # Кэш
        cache_key = hashlib.md5((model_to_use + "|" + json.dumps(messages_api[-3:], sort_keys=True)).encode()).hexdigest()
        async with db_lock:
            with sqlite3.connect("bot.db", timeout=30) as conn:
                c = conn.cursor()
                c.execute("SELECT response FROM cache WHERE key = ?", (cache_key,))
                cached = c.fetchone()
        if cached:
            await message.answer(cached[0], reply_markup=main_keyboard())
            return

        status_msg = await message.answer(f"⏳ {model_display} анализирует...")
        reply, used_model = await ask_ai_thread(messages_api, user_text)
        if used_model is None:
            used_model = model_to_use

        # Простая оценка качества для UCB1
        is_code_req = bool(re.search(r'(код|скрипт|программу|функцию)', user_text, re.I))
        score = 5
        if len(reply) > 200:
            score += 1
        if len(reply) < 50:
            score -= 2
        if is_code_req and "```" in reply:
            score += 2
        delta = max(-5, min(5, score - 5))
        await update_model_score(used_model, delta)

        # Форматирование кода
        if is_code_req and not reply.startswith('```'):
            lang = 'python' if 'python' in user_text.lower() else ''
            reply = f"```{lang}\n{reply}\n```" if lang else f"```\n{reply}\n```"

        # Сохраняем в кэш и историю
        async with db_lock:
            with sqlite3.connect("bot.db", timeout=30) as conn:
                c = conn.cursor()
                c.execute("INSERT OR REPLACE INTO cache (key, response, created_at) VALUES (?, ?, ?)",
                          (cache_key, reply[:1500], datetime.now()))
                c.execute("INSERT INTO history (user_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
                          (user_id, "assistant", reply[:2000], datetime.now()))
                conn.commit()

        try:
            await status_msg.delete()
        except:
            pass

        # Отправка ответа (разбивка на части при длине > 4000)
        if len(reply) > 4000:
            for i in range(0, len(reply), 4000):
                await message.answer(reply[i:i+4000], reply_markup=main_keyboard() if i == 0 else None)
        else:
            await message.answer(reply, reply_markup=main_keyboard())

# ==================== HEALTH CHECK ДЛЯ RENDER ====================
async def handle_health(reader, writer):
    writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nOK")
    await writer.drain()
    writer.close()

async def start_web_server():
    server = await asyncio.start_server(handle_health, "0.0.0.0", PORT)
    print(f"✅ Health check server listening on 0.0.0.0:{PORT}")
    asyncio.create_task(server.serve_forever())

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
    for _ in range(queue_workers):
        asyncio.create_task(worker(_))

    print("🚀 Bot started, health server reachable at /health")
    await dp.start_polling(bot, on_shutdown=on_shutdown)

if __name__ == "__main__":
    asyncio.run(main())
