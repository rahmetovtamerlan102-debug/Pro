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
            model TEXT DEFAULT 'llama31',
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

# ==================== 15 БЕСПЛАТНЫХ МОДЕЛЕЙ (БЕЗ DEEPSEEK R1) ====================
MODELS = {
    "kimi": "moonshotai/kimi-k2.6:free",
    "deepseek_chat": "deepseek/deepseek-chat:free",
    "qwen3_thinking": "qwen/qwen3-thinking:free",
    "qwen3_coder": "qwen/qwen3-coder:free",
    "qwen36_plus": "qwen/qwen3.6-plus:free",
    "llama33_70b": "meta-llama/llama-3.3-70b-instruct:free",
    "llama31_8b": "meta-llama/llama-3.1-8b-instruct:free",
    "devstral2": "mistralai/devstral-2:free",
    "owl_alpha": "openrouter/owl-alpha:free",
    "trinity_large": "arcee-ai/trinity-large-preview:free",
    "laguna_m1": "poolside/laguna-m.1:free",
    "laguna_xs2": "poolside/laguna-xs.2:free",
    "mimo_v2": "xiaomi/mimo-v2-flash:free",
    "ring_2_6": "inclusionai/ring-2.6-1t:free",
    "phi3_mini": "microsoft/phi-3-mini-128k-instruct:free",
}

MODEL_NAMES = {
    "kimi": "Kimi K2.6",
    "deepseek_chat": "DeepSeek Chat",
    "qwen3_thinking": "Qwen3 Thinking",
    "qwen3_coder": "Qwen3 Coder",
    "qwen36_plus": "Qwen 3.6 Plus",
    "llama33_70b": "Llama 3.3 70B",
    "llama31_8b": "Llama 3.1 8B",
    "devstral2": "Devstral 2",
    "owl_alpha": "Owl Alpha",
    "trinity_large": "Trinity Large",
    "laguna_m1": "Laguna M1",
    "laguna_xs2": "Laguna XS2",
    "mimo_v2": "Mimo V2 Flash",
    "ring_2_6": "Ring 2.6 1T",
    "phi3_mini": "Phi-3 Mini",
}

DEFAULT_MODEL = "llama31_8b"

user_semaphores = {}
session = None
request_queue = asyncio.Queue(maxsize=50)
queue_workers = 2

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
            return row[0] if row else DEFAULT_MODEL

@db_retry
async def db_update_user_model(user_id, model_key):
    async with db_lock:
        with sqlite3.connect("bot.db", timeout=30) as conn:
            c = conn.cursor()
            c.execute("UPDATE users SET model = ? WHERE user_id = ?", (model_key, user_id))
            conn.commit()

@db_retry
async def db_add_user(user_id):
    async with db_lock:
        with sqlite3.connect("bot.db", timeout=30) as conn:
            c = conn.cursor()
            c.execute("INSERT OR IGNORE INTO users (user_id, model, created_at) VALUES (?, ?, ?)",
                      (user_id, DEFAULT_MODEL, datetime.now()))
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

# ==================== API ЗАПРОС ====================
async def worker(worker_id):
    while True:
        future, model, messages = await request_queue.get()
        try:
            result = await ask_ai_raw(model, messages)
            future.set_result(result)
        except Exception as e:
            future.set_exception(e)
        finally:
            request_queue.task_done()

async def ask_ai_with_queue(model, messages):
    loop = asyncio.get_running_loop()
    future = loop.create_future()
    await request_queue.put((future, model, messages))
    return await future

async def ask_ai_raw(model, messages):
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
    for attempt in range(3):
        async with session.post(url, json=payload, headers=headers) as resp:
            if resp.status == 429:
                await asyncio.sleep(2 ** attempt)
                continue
            if resp.status != 200:
                error_text = await resp.text()
                return f"Ошибка API: {resp.status}\n{error_text[:200]}"
            data = await resp.json()
            return data["choices"][0]["message"]["content"]
    return "Слишком много запросов, попробуйте позже."

async def ask_ai(model, messages):
    return await ask_ai_with_queue(model, messages)

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

def main_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Сменить модель", callback_data="show_models")],
        [InlineKeyboardButton(text="Очистить диалог", callback_data="clear")],
        [InlineKeyboardButton(text="Статистика", callback_data="stats")]
    ])

def model_keyboard():
    buttons = []
    row = []
    for key, name in MODEL_NAMES.items():
        row.append(InlineKeyboardButton(text=name[:15], callback_data=f"model_{key}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton(text="Назад", callback_data="back_to_main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# ==================== ХЕНДЛЕРЫ ====================
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    await db_add_user(user_id)
    await message.answer(
        f"AI Router | 15 бесплатных моделей\n\n"
        f"Доступно {len(MODELS)} моделей. Текущая: {MODEL_NAMES[DEFAULT_MODEL]}\n"
        f"Напиши сообщение или выбери модель в меню.",
        reply_markup=main_keyboard()
    )

@dp.callback_query()
async def handle_callback(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    data = callback.data

    if data.startswith("model_"):
        model_key = data.replace("model_", "")
        if model_key in MODELS:
            await db_update_user_model(user_id, model_key)
            await callback.message.edit_text(
                f"Модель изменена: {MODEL_NAMES[model_key]}",
                reply_markup=main_keyboard()
            )
        await callback.answer()
        return

    if data == "show_models":
        text = "Доступные бесплатные модели:\n\n" + "\n".join([f"- {n}" for n in MODEL_NAMES.values()])
        await callback.message.edit_text(text, reply_markup=model_keyboard())
        await callback.answer()
        return

    if data == "back_to_main":
        await callback.message.edit_text("Главное меню", reply_markup=main_keyboard())
        await callback.answer()
        return

    if data == "clear":
        await db_clear_history(user_id)
        await callback.message.edit_text("История диалога очищена", reply_markup=main_keyboard())
        await callback.answer()
        return

    if data == "stats":
        hist, cache = await db_get_stats(user_id)
        stats_text = f"Статистика\n\nСообщений в истории: {hist}\nРазмер кэша: {cache} записей"
        await callback.message.edit_text(stats_text, reply_markup=main_keyboard())
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
        is_code = bool(re.search(r'(код|скрипт|программу|функцию)', user_text, re.I))
        lang = detect_language(user_text) if is_code else None

        history = await db_get_history(user_id, limit=8)
        await db_add_history(user_id, "user", user_text)
        history.append({"role": "user", "content": user_text})
        messages_api = [{"role": h["role"], "content": h["content"]} for h in history]

        user_model_key = await db_get_user_model(user_id)
        model_to_use = MODELS.get(user_model_key, MODELS[DEFAULT_MODEL])
        model_display = MODEL_NAMES.get(user_model_key, MODEL_NAMES[DEFAULT_MODEL])

        cache_key = hashlib.md5((model_to_use + "|" + json.dumps(messages_api[-3:], sort_keys=True)).encode()).hexdigest()
        cached = await db_cache_get(cache_key)
        if cached:
            await message.answer(cached, reply_markup=main_keyboard())
            return

        status_msg = await message.answer(f"{model_display} анализирует...")
        reply = await ask_ai(model_to_use, messages_api)

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

    print("Bot started, health server reachable at /health")
    await dp.start_polling(bot, on_shutdown=on_shutdown)

if __name__ == "__main__":
    asyncio.run(main())
