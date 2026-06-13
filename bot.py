#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio
import os
import re
import json
import sqlite3
import hashlib
import random
import string
import base64
from io import BytesIO
from datetime import datetime
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile
import aiohttp
from aiohttp import web

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
PORT = int(os.environ.get("PORT", 10000))
CRYPTOBOT_TOKEN = os.getenv("CRYPTOBOT_TOKEN")
CF_ACCOUNT_ID = os.getenv("CF_ACCOUNT_ID")
CF_API_TOKEN = os.getenv("CF_API_TOKEN")
AGNES_API_KEY = os.getenv("AGNES_API_KEY")

if not BOT_TOKEN or not GROQ_API_KEY or not CRYPTOBOT_TOKEN:
    raise ValueError("BOT_TOKEN, GROQ_API_KEY и CRYPTOBOT_TOKEN обязательны")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

user_state = {}

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
        c.execute('''CREATE TABLE IF NOT EXISTS referrals (
            user_id INTEGER PRIMARY KEY,
            code TEXT UNIQUE,
            bonus_balance INTEGER DEFAULT 0,
            referred_by INTEGER,
            created_at TIMESTAMP
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

# ==================== ГЕНЕРАЦИЯ ИЗОБРАЖЕНИЙ (С ПРОГРЕССОМ) ====================
async def generate_image_with_progress(status_msg: types.Message, prompt: str) -> str | None:
    """Генерирует изображение, обновляя status_msg точками"""
    if not CF_ACCOUNT_ID or not CF_API_TOKEN:
        return None
    url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/ai/run/@cf/black-forest-labs/flux-1-schnell"
    headers = {
        "Authorization": f"Bearer {CF_API_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "prompt": prompt,
        "height": 512,
        "width": 512,
        "num_steps": 20
    }
    # Анимация прогресса
    progress_task = asyncio.create_task(animate_progress(status_msg, "Генерирую изображение"))
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers, timeout=60) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    result = data.get("result", {})
                    if "image" in result:
                        return result["image"]
                return None
    finally:
        progress_task.cancel()
        try:
            await status_msg.edit_text("✅ Изображение готово!")
        except:
            pass

async def animate_progress(msg: types.Message, base_text: str):
    """Анимирует сообщение с точками каждые 0.5 секунды"""
    dots = 0
    while True:
        dots = (dots % 3) + 1
        try:
            await msg.edit_text(f"{base_text}{'.' * dots}")
        except:
            pass
        await asyncio.sleep(0.5)

# ==================== ГЕНЕРАЦИЯ ВИДЕО (С ПРОГРЕССОМ) ====================
async def generate_video_with_progress(status_msg: types.Message, prompt: str) -> str | None:
    if not AGNES_API_KEY:
        return None
    create_url = "https://api.agnes-ai.com/v1/video/generate"
    headers = {
        "Authorization": f"Bearer {AGNES_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "agnes-video-v2.0",
        "prompt": prompt,
        "duration": 5,
        "resolution": "720p"
    }
    async with aiohttp.ClientSession() as session:
        # Создаём задачу
        async with session.post(create_url, json=payload, headers=headers, timeout=60) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
            task_id = data.get("task_id")
            if not task_id:
                return None

        # Анимация ожидания
        progress_task = asyncio.create_task(animate_progress(status_msg, "Генерирую видео (это может занять до минуты)"))
        try:
            status_url = f"https://api.agnes-ai.com/v1/video/status/{task_id}"
            for attempt in range(45):  # до 90 секунд
                await asyncio.sleep(2)
                async with session.get(status_url, headers=headers) as status_resp:
                    if status_resp.status != 200:
                        continue
                    status_data = await status_resp.json()
                    if status_data.get("status") == "completed":
                        video_url = status_data.get("video_url") or (status_data.get("urls") or [None])[0]
                        return video_url
                    elif status_data.get("status") == "failed":
                        return None
            return None
        finally:
            progress_task.cancel()
            try:
                await status_msg.edit_text("✅ Видео готово!")
            except:
                pass

async def send_video_to_user(message: types.Message, video_url: str):
    try:
        await message.answer_video(video_url, reply_markup=main_keyboard())
    except Exception:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(video_url) as resp:
                    if resp.status == 200:
                        video_data = await resp.read()
                        input_file = BufferedInputFile(video_data, filename="video.mp4")
                        await message.answer_video(input_file, reply_markup=main_keyboard())
                    else:
                        await message.answer("Не удалось загрузить видео.")
        except Exception as e:
            await message.answer(f"Ошибка при отправке видео: {e}")

# ==================== ЛИМИТЫ И ТАРИФЫ (БЕЗ ИЗМЕНЕНИЙ) ====================
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

async def get_usage_stats(user_id):
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
            used = row[0] if row else 0
            remaining = limit - used
            return tier, used, remaining, limit

# ==================== РЕФЕРАЛЫ (БЕЗ ИЗМЕНЕНИЙ) ====================
def generate_referral_code():
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))

async def get_or_create_referral(user_id):
    async with db_lock:
        with sqlite3.connect("bot.db", timeout=30) as conn:
            c = conn.cursor()
            c.execute("SELECT code, bonus_balance FROM referrals WHERE user_id = ?", (user_id,))
            row = c.fetchone()
            if row:
                return row[0], row[1]
            code = generate_referral_code()
            c.execute("INSERT INTO referrals (user_id, code, bonus_balance, created_at) VALUES (?, ?, ?, ?)",
                      (user_id, code, 0, datetime.now()))
            conn.commit()
            return code, 0

async def add_bonus_requests(user_id, amount):
    async with db_lock:
        with sqlite3.connect("bot.db", timeout=30) as conn:
            c = conn.cursor()
            c.execute("UPDATE referrals SET bonus_balance = bonus_balance + ? WHERE user_id = ?", (amount, user_id))
            conn.commit()

async def use_bonus_request(user_id):
    async with db_lock:
        with sqlite3.connect("bot.db", timeout=30) as conn:
            c = conn.cursor()
            c.execute("SELECT bonus_balance FROM referrals WHERE user_id = ?", (user_id,))
            row = c.fetchone()
            if row and row[0] > 0:
                c.execute("UPDATE referrals SET bonus_balance = bonus_balance - 1 WHERE user_id = ?", (user_id,))
                conn.commit()
                return True, row[0] - 1
            return False, 0

async def apply_referral_code(user_id, referrer_code):
    async with db_lock:
        with sqlite3.connect("bot.db", timeout=30) as conn:
            c = conn.cursor()
            c.execute("SELECT user_id FROM referrals WHERE code = ?", (referrer_code,))
            row = c.fetchone()
            if not row:
                return False, "Неверный реферальный код"
            referrer_id = row[0]
            if referrer_id == user_id:
                return False, "Нельзя пригласить самого себя"
            c.execute("SELECT referred_by FROM referrals WHERE user_id = ?", (user_id,))
            row = c.fetchone()
            if row and row[0]:
                return False, "Вы уже активировали реферальный код"
            c.execute("UPDATE referrals SET referred_by = ? WHERE user_id = ?", (referrer_id, user_id))
            conn.commit()
            await add_bonus_requests(user_id, 50)
            await add_bonus_requests(referrer_id, 50)
            return True, "Вы и ваш друг получили +50 бонусных запросов!"

# ==================== CRYPTOBOT (БЕЗ ИЗМЕНЕНИЙ) ====================
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
                except:
                    pass
    return web.Response(text="OK")

# ==================== КЛАВИАТУРЫ (БЕЗ ИЗМЕНЕНИЙ) ====================
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
        [InlineKeyboardButton(text="Личный кабинет", callback_data="profile")],
        [InlineKeyboardButton(text="Сменить модель", callback_data="show_models")],
        [InlineKeyboardButton(text="Купить премиум", callback_data="premium")],
        [InlineKeyboardButton(text="Генерация изображения", callback_data="gen_image")],
        [InlineKeyboardButton(text="Генерация видео", callback_data="gen_video")]
    ])

def premium_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="PRO (300 запросов/день)", callback_data="buy_pro")],
        [InlineKeyboardButton(text="ULTRA (безлимит)", callback_data="buy_ultra")],
        [InlineKeyboardButton(text="Назад", callback_data="back_to_main")]
    ])

def profile_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Повысить тариф", callback_data="premium")],
        [InlineKeyboardButton(text="Назад", callback_data="back_to_main")]
    ])

# ==================== РАБОТА С БД (ОСТАЛЬНЫЕ ФУНКЦИИ БЕЗ ИЗМЕНЕНИЙ) ====================
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
    await get_or_create_referral(user_id)

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

# ==================== URL, ФАЙЛЫ, GROQ API (БЕЗ ИЗМЕНЕНИЙ) ====================
async def fetch_url_content(url: str) -> str:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as resp:
                if resp.status == 200:
                    text = await resp.text()
                    return text[:5000]
                return f"Ошибка: HTTP {resp.status}"
    except Exception as e:
        return f"Ошибка: {str(e)}"

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

# ==================== ОБРАБОТКА ФАЙЛОВ ====================
async def process_file(document: types.Document) -> str:
    file = await bot.get_file(document.file_id)
    file_path = file.file_path
    downloaded_file = await bot.download_file(file_path)
    content = downloaded_file.read().decode('utf-8', errors='ignore')
    return content[:5000]

# ==================== ХЕНДЛЕРЫ (С ПРОГРЕССОМ ДЛЯ ИЗОБРАЖЕНИЙ И ВИДЕО) ====================
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    await db_add_user(user_id)
    args = message.text.split()
    if len(args) > 1 and args[1].startswith("ref_"):
        code = args[1][4:]
        success, msg = await apply_referral_code(user_id, code)
        if success:
            await message.answer(msg)
    await message.answer(
        "LLM Hub\n\nБыстрый доступ к AI моделям\nВыберите действие ниже",
        reply_markup=main_keyboard()
    )

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
        await callback.message.edit_text("Главное меню\nВыберите действие ниже", reply_markup=main_keyboard())
        await callback.answer()
        return

    if data == "profile":
        tier, used, remaining, limit = await get_usage_stats(user_id)
        code, bonus = await get_or_create_referral(user_id)
        hist, cache = await db_get_stats(user_id)
        current_model = await db_get_user_model(user_id)
        model_name = get_model_display_name(current_model)
        text = (
            f"Личный кабинет\n\n"
            f"Тариф: {tier.upper()}\n"
            f"Запросов сегодня: {used} / {limit}\n"
            f"Осталось: {remaining}\n"
            f"Бонусных запросов: {bonus}\n"
            f"Сброс: 00:00 UTC\n"
            f"Текущая модель: {model_name}\n\n"
            f"Статистика\nВсего сообщений: {hist}\n\n"
            f"Реферальная ссылка:\n"
            f"https://t.me/{bot.username}?start=ref_{code}\n"
            f"За каждого друга +50 бонусов"
        )
        await callback.message.edit_text(text, reply_markup=profile_keyboard())
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

    if data == "gen_image":
        user_state[user_id] = "waiting_for_image_prompt"
        await callback.message.edit_text(
            "Отправьте описание картинки.\n\nНапример: «кот в космосе»",
            reply_markup=main_keyboard()
        )
        await callback.answer()
        return

    if data == "gen_video":
        user_state[user_id] = "waiting_for_video_prompt"
        await callback.message.edit_text(
            "Отправьте описание видео.\n\nНапример: «закат на пляже, 5 секунд»",
            reply_markup=main_keyboard()
        )
        await callback.answer()
        return

# ==================== ОБРАБОТЧИК СООБЩЕНИЙ (С ПРОГРЕССОМ) ====================
@dp.message()
async def handle_message(message: types.Message):
    user_id = message.from_user.id
    user_text = message.text.strip() if message.text else ""

    # ---- Генерация изображения с прогрессом ----
    if user_id in user_state and user_state[user_id] == "waiting_for_image_prompt":
        user_state[user_id] = None
        if not user_text:
            await message.answer("Пожалуйста, отправьте текстовое описание картинки.")
            return
        status_msg = await message.answer("Генерирую изображение.")
        image_base64 = await generate_image_with_progress(status_msg, user_text)
        if image_base64:
            image_bytes = base64.b64decode(image_base64)
            photo_file = BufferedInputFile(image_bytes, filename="image.png")
            await message.answer_photo(photo=photo_file, reply_markup=main_keyboard())
        else:
            await message.answer("Не удалось сгенерировать изображение. Проверьте настройки Cloudflare API.")
        return

    # ---- Генерация видео с прогрессом ----
    if user_id in user_state and user_state[user_id] == "waiting_for_video_prompt":
        user_state[user_id] = None
        if not user_text:
            await message.answer("Пожалуйста, отправьте текстовое описание видео.")
            return
        status_msg = await message.answer("Генерирую видео (это может занять до минуты).")
        video_url = await generate_video_with_progress(status_msg, user_text)
        if video_url:
            await send_video_to_user(message, video_url)
        else:
            await message.answer("Не удалось сгенерировать видео. Попробуйте другой запрос.")
        return

    # ---- Остальная логика (длинные сообщения, URL, файлы, ИИ) без изменений ----
    if len(user_text) > 4000:
        parts = split_long_message(user_text, 4000)
        user_text = " ".join(parts)

    if user_text and re.match(r'^https?://', user_text):
        status_msg = await message.answer("Анализирую ссылку...")
        url_content = await fetch_url_content(user_text)
        if url_content.startswith("Ошибка"):
            await status_msg.edit_text(url_content)
            return
        user_text = f"Проанализируй содержимое страницы:\n{url_content}\n\nВопрос: что это за страница?"
        await status_msg.delete()

    if message.document:
        ext = message.document.file_name.split('.')[-1].lower()
        if ext not in ['txt', 'py', 'json', 'md', 'csv']:
            await message.answer("Поддерживаются файлы: txt, py, json, md, csv")
            return
        status_msg = await message.answer("Читаю файл...")
        file_content = await process_file(message.document)
        user_text = f"Проанализируй содержимое файла:\n{file_content}"
        await status_msg.delete()

    if not user_text:
        return

    used_bonus, bonus_remaining = await use_bonus_request(user_id)
    if used_bonus:
        allowed = True
        remaining = bonus_remaining
    else:
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
        if not used_bonus and remaining <= 5:
            await message.answer(f"Осталось запросов сегодня: {remaining}")

# ==================== HEALTH CHECK & WEB SERVER ====================
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

    print("LLM Hub started | Image + Video generation with progress | Profile | Referrals | Files | URL | CryptoBot")
    await dp.start_polling(bot, on_shutdown=on_shutdown)

if __name__ == "__main__":
    asyncio.run(main())
