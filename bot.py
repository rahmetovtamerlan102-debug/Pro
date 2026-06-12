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

# ==================== ДИНАМИЧЕСКИЙ СПИСОК МОДЕЛЕЙ ====================
free_models_cache = []
cache_timestamp = 0
CACHE_TTL = 3600

# Жёсткий запасной список (если API не отвечает)
ULTIMATE_FALLBACK_MODELS = [
    "deepseek/deepseek-chat:free",
    "meta-llama/llama-3.1-8b-instruct:free"
]

async def fetch_free_models():
    global free_models_cache, cache_timestamp
    now = time.time()
    if free_models_cache and (now - cache_timestamp) < CACHE_TTL:
        return free_models_cache

    url = "https://openrouter.ai/api/v1/models"
    headers = {"Authorization": f"Bearer {OPENROUTER_API_KEY}"}
    async with aiohttp.ClientSession() as sess:
        try:
            async with sess.get(url, headers=headers, timeout=15) as resp:
                if resp.status != 200:
                    return ULTIMATE_FALLBACK_MODELS.copy()
                data = await resp.json()
                models = []
                for m in data.get("data", []):
                    model_id = m.get("id", "")
                    pricing = m.get("pricing", {})
                    if pricing.get("prompt", 1) == 0 and pricing.get("completion", 1) == 0:
                        models.append(model_id)
                if models:
                    free_models_cache = models
                    cache_timestamp = now
                    return models
                return ULTIMATE_FALLBACK_MODELS.copy()
        except Exception:
            return ULTIMATE_FALLBACK_MODELS.copy()

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
async def db_add_user(user_id):
    async with db_lock:
        with sqlite3.connect("bot.db", timeout=30) as conn:
            c = conn.cursor()
            c.execute("INSERT OR IGNORE INTO users (user_id, model, created_at) VALUES (?, ?, ?)",
                      (user_id, "auto", datetime.now()))
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

@db_retry
async def db_update_model_score(model_id, delta):
    async with db_lock:
        with sqlite3.connect("bot.db", timeout=30) as conn:
            c = conn.cursor()
            c.execute("INSERT INTO model_scores (model_id, score, uses, last_used) VALUES (?, ?, 1, ?) ON CONFLICT(model_id) DO UPDATE SET score = score + ?, uses = uses + 1, last_used = ?",
                      (model_id, delta, datetime.now(), delta, datetime.now()))
            conn.commit()

# ==================== API ЗАПРОС С ДЕТАЛЬНЫМ ЛОГИРОВАНИЕМ ====================
session = None

async def ask_ai_with_retry(model, messages, retries=3):
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
                status = resp.status
                text = await resp.text()
                
                # Логирование в консоль Render
                print(f"[DEBUG] Модель: {model}")
                print(f"[DEBUG] HTTP статус: {status}")
                if status != 200:
                    print(f"[DEBUG] Тело ошибки: {text[:500]}")
                
                if status == 401:
                    return None, "❌ Ошибка: недействительный API-ключ OpenRouter. Проверьте OPENROUTER_API_KEY"
                if status == 402:
                    return None, "❌ Ошибка: недостаточно средств на аккаунте OpenRouter. Пополните баланс (минимально $5)"
                if status == 403:
                    return None, "❌ Ошибка: нет доступа к этой модели"
                if status == 429:
                    print(f"[DEBUG] Rate limit, попытка {attempt+1}/{retries}")
                    await asyncio.sleep(2 ** attempt)
                    continue
                if status >= 500:
                    print(f"[DEBUG] Ошибка сервера OpenRouter, попытка {attempt+1}/{retries}")
                    await asyncio.sleep(2 ** attempt)
                    continue
                if status != 200:
                    return None, f"❌ Ошибка API: {status}\n{text[:200]}"
                
                try:
                    data = json.loads(text)
                except json.JSONDecodeError:
                    return None, "❌ Ошибка: неверный формат ответа от OpenRouter"
                
                if "error" in data:
                    error_msg = data["error"].get("message", "Неизвестная ошибка")
                    return None, f"❌ Ошибка OpenRouter: {error_msg}"
                
                if not data.get("choices") or len(data["choices"]) == 0:
                    return None, "❌ Ошибка: OpenRouter вернул пустой ответ"
                
                content = data["choices"][0].get("message", {}).get("content")
                if not content:
                    return None, "❌ Ошибка: модель вернула пустое сообщение"
                
                return content, None
                
        except asyncio.TimeoutError:
            print(f"[DEBUG] Таймаут, попытка {attempt+1}/{retries}")
            await asyncio.sleep(2 ** attempt)
            continue
        except aiohttp.ClientError as e:
            print(f"[DEBUG] Клиентская ошибка: {e}, попытка {attempt+1}/{retries}")
            await asyncio.sleep(2 ** attempt)
            continue
        except Exception as e:
            print(f"[DEBUG] Неизвестная ошибка: {repr(e)}")
            await asyncio.sleep(2 ** attempt)
            continue
    
    return None, "❌ Не удалось получить ответ после нескольких попыток"

async def ask_with_fallback(messages):
    models = await fetch_free_models()
    if not models or len(models) < 2:
        models = ULTIMATE_FALLBACK_MODELS.copy()
        print("[DEBUG] Динамический список пуст, использую запасной")
    
    for model in models:
        print(f"[DEBUG] Пробуем модель: {model}")
        reply, err = await ask_ai_with_retry(model, messages)
        if reply is not None:
            print(f"[DEBUG] Успех с моделью: {model}")
            return reply, model
        else:
            print(f"[DEBUG] Модель {model} не ответила: {err}")
    
    print("[DEBUG] Все модели не ответили, пробуем последнюю надежду")
    for model in ULTIMATE_FALLBACK_MODELS:
        reply, err = await ask_ai_with_retry(model, messages)
        if reply is not None:
            print(f"[DEBUG] Последняя надежда сработала: {model}")
            return reply, model
    
    return "❌ Все модели временно недоступны. Проверьте:\n1. API-ключ OpenRouter (возможно, истёк)\n2. Баланс аккаунта (нужен хотя бы $5)\n3. Подключение к интернету", None

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
        [InlineKeyboardButton(text="Очистить диалог", callback_data="clear")],
        [InlineKeyboardButton(text="Статистика", callback_data="stats")]
    ])

# ==================== ХЕНДЛЕРЫ ====================
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    await db_add_user(user_id)
    await message.answer(
        f"🤖 AI Бот | Динамические модели\n\n"
        f"Список моделей обновляется автоматически.\n"
        f"Доступны только бесплатные модели OpenRouter.\n"
        f"Напиши сообщение или попроси код.",
        reply_markup=main_keyboard()
    )

@dp.callback_query()
async def handle_callback(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    data = callback.data

    if data == "clear":
        await db_clear_history(user_id)
        await callback.message.edit_text("История диалога очищена.", reply_markup=main_keyboard())
        await callback.answer()
        return

    if data == "stats":
        hist, cache = await db_get_stats(user_id)
        await callback.message.edit_text(f"Статистика\n\nСообщений в истории: {hist}\nКэш: {cache} записей", reply_markup=main_keyboard())
        await callback.answer()
        return

@dp.message()
async def handle_message(message: types.Message):
    user_id = message.from_user.id
    user_text = message.text.strip()
    if not user_text:
        return

    # Проверка ключа (один раз)
    if not hasattr(bot, "_key_checked"):
        test_reply, err = await ask_ai_with_retry(ULTIMATE_FALLBACK_MODELS[0], [{"role": "user", "content": "ping"}])
        if err and ("API-ключ" in err or "средств" in err):
            await message.answer(err)
            return
        bot._key_checked = True

    history = await db_get_history(user_id, limit=8)
    await db_add_history(user_id, "user", user_text)
    history.append({"role": "user", "content": user_text})
    messages_api = [{"role": h["role"], "content": h["content"]} for h in history]

    is_code = bool(re.search(r'(код|скрипт|программу|функцию)', user_text, re.I))
    lang = detect_language(user_text) if is_code else None

    cache_key = hashlib.md5(("dynamic" + "|" + json.dumps(messages_api[-3:], sort_keys=True)).encode()).hexdigest()
    cached = await db_cache_get(cache_key)
    if cached:
        await message.answer(cached, reply_markup=main_keyboard())
        return

    status_msg = await message.answer("⏳ Анализирую и выбираю модель...")
    reply, model_used = await ask_with_fallback(messages_api)

    if model_used:
        score = 5
        if len(reply) > 200:
            score += 1
        if len(reply) < 50:
            score -= 2
        if is_code and "```" in reply:
            score += 2
        delta = max(-5, min(5, score - 5))
        await db_update_model_score(model_used, delta)

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

async def main():
    global session
    print("=== MAIN START ===")
    print(f"PORT = {PORT}")
    session = aiohttp.ClientSession()
    await start_web_server()
    await asyncio.sleep(2)
    print("🚀 Bot started")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
