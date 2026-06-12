#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio
import os
import re
import json
import sqlite3
import hashlib
import time
from datetime import datetime
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import aiohttp

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
PORT = int(os.environ.get("PORT", 10000))

if not BOT_TOKEN or not GROQ_API_KEY:
    raise ValueError("BOT_TOKEN и GROQ_API_KEY обязательны")

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
            model TEXT DEFAULT 'llama-3.1-8b-instant',
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
        conn.commit()

init_db()

# ==================== ДИНАМИЧЕСКАЯ ЗАГРУЗКА МОДЕЛЕЙ ====================
models_cache = []
models_cache_time = 0
CACHE_TTL = 3600  # 1 час

async def fetch_groq_models():
    """Получает актуальный список моделей из Groq API"""
    global models_cache, models_cache_time
    now = time.time()
    if models_cache and (now - models_cache_time) < CACHE_TTL:
        return models_cache
    
    url = "https://api.groq.com/openai/v1/models"
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}"}
    async with aiohttp.ClientSession() as sess:
        try:
            async with sess.get(url, headers=headers, timeout=15) as resp:
                if resp.status != 200:
                    return get_fallback_models()
                data = await resp.json()
                models = []
                for m in data.get("data", []):
                    model_id = m.get("id", "")
                    # Фильтруем только основные модели (исключаем устаревшие)
                    if any(x in model_id.lower() for x in ["llama", "gemma", "mixtral", "deepseek", "qwen", "phi"]):
                        models.append(model_id)
                if models:
                    models_cache = models
                    models_cache_time = now
                    return models
                return get_fallback_models()
        except Exception:
            return get_fallback_models()

def get_fallback_models():
    """Запасной список на случай, если API недоступен"""
    return [
        "llama-3.1-8b-instant",
        "llama-3.2-3b-preview",
        "llama-3.3-70b-versatile",
        "gemma2-9b-it",
        "mixtral-8x7b-32768",
        "deepseek-r1-distill-llama-70b",
        "qwen-2.5-32b-preview",
        "phi-3-mini-128k-instruct"
    ]

def get_model_display_name(model_id):
    """Преобразует ID модели в читаемое название"""
    names = {
        "llama-3.1-8b-instant": "Llama 3.1 8B",
        "llama-3.2-3b-preview": "Llama 3.2 3B",
        "llama-3.3-70b-versatile": "Llama 3.3 70B",
        "gemma2-9b-it": "Gemma 2 9B",
        "mixtral-8x7b-32768": "Mixtral 8x7B",
        "deepseek-r1-distill-llama-70b": "DeepSeek R1 70B",
        "qwen-2.5-32b-preview": "Qwen 2.5 32B",
        "phi-3-mini-128k-instruct": "Phi-3 Mini"
    }
    return names.get(model_id, model_id.split("-")[0].capitalize())

# ==================== РАБОТА С БД ====================
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
            return row[0] if row else "llama-3.1-8b-instant"

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
            c.execute("INSERT OR IGNORE INTO users (user_id, model, created_at) VALUES (?, ?, ?)",
                      (user_id, "llama-3.1-8b-instant", datetime.now()))
            conn.commit()

@db_retry
async def db_clear_history(user_id):
    async with db_lock:
        with sqlite3.connect("bot.db", timeout=30) as conn:
            c = conn.cursor()
            c.execute("DELETE FROM history WHERE user_id = ?", (user_id,))
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
async def db_get_history(user_id, limit=8):
    async with db_lock:
        with sqlite3.connect("bot.db", timeout=30) as conn:
            c = conn.cursor()
            c.execute("SELECT role, content FROM history WHERE user_id = ? ORDER BY timestamp DESC LIMIT ?", (user_id, limit))
            rows = c.fetchall()
            return [{"role": r[0], "content": r[1]} for r in reversed(rows)]

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

# ==================== API ЗАПРОС К GROQ ====================
session = None
request_queue = asyncio.Queue(maxsize=50)
queue_workers = 2
user_semaphores = {}

async def worker(worker_id):
    while True:
        future, model, messages = await request_queue.get()
        try:
            result = await ask_groq_raw(model, messages)
            future.set_result(result)
        except Exception as e:
            future.set_exception(e)
        finally:
            request_queue.task_done()

async def ask_groq_with_queue(model, messages):
    loop = asyncio.get_running_loop()
    future = loop.create_future()
    await request_queue.put((future, model, messages))
    return await future

async def ask_groq_raw(model, messages):
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    system_prompt = {"role": "system", "content": "Ты краткий, точный ассистент. Отвечай на русском."}
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
    
    for attempt in range(3):
        try:
            async with session.post(url, json=payload, headers=headers, timeout=30) as resp:
                if resp.status == 429:
                    await asyncio.sleep(2 ** attempt)
                    continue
                if resp.status == 401:
                    return "❌ Ошибка: недействительный API-ключ Groq. Проверьте GROQ_API_KEY"
                if resp.status != 200:
                    error_text = await resp.text()
                    return f"Ошибка API: {resp.status}\n{error_text[:200]}"
                data = await resp.json()
                if "error" in data:
                    return f"Ошибка: {data['error'].get('message', 'Неизвестная ошибка')}"
                content = data["choices"][0]["message"]["content"]
                return content
        except asyncio.TimeoutError:
            await asyncio.sleep(2 ** attempt)
            continue
        except Exception as e:
            await asyncio.sleep(2 ** attempt)
            continue
    return "Слишком много запросов, попробуйте позже."

async def ask_ai(model, messages):
    return await ask_groq_with_queue(model, messages)

# ==================== КЛАВИАТУРЫ ====================
async def get_models_keyboard():
    """Динамически создаёт клавиатуру из актуальных моделей Groq"""
    models = await fetch_groq_models()
    buttons = []
    row = []
    for model_id in models:
        name = get_model_display_name(model_id)
        row.append(InlineKeyboardButton(text=name[:15], callback_data=f"model_{model_id}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def main_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🧠 Сменить модель", callback_data="show_models")],
        [InlineKeyboardButton(text="🗑 Очистить диалог", callback_data="clear")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="stats")]
    ])

# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====================
def detect_language(text):
    if 'python' in text.lower() or 'питон' in text.lower():
        return 'python'
    if 'javascript' in text.lower() or 'js' in text.lower():
        return 'javascript'
    return 'python'

def format_code_response(text, lang):
    text = text.strip()
    if text.startswith('```'):
        return text
    return f"```{lang}\n{text}\n```"

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
    models = await fetch_groq_models()
    await message.answer(
        f"🤖 *Groq AI Router | Динамические модели*\n\n"
        f"📌 *Доступно моделей:* {len(models)}\n"
        f"⚡ *Лимит:* 30 запросов/минуту\n"
        f"🚀 *Список моделей обновляется автоматически*\n\n"
        f"👇 Выберите модель в меню",
        reply_markup=main_keyboard(),
        parse_mode="Markdown"
    )

@dp.callback_query()
async def handle_callback(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    data = callback.data

    if data.startswith("model_"):
        model_id = data.replace("model_", "")
        await db_update_user_model(user_id, model_id)
        await callback.message.edit_text(
            f"✅ Модель изменена: *{get_model_display_name(model_id)}*",
            reply_markup=main_keyboard(),
            parse_mode="Markdown"
        )
        await callback.answer()
        return

    if data == "show_models":
        keyboard = await get_models_keyboard()
        await callback.message.edit_text(
            "🧠 *Выберите модель из актуального списка Groq:*",
            reply_markup=keyboard,
            parse_mode="Markdown"
        )
        await callback.answer()
        return

    if data == "back_to_main":
        current = await db_get_user_model(user_id)
        current_name = get_model_display_name(current)
        await callback.message.edit_text(
            f"🤖 *Главное меню*\n📌 Текущая модель: {current_name}",
            reply_markup=main_keyboard(),
            parse_mode="Markdown"
        )
        await callback.answer()
        return

    if data == "clear":
        await db_clear_history(user_id)
        await callback.message.edit_text("🗑 История диалога очищена.", reply_markup=main_keyboard())
        await callback.answer()
        return

    if data == "stats":
        hist, cache = await db_get_stats(user_id)
        await callback.message.edit_text(
            f"📊 *Статистика*\n\nСообщений в истории: {hist}\nРазмер кэша: {cache} записей",
            reply_markup=main_keyboard(),
            parse_mode="Markdown"
        )
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
        is_code = bool(re.search(r'(код|скрипт|программу|функцию|напиши)', user_text, re.I))
        lang = detect_language(user_text) if is_code else None

        history = await db_get_history(user_id, limit=8)
        await db_add_history(user_id, "user", user_text)
        history.append({"role": "user", "content": user_text})
        messages_api = [{"role": h["role"], "content": h["content"]} for h in history]

        user_model_id = await db_get_user_model(user_id)
        model_display = get_model_display_name(user_model_id)

        cache_key = hashlib.md5((user_model_id + "|" + json.dumps(messages_api[-3:], sort_keys=True)).encode()).hexdigest()
        cached = await db_cache_get(cache_key)
        if cached:
            await message.answer(cached, reply_markup=main_keyboard())
            return

        status_msg = await message.answer(f"⏳ *{model_display}* анализирует...", parse_mode="Markdown")
        reply = await ask_ai(user_model_id, messages_api)

        if is_code and not reply.startswith('```'):
            reply = format_code_response(reply, lang)

        await db_cache_set(cache_key, reply)
        await db_add_history(user_id, "assistant", reply[:2000])

        try:
            await status_msg.delete()
        except:
            pass
        await send_long_answer(message, reply, main_keyboard())

# ==================== HEALTH CHECK ====================
async def handle_health(reader, writer):
    writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nOK")
    await writer.drain()
    writer.close()

async def start_web_server():
    server = await asyncio.start_server(handle_health, "0.0.0.0", PORT)
    print(f"Health check server listening on 0.0.0.0:{PORT}")
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
    for i in range(queue_workers):
        asyncio.create_task(worker(i))

    print("🚀 Groq AI Bot started | Dynamic models from API")
    await dp.start_polling(bot, on_shutdown=on_shutdown)

if __name__ == "__main__":
    asyncio.run(main())
