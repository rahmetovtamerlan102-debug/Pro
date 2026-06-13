#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LLM Hub Bot - Production Ready Telegram Bot
Version: 4.0.0
Features: 6 AI Models, PostgreSQL, Redis, Rate Limiting, Anti-Spam, 
          Referral System, Premium Payments, Admin Panel, Export Dialog,
          Health Check, Metrics, Queue System, Circuit Breaker
"""

import asyncio
import os
import sys
import json
import hashlib
import hmac
import string
import random
import logging
import time
import signal
import re
import traceback
import uuid
import base64
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from typing import Optional, Tuple, Dict, List, Any, Union
from dataclasses import dataclass, field
from enum import Enum
from functools import wraps
from collections import defaultdict
import secrets

from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton, 
    BufferedInputFile, Message, CallbackQuery,
    ReplyKeyboardMarkup, KeyboardButton
)
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
import aiohttp
from aiohttp import web, ClientTimeout, ClientSession
import asyncpg
from asyncpg.pool import Pool
import redis.asyncio as redis

load_dotenv()

# ==================== ВЕРСИЯ ====================
__version__ = "4.0.0"
__author__ = "LLM Hub Team"

# ==================== КОНФИГУРАЦИЯ ====================
class Config:
    # Telegram
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
    BOT_USERNAME: str = os.getenv("BOT_USERNAME", "LLMHubBot")
    ADMIN_IDS: List[int] = [int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]
    
    # API Keys
    GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
    CRYPTOBOT_TOKEN: str = os.getenv("CRYPTOBOT_TOKEN", "")
    
    # Database
    DATABASE_URL: str = os.getenv("DATABASE_URL", "")
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379")
    
    # Server
    PORT: int = int(os.environ.get("PORT", 10000))
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development")
    
    # Limits
    MAX_MESSAGE_LEN: int = 8000
    MAX_FILE_SIZE: int = 20 * 1024 * 1024
    MAX_HISTORY: int = 100
    REFERRAL_BONUS: int = 20
    DAILY_LIMIT_FREE: int = 30
    DAILY_LIMIT_PRO: int = 500
    DAILY_LIMIT_ULTRA: int = 10000
    
    # Rate Limits
    RATE_LIMIT_REQUESTS: int = 2
    RATE_LIMIT_PERIOD: int = 1
    SPAM_THRESHOLD: int = 50
    SPAM_WINDOW: int = 60
    SPAM_WARN_THRESHOLD: int = 30
    
    # Queue
    QUEUE_MAX_SIZE: int = 200
    QUEUE_WORKERS: int = 10
    GLOBAL_CONCURRENCY: int = 10
    REQUEST_TIMEOUT: int = 40
    
    # Circuit Breaker
    CIRCUIT_FAILURE_THRESHOLD: int = 5
    CIRCUIT_COOLDOWN: int = 30
    
    # Cache
    CACHE_TTL: int = 86400
    
    # Models
    MODEL_LIST: List[str] = [
        "meta-llama/llama-4-scout-17b-16e-instruct",
        "llama-3.3-70b-versatile",
        "llama-3.1-8b-instant",
        "qwen/qwen3-32b",
        "deepseek-r1-distill-llama-70b",
        "mixtral-8x7b-32768"
    ]
    
    MODEL_NAMES: Dict[str, str] = {
        "meta-llama/llama-4-scout-17b-16e-instruct": "🦙 Llama 4 Scout",
        "llama-3.3-70b-versatile": "🦙 Llama 3.3 70B",
        "llama-3.1-8b-instant": "⚡ Llama 3.1 8B",
        "qwen/qwen3-32b": "🐉 Qwen 3 32B",
        "deepseek-r1-distill-llama-70b": "🔍 DeepSeek R1",
        "mixtral-8x7b-32768": "🧩 Mixtral 8x7B"
    }
    
    MODEL_PRICES: Dict[str, Dict[str, float]] = {
        "meta-llama/llama-4-scout-17b-16e-instruct": {"input": 0.11, "output": 0.34},
        "llama-3.3-70b-versatile": {"input": 0.59, "output": 0.79},
        "llama-3.1-8b-instant": {"input": 0.05, "output": 0.08},
        "qwen/qwen3-32b": {"input": 0.29, "output": 0.59},
        "deepseek-r1-distill-llama-70b": {"input": 0.50, "output": 0.50},
        "mixtral-8x7b-32768": {"input": 0.24, "output": 0.24}
    }
    
    TIER_LIMITS: Dict[str, int] = {"free": 30, "pro": 500, "ultra": 10000}
    TIER_NAMES: Dict[str, str] = {"free": "Бесплатный", "pro": "PRO", "ultra": "ULTRA"}
    TIER_PRICES: Dict[str, float] = {"pro": 3.0, "ultra": 10.0}
    
    # Support
    SUPPORT_URL: str = os.getenv("SUPPORT_URL", "https://t.me/llm_hub_support")
    CHANNEL_URL: str = os.getenv("CHANNEL_URL", "https://t.me/llm_hub")
    
    @classmethod
    def validate(cls) -> bool:
        if not cls.BOT_TOKEN:
            raise ValueError("BOT_TOKEN обязателен")
        if not cls.GROQ_API_KEY:
            raise ValueError("GROQ_API_KEY обязателен")
        if not cls.DATABASE_URL:
            raise ValueError("DATABASE_URL обязателен")
        return True

# ==================== ЛОГИРОВАНИЕ ====================
os.makedirs("logs", exist_ok=True)
file_handler = RotatingFileHandler("logs/bot.log", maxBytes=50*1024*1024, backupCount=10, encoding="utf-8")
file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(name)s - %(message)s'))
error_handler = RotatingFileHandler("logs/error.log", maxBytes=10*1024*1024, backupCount=5, encoding="utf-8")
error_handler.setLevel(logging.ERROR)
error_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(name)s - %(message)s'))
console_handler = logging.StreamHandler()
console_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))

logging.basicConfig(level=logging.INFO, handlers=[file_handler, error_handler, console_handler])
logger = logging.getLogger(__name__)

# ==================== КЛАССЫ ДАННЫХ ====================
class UserRole(Enum):
    FREE = "free"
    PRO = "pro"
    ULTRA = "ultra"
    ADMIN = "admin"

class RequestStatus(Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"

@dataclass
class UserData:
    user_id: int
    model: str
    tier: str
    is_banned: bool
    ban_reason: Optional[str]
    referrer_id: Optional[int]
    referral_code: Optional[str]
    created_at: datetime
    total_requests: int = 0
    total_tokens: int = 0

@dataclass
class ModelMetrics:
    total_requests: int = 0
    total_time: float = 0.0
    avg_time: float = 0.0
    success_count: int = 0
    error_count: int = 0
    total_tokens: int = 0
    
    @property
    def success_rate(self) -> float:
        total = self.total_requests
        if total == 0:
            return 100.0
        return (self.success_count / total) * 100
    
    def add_request(self, duration: float, success: bool, tokens: int = 0):
        self.total_requests += 1
        self.total_time += duration
        self.avg_time = self.total_time / self.total_requests
        self.total_tokens += tokens
        if success:
            self.success_count += 1
        else:
            self.error_count += 1

@dataclass
class Payment:
    id: int
    user_id: int
    amount: float
    tier: str
    status: str
    created_at: datetime
    completed_at: Optional[datetime]

# ==================== FSM СОСТОЯНИЯ ====================
class FormStates(StatesGroup):
    waiting_for_broadcast = State()
    waiting_for_ban_reason = State()
    waiting_for_export_format = State()
    waiting_for_feedback = State()
    waiting_for_model_change = State()

# ==================== БЕЗОПАСНОСТЬ ====================
class SecurityManager:
    FORBIDDEN_NETWORKS = [
        ipaddress.ip_network("127.0.0.0/8"),
        ipaddress.ip_network("10.0.0.0/8"),
        ipaddress.ip_network("172.16.0.0/12"),
        ipaddress.ip_network("192.168.0.0/16"),
        ipaddress.ip_network("169.254.0.0/16"),
        ipaddress.ip_network("0.0.0.0/8"),
        ipaddress.ip_network("224.0.0.0/4"),
        ipaddress.ip_network("240.0.0.0/4"),
    ]
    
    @classmethod
    def is_safe_url(cls, url: str) -> bool:
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            host = parsed.hostname
            if not host:
                return False
            if host in ['localhost', '127.0.0.1', '0.0.0.0', '::1']:
                return False
            try:
                ip = ipaddress.ip_address(host)
                for network in cls.FORBIDDEN_NETWORKS:
                    if ip in network:
                        return False
            except ValueError:
                pass
            return True
        except:
            return False
    
    @classmethod
    def validate_file_size(cls, file_size: int, max_size: int = Config.MAX_FILE_SIZE) -> bool:
        return file_size <= max_size
    
    @classmethod
    def sanitize_input(cls, text: str, max_length: int = Config.MAX_MESSAGE_LEN) -> str:
        if not text:
            return ""
        text = re.sub(r'[<>{}]', '', text)
        text = re.sub(r'[^\w\s\.,!?\-:;()\[\]@#$%^&*+=/\\|`~]', '', text)
        if len(text) > max_length:
            text = text[:max_length]
        return text.strip()
    
    @classmethod
    def validate_email(cls, email: str) -> bool:
        pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        return re.match(pattern, email) is not None

# ==================== БАЗА ДАННЫХ ====================
class DatabaseManager:
    def __init__(self):
        self.pool: Optional[Pool] = None
        self._lock = asyncio.Lock()
    
    async def init(self, dsn: str) -> None:
        async with self._lock:
            self.pool = await asyncpg.create_pool(
                dsn,
                min_size=2,
                max_size=20,
                command_timeout=30,
                max_queries=50000,
                max_inactive_connection_lifetime=300
            )
            db_connections.set(self.pool.get_size() if 'db_connections' in globals() else 0)
            await self._create_tables()
            logger.info("PostgreSQL подключён")
    
    async def _create_tables(self) -> None:
        async with self.pool.acquire() as conn:
            # Users table
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY,
                    model TEXT DEFAULT 'meta-llama/llama-4-scout-17b-16e-instruct',
                    tier TEXT DEFAULT 'free',
                    is_banned BOOLEAN DEFAULT FALSE,
                    ban_reason TEXT,
                    referrer_id BIGINT,
                    referral_code TEXT UNIQUE,
                    created_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW()
                )
            ''')
            
            # Add missing columns
            await conn.execute('''
                DO $$
                BEGIN
                    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                                   WHERE table_name='users' AND column_name='updated_at') THEN
                        ALTER TABLE users ADD COLUMN updated_at TIMESTAMP DEFAULT NOW();
                    END IF;
                    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                                   WHERE table_name='users' AND column_name='referrer_id') THEN
                        ALTER TABLE users ADD COLUMN referrer_id BIGINT;
                    END IF;
                    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                                   WHERE table_name='users' AND column_name='referral_code') THEN
                        ALTER TABLE users ADD COLUMN referral_code TEXT UNIQUE;
                    END IF;
                END $$;
            ''')
            
            # History table
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS history (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT,
                    role TEXT,
                    content TEXT,
                    tokens_used INT DEFAULT 0,
                    model TEXT,
                    timestamp TIMESTAMP DEFAULT NOW()
                )
            ''')
            
            # Usage table
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS usage (
                    user_id BIGINT,
                    date DATE,
                    count INT DEFAULT 0,
                    PRIMARY KEY (user_id, date)
                )
            ''')
            
            # Referrals table
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS referrals (
                    user_id BIGINT PRIMARY KEY,
                    invited_count INT DEFAULT 0,
                    bonus_balance INT DEFAULT 0,
                    total_earned INT DEFAULT 0
                )
            ''')
            
            # Payments table
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS payments (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT,
                    amount DECIMAL(10,2),
                    tier TEXT,
                    status TEXT DEFAULT 'pending',
                    created_at TIMESTAMP DEFAULT NOW(),
                    completed_at TIMESTAMP
                )
            ''')
            
            # Feedback table
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS feedback (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT,
                    message TEXT,
                    rating INT,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            ''')
            
            # Indexes
            await conn.execute('CREATE INDEX IF NOT EXISTS idx_history_user_id ON history(user_id, timestamp DESC)')
            await conn.execute('CREATE INDEX IF NOT EXISTS idx_usage_date ON usage(date)')
            await conn.execute('CREATE INDEX IF NOT EXISTS idx_users_referral_code ON users(referral_code)')
            await conn.execute('CREATE INDEX IF NOT EXISTS idx_payments_user_id ON payments(user_id)')
            await conn.execute('CREATE INDEX IF NOT EXISTS idx_feedback_created_at ON feedback(created_at)')
            
            logger.info("Таблицы созданы/проверены")
    
    async def close(self) -> None:
        if self.pool:
            await self.pool.close()
            logger.info("PostgreSQL пул закрыт")
    
    async def create_user(self, user_id: int, referral_code: str = None) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute('INSERT INTO users (user_id) VALUES ($1) ON CONFLICT DO NOTHING', user_id)
            await conn.execute('INSERT INTO referrals (user_id) VALUES ($1) ON CONFLICT DO NOTHING', user_id)
            
            if referral_code:
                referrer = await conn.fetchrow('SELECT user_id FROM users WHERE referral_code = $1', referral_code)
                if referrer and referrer['user_id'] != user_id:
                    existing = await conn.fetchval('SELECT referrer_id FROM users WHERE user_id = $1', user_id)
                    if not existing:
                        await conn.execute('UPDATE users SET referrer_id = $1 WHERE user_id = $2', referrer['user_id'], user_id)
                        await conn.execute('UPDATE referrals SET bonus_balance = bonus_balance + $1 WHERE user_id = $2', Config.REFERRAL_BONUS, referrer['user_id'])
                        await conn.execute('UPDATE referrals SET invited_count = invited_count + 1 WHERE user_id = $1', referrer['user_id'])
    
    async def get_user(self, user_id: int) -> Optional[UserData]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('SELECT * FROM users WHERE user_id = $1', user_id)
            if row:
                return UserData(
                    user_id=row['user_id'],
                    model=row['model'],
                    tier=row['tier'],
                    is_banned=row['is_banned'],
                    ban_reason=row.get('ban_reason'),
                    referrer_id=row.get('referrer_id'),
                    referral_code=row.get('referral_code'),
                    created_at=row['created_at']
                )
            return None
    
    async def get_user_model(self, user_id: int) -> str:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('SELECT model FROM users WHERE user_id = $1', user_id)
            return row[0] if row else Config.MODEL_LIST[0]
    
    async def update_user_model(self, user_id: int, model: str) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute('UPDATE users SET model = $1, updated_at = NOW() WHERE user_id = $2', model, user_id)
    
    async def add_history(self, user_id: int, role: str, content: str, model: str = None, tokens: int = 0) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute('INSERT INTO history (user_id, role, content, model, tokens_used) VALUES ($1, $2, $3, $4, $5)', 
                              user_id, role, content[:4000], model, tokens)
            await conn.execute('DELETE FROM history WHERE id IN (SELECT id FROM history WHERE user_id = $1 ORDER BY timestamp DESC OFFSET $2)', 
                              user_id, Config.MAX_HISTORY)
    
    async def get_history(self, user_id: int, limit: int = 20) -> List[Dict[str, str]]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch('SELECT role, content FROM history WHERE user_id = $1 ORDER BY timestamp ASC LIMIT $2', user_id, limit)
            return [{"role": r[0], "content": r[1]} for r in rows]
    
    async def clear_history(self, user_id: int) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute('DELETE FROM history WHERE user_id = $1', user_id)
    
    async def get_history_count(self, user_id: int) -> int:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('SELECT COUNT(*) FROM history WHERE user_id = $1', user_id)
            return row[0] if row else 0
    
    async def get_user_tier(self, user_id: int) -> str:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('SELECT tier FROM users WHERE user_id = $1', user_id)
            return row[0] if row else "free"
    
    async def set_user_tier(self, user_id: int, tier: str) -> None:
        if tier not in Config.TIER_LIMITS:
            return
        async with self.pool.acquire() as conn:
            await conn.execute('UPDATE users SET tier = $1 WHERE user_id = $2', tier, user_id)
    
    async def get_usage_stats(self, user_id: int) -> Tuple[str, int, int, int]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('SELECT tier FROM users WHERE user_id = $1', user_id)
            tier = row[0] if row else "free"
            limit = Config.TIER_LIMITS.get(tier, 30)
            today = datetime.now().strftime("%Y-%m-%d")
            row = await conn.fetchrow('SELECT count FROM usage WHERE user_id = $1 AND date = $2', user_id, today)
            used = row[0] if row else 0
            return tier, used, max(0, limit - used), limit
    
    async def increment_usage(self, user_id: int) -> Tuple[bool, int]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('SELECT tier FROM users WHERE user_id = $1', user_id)
            tier = row[0] if row else "free"
            limit = Config.TIER_LIMITS.get(tier, 30)
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
    
    async def is_banned(self, user_id: int) -> Tuple[bool, str]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('SELECT is_banned, ban_reason FROM users WHERE user_id = $1', user_id)
            if row and row['is_banned']:
                return True, row['ban_reason'] or "Нарушение правил"
            return False, ""
    
    async def ban_user(self, user_id: int, reason: str = "Нарушение правил") -> None:
        async with self.pool.acquire() as conn:
            await conn.execute('UPDATE users SET is_banned = TRUE, ban_reason = $1 WHERE user_id = $2', reason, user_id)
        logger.info(f"Пользователь {user_id} забанен: {reason}")
    
    async def unban_user(self, user_id: int) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute('UPDATE users SET is_banned = FALSE, ban_reason = NULL WHERE user_id = $1', user_id)
        logger.info(f"Пользователь {user_id} разбанен")
    
    async def get_total_users(self) -> int:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('SELECT COUNT(*) FROM users')
            return row[0] if row else 0
    
    async def get_banned_count(self) -> int:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('SELECT COUNT(*) FROM users WHERE is_banned = TRUE')
            return row[0] if row else 0
    
    async def get_today_active_users(self) -> int:
        today = datetime.now().strftime("%Y-%m-%d")
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('SELECT COUNT(DISTINCT user_id) FROM usage WHERE date = $1', today)
            return row[0] if row else 0
    
    async def get_today_requests(self) -> int:
        today = datetime.now().strftime("%Y-%m-%d")
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('SELECT SUM(count) FROM usage WHERE date = $1', today)
            return row[0] if row else 0
    
    async def get_week_stats(self) -> Dict[str, int]:
        week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('SELECT SUM(count) FROM usage WHERE date >= $1', week_ago)
            return {"week_requests": row[0] if row[0] else 0}
    
    async def get_referral_info(self, user_id: int) -> Tuple[int, int]:
        async with self.pool.acquire() as conn:
            invited = await conn.fetchval('SELECT COUNT(*) FROM users WHERE referrer_id = $1', user_id) or 0
            bonus = await conn.fetchval('SELECT bonus_balance FROM referrals WHERE user_id = $1', user_id) or 0
            return invited, bonus
    
    async def get_or_create_referral_code(self, user_id: int) -> str:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('SELECT referral_code FROM users WHERE user_id = $1', user_id)
            if row and row['referral_code']:
                return row['referral_code']
            code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
            await conn.execute('UPDATE users SET referral_code = $1 WHERE user_id = $2', code, user_id)
            return code
    
    async def cleanup_old_history(self) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute('DELETE FROM history WHERE timestamp < NOW() - INTERVAL \'30 days\'')
            logger.info("Очистка старой истории выполнена")
    
    async def get_users_list(self, limit: int = 100, offset: int = 0) -> List[dict]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch('SELECT user_id, tier, is_banned, created_at FROM users ORDER BY created_at DESC LIMIT $1 OFFSET $2', limit, offset)
            return [dict(row) for row in rows]
    
    async def save_feedback(self, user_id: int, message: str, rating: int = None) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute('INSERT INTO feedback (user_id, message, rating) VALUES ($1, $2, $3)', user_id, message[:1000], rating)
    
    async def get_payments(self, user_id: int = None, limit: int = 50) -> List[Payment]:
        async with self.pool.acquire() as conn:
            if user_id:
                rows = await conn.fetch('SELECT * FROM payments WHERE user_id = $1 ORDER BY created_at DESC LIMIT $2', user_id, limit)
            else:
                rows = await conn.fetch('SELECT * FROM payments ORDER BY created_at DESC LIMIT $1', limit)
            return [Payment(id=r['id'], user_id=r['user_id'], amount=float(r['amount']), 
                           tier=r['tier'], status=r['status'], created_at=r['created_at'], 
                           completed_at=r['completed_at']) for r in rows]

db = DatabaseManager()

# ==================== REDIS МЕНЕДЖЕР ====================
class RedisManager:
    def __init__(self):
        self.client: Optional[redis.Redis] = None
        self._enabled = False
    
    async def init(self, url: str) -> None:
        try:
            self.client = await redis.from_url(url, decode_responses=True)
            await self.client.ping()
            self._enabled = True
            logger.info("Redis подключён")
        except Exception as e:
            logger.warning(f"Redis недоступен: {e}, кэш и rate limit отключены")
            self._enabled = False
    
    async def close(self) -> None:
        if self.client:
            await self.client.close()
            logger.info("Redis закрыт")
    
    @property
    def enabled(self) -> bool:
        return self._enabled
    
    async def rate_limit(self, user_id: int, limit: int = 2, period: int = 1) -> bool:
        if not self._enabled:
            return True
        key = f"rl:{user_id}"
        current = await self.client.incr(key)
        if current == 1:
            await self.client.expire(key, period)
        return current <= limit
    
    async def deduplicate(self, user_id: int, text: str) -> bool:
        if not self._enabled or len(text) < 10:
            return False
        key = f"dup:{user_id}"
        last = await self.client.get(key)
        if last and last == text:
            return True
        await self.client.setex(key, 5, text)
        return False
    
    async def cache_get(self, key: str) -> Optional[str]:
        if not self._enabled:
            return None
        return await self.client.get(key)
    
    async def cache_set(self, key: str, value: str, ttl: int = Config.CACHE_TTL) -> None:
        if self._enabled:
            await self.client.setex(key, ttl, value)
    
    async def cache_delete(self, key: str) -> None:
        if self._enabled:
            await self.client.delete(key)
    
    async def get_user_state(self, user_id: int, state_key: str) -> Optional[dict]:
        if not self._enabled:
            return None
        key = f"state:{user_id}:{state_key}"
        data = await self.client.get(key)
        return json.loads(data) if data else None
    
    async def set_user_state(self, user_id: int, state_key: str, data: dict, ttl: int = 3600) -> None:
        if self._enabled:
            key = f"state:{user_id}:{state_key}"
            await self.client.setex(key, ttl, json.dumps(data))
    
    async def delete_user_state(self, user_id: int, state_key: str) -> None:
        if self._enabled:
            key = f"state:{user_id}:{state_key}"
            await self.client.delete(key)

redis_manager = RedisManager()

# ==================== GROQ API КЛИЕНТ ====================
class GroqClient:
    def __init__(self):
        self.session: Optional[ClientSession] = None
        self.api_key = Config.GROQ_API_KEY
        self.base_url = "https://api.groq.com/openai/v1"
        self._metrics: Dict[str, ModelMetrics] = {}
        self._circuit_breaker = {"failed": False, "failures": 0, "disabled_until": None}
    
    async def init(self) -> None:
        timeout = ClientTimeout(total=30, connect=10)
        self.session = ClientSession(timeout=timeout)
        logger.info("Groq клиент инициализирован")
    
    async def close(self) -> None:
        if self.session:
            await self.session.close()
            logger.info("Groq клиент закрыт")
    
    def _is_circuit_open(self) -> bool:
        if self._circuit_breaker["failed"]:
            if self._circuit_breaker["disabled_until"] and time.time() < self._circuit_breaker["disabled_until"]:
                return True
            self._circuit_breaker["failed"] = False
            self._circuit_breaker["failures"] = 0
        return False
    
    def _record_failure(self):
        self._circuit_breaker["failures"] += 1
        if self._circuit_breaker["failures"] >= Config.CIRCUIT_FAILURE_THRESHOLD:
            self._circuit_breaker["failed"] = True
            self._circuit_breaker["disabled_until"] = time.time() + Config.CIRCUIT_COOLDOWN
            logger.warning(f"Circuit breaker активирован на {Config.CIRCUIT_COOLDOWN} сек")
    
    def _record_success(self):
        self._circuit_breaker["failures"] = 0
    
    def _get_metrics(self, model: str) -> ModelMetrics:
        if model not in self._metrics:
            self._metrics[model] = ModelMetrics()
        return self._metrics[model]
    
    async def ask(self, model: str, messages: List[Dict], is_code: bool = False) -> Tuple[str, bool, int]:
        if self._is_circuit_open():
            return "Сервис временно недоступен. Попробуйте позже.", False, 0
        
        url = f"{self.base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        
        system_prompt = {
            "role": "system",
            "content": "Ты русскоязычный ассистент. Отвечай ТОЛЬКО на русском языке. Будь вежливым и полезным. Никогда не используй английский язык в ответах."
        }
        
        if is_code:
            system_prompt["content"] = "Ты профессиональный разработчик. Отвечай на русском языке. Код пиши на английском (синтаксис), но все пояснения на русском."
        
        temperature = 0.6 if "deepseek" in model else 0.5
        
        payload = {
            "model": model,
            "messages": [system_prompt] + messages,
            "temperature": temperature,
            "max_tokens": 2000
        }
        
        metrics = self._get_metrics(model)
        metrics.total_requests += 1
        
        for attempt in range(3):
            try:
                start_time = time.time()
                async with self.session.post(url, json=payload, headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        result = data["choices"][0]["message"]["content"]
                        tokens = data.get("usage", {}).get("total_tokens", 0)
                        duration = time.time() - start_time
                        metrics.add_request(duration, True, tokens)
                        self._record_success()
                        return result, True, tokens
                    elif resp.status == 429:
                        await asyncio.sleep(2 ** attempt)
                        continue
                    else:
                        break
            except asyncio.TimeoutError:
                await asyncio.sleep(2 ** attempt)
            except Exception as e:
                logger.error(f"Groq ошибка: {e}")
                break
        
        metrics.add_request(0, False, 0)
        self._record_failure()
        return "Сервис временно недоступен. Попробуйте позже.", False, 0
    
    async def ask_with_fallback(self, model: str, messages: List[Dict], is_code: bool = False) -> Tuple[str, str, int]:
        result, success, tokens = await self.ask(model, messages, is_code)
        if success:
            return result, model, tokens
        
        for fallback in Config.MODEL_LIST:
            if fallback == model:
                continue
            result, success, tokens = await self.ask(fallback, messages, is_code)
            if success:
                return f"[Переключено на {Config.MODEL_NAMES[fallback]}]\n\n{result}", fallback, tokens
        
        return "Сервис временно недоступен. Попробуйте позже.", "error", 0
    
    def get_metrics(self) -> Dict[str, dict]:
        return {
            model: {
                "total_requests": m.total_requests,
                "avg_time": round(m.avg_time, 2),
                "success_rate": round(m.success_rate, 1),
                "error_count": m.error_count,
                "total_tokens": m.total_tokens
            }
            for model, m in self._metrics.items()
        }

groq_client = GroqClient()

# ==================== CRYPTOBOT КЛИЕНТ ====================
class CryptoBotClient:
    def __init__(self):
        self.token = Config.CRYPTOBOT_TOKEN
        self.base_url = "https://pay.crypt.bot/api"
    
    async def create_invoice(self, user_id: int, tier: str) -> Optional[str]:
        if not self.token:
            return None
        
        amount = Config.TIER_PRICES.get(tier, 0)
        if amount == 0:
            return None
        
        url = f"{self.base_url}/createInvoice"
        headers = {"Crypto-Pay-API-Token": self.token, "Content-Type": "application/json"}
        payload = {
            "asset": "USDT",
            "amount": str(amount),
            "description": f"Повышение тарифа до {tier.upper()}",
            "payload": str(user_id),
            "expires_in": 3600
        }
        
        async with ClientSession() as session:
            try:
                async with session.post(url, json=payload, headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data.get("ok"):
                            return data["result"]["bot_invoice_url"]
            except Exception as e:
                logger.error(f"CryptoBot ошибка: {e}")
        return None

crypto_client = CryptoBotClient()

# ==================== ИНИЦИАЛИЗАЦИЯ БОТА ====================
bot = Bot(token=Config.BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# ==================== КЛАВИАТУРЫ ====================
class Keyboards:
    @staticmethod
    def main() -> InlineKeyboardMarkup:
        builder = InlineKeyboardBuilder()
        builder.button(text="👤 Личный кабинет", callback_data="profile")
        builder.button(text="🤖 Сменить модель", callback_data="show_models")
        builder.button(text="📊 Статистика", callback_data="stats")
        builder.button(text="🔗 Рефералы", callback_data="referral")
        builder.button(text="❓ Помощь", callback_data="help")
        if Config.CRYPTOBOT_TOKEN:
            builder.button(text="💎 Купить премиум", callback_data="premium")
        if Config.ADMIN_IDS:
            builder.button(text="👑 Админ панель", callback_data="admin_panel")
        builder.adjust(1)
        return builder.as_markup()
    
    @staticmethod
    def models() -> InlineKeyboardMarkup:
        builder = InlineKeyboardBuilder()
        for model_id in Config.MODEL_LIST:
            builder.button(text=Config.MODEL_NAMES[model_id], callback_data=f"model_{model_id}")
        builder.button(text="◀️ Назад", callback_data="back_to_main")
        builder.adjust(2)
        return builder.as_markup()
    
    @staticmethod
    def premium() -> InlineKeyboardMarkup:
        builder = InlineKeyboardBuilder()
        builder.button(text="⭐ PRO (500/день) - 3 USDT", callback_data="buy_pro")
        builder.button(text="👑 ULTRA (10000/день) - 10 USDT", callback_data="buy_ultra")
        builder.button(text="◀️ Назад", callback_data="back_to_main")
        builder.adjust(1)
        return builder.as_markup()
    
    @staticmethod
    def profile() -> InlineKeyboardMarkup:
        builder = InlineKeyboardBuilder()
        builder.button(text="💎 Купить премиум", callback_data="premium")
        builder.button(text="🔗 Реферальная ссылка", callback_data="show_ref")
        builder.button(text="📊 Мои запросы", callback_data="my_stats")
        builder.button(text="🗑 Очистить историю", callback_data="clear_history")
        builder.button(text="📤 Экспорт диалога", callback_data="export")
        builder.button(text="◀️ Назад", callback_data="back_to_main")
        builder.adjust(1)
        return builder.as_markup()
    
    @staticmethod
    def admin() -> InlineKeyboardMarkup:
        builder = InlineKeyboardBuilder()
        builder.button(text="📊 Общая статистика", callback_data="admin_stats")
        builder.button(text="👥 Пользователи", callback_data="admin_users")
        builder.button(text="✉️ Рассылка", callback_data="admin_broadcast")
        builder.button(text="🤖 Метрики моделей", callback_data="admin_metrics")
        builder.button(text="💸 Платежи", callback_data="admin_payments")
        builder.button(text="◀️ Назад", callback_data="back_to_main")
        builder.adjust(2)
        return builder.as_markup()
    
    @staticmethod
    def export() -> InlineKeyboardMarkup:
        builder = InlineKeyboardBuilder()
        builder.button(text="📄 TXT", callback_data="export_txt")
        builder.button(text="📦 JSON", callback_data="export_json")
        builder.button(text="◀️ Назад", callback_data="back_to_profile")
        builder.adjust(2)
        return builder.as_markup()

kb = Keyboards()

# ==================== УПРАВЛЕНИЕ ОЧЕРЕДЬЮ ====================
class RequestQueue:
    def __init__(self, maxsize: int = Config.QUEUE_MAX_SIZE, concurrency: int = Config.GLOBAL_CONCURRENCY):
        self.queue = asyncio.Queue(maxsize=maxsize)
        self.semaphore = asyncio.Semaphore(concurrency)
        self._workers = []
        self._running = False
    
    async def start(self, num_workers: int = Config.QUEUE_WORKERS):
        self._running = True
        for i in range(num_workers):
            worker = asyncio.create_task(self._worker(i))
            self._workers.append(worker)
        logger.info(f"Запущено {num_workers} воркеров")
    
    async def stop(self):
        self._running = False
        for worker in self._workers:
            worker.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        logger.info("Все воркеры остановлены")
    
    async def _worker(self, worker_id: int):
        while self._running:
            try:
                future, user_id, model, messages, is_code = await self.queue.get()
                async with self.semaphore:
                    try:
                        result, used_model, tokens = await groq_client.ask_with_fallback(model, messages, is_code)
                        future.set_result((result, used_model, tokens))
                    except Exception as e:
                        future.set_exception(e)
                    finally:
                        self.queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Воркер {worker_id} ошибка: {e}")
    
    async def add(self, user_id: int, model: str, messages: List[Dict], is_code: bool = False) -> Tuple[str, str, int]:
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        await self.queue.put((future, user_id, model, messages, is_code))
        return await future

request_queue = RequestQueue()

# ==================== АНТИСПАМ ====================
class SpamManager:
    def __init__(self):
        self.spam_tracker: Dict[int, List[float]] = {}
    
    def check(self, user_id: int) -> Tuple[bool, Optional[str]]:
        now = time.time()
        
        if user_id not in self.spam_tracker:
            self.spam_tracker[user_id] = []
        
        self.spam_tracker[user_id] = [t for t in self.spam_tracker[user_id] if now - t < Config.SPAM_WINDOW]
        self.spam_tracker[user_id].append(now)
        
        count = len(self.spam_tracker[user_id])
        
        if count >= Config.SPAM_THRESHOLD:
            return True, "Вы забанены за спам (50 запросов в минуту)"
        if count >= Config.SPAM_WARN_THRESHOLD:
            return False, "⚠️ Предупреждение: слишком много запросов!"
        return False, None

spam_manager = SpamManager()

# ==================== ЭКСПОРТ ДИАЛОГА ====================
class ExportService:
    @staticmethod
    async def export_txt(user_id: int) -> BufferedInputFile:
        history = await db.get_history(user_id, 100)
        lines = [f"{'Пользователь' if msg['role'] == 'user' else 'Ассистент'}:\n{msg['content']}\n{'-'*40}" for msg in history]
        data = "\n".join(lines).encode('utf-8')
        return BufferedInputFile(data, filename=f"dialog_{user_id}_{int(time.time())}.txt")
    
    @staticmethod
    async def export_json(user_id: int) -> BufferedInputFile:
        history = await db.get_history(user_id, 100)
        data = json.dumps(history, ensure_ascii=False, indent=2).encode('utf-8')
        return BufferedInputFile(data, filename=f"dialog_{user_id}_{int(time.time())}.json")

export_service = ExportService()

# ==================== ХЕНДЛЕРЫ КОМАНД ====================
@dp.message(Command("start"))
async def cmd_start(message: Message):
    user_id = message.from_user.id
    args = message.text.split()
    ref_code = args[1] if len(args) > 1 else None
    await db.create_user(user_id, ref_code)
    
    text = (
        f"🤖 **LLM Hub Bot v{__version__}**\n\n"
        f"Добро пожаловать! Я предоставляю доступ к передовым AI моделям.\n\n"
        f"**Доступные модели:**\n"
        + "\n".join([f"• {Config.MODEL_NAMES[m]}" for m in Config.MODEL_LIST]) +
        f"\n\n**Тарифы:**\n"
        f"• Бесплатный: {Config.TIER_LIMITS['free']} запросов/день\n"
        f"• PRO: {Config.TIER_LIMITS['pro']} запросов/день (3 USDT)\n"
        f"• ULTRA: {Config.TIER_LIMITS['ultra']} запросов/день (10 USDT)\n\n"
        f"Просто напишите сообщение, и я отвечу!"
    )
    await message.answer(text, parse_mode="Markdown", reply_markup=kb.main())

@dp.message(Command("admin"))
async def cmd_admin(message: Message):
    if message.from_user.id not in Config.ADMIN_IDS:
        await message.answer("⛔ Доступ запрещён")
        return
    await message.answer("👑 **Админ-панель**", parse_mode="Markdown", reply_markup=kb.admin())

@dp.message(Command("stats"))
async def cmd_stats(message: Message):
    user_id = message.from_user.id
    tier, used, remaining, limit = await db.get_usage_stats(user_id)
    history_count = await db.get_history_count(user_id)
    invited, bonus = await db.get_referral_info(user_id)
    model = await db.get_user_model(user_id)
    
    text = (
        f"📊 **Ваша статистика**\n\n"
        f"💎 Тариф: {Config.TIER_NAMES[tier]}\n"
        f"📈 Запросов сегодня: {used}/{limit}\n"
        f"✨ Осталось: {remaining}\n"
        f"💬 Всего сообщений: {history_count}\n"
        f"🤖 Модель: {Config.MODEL_NAMES[model]}\n"
        f"👥 Приглашено друзей: {invited}\n"
        f"🎁 Бонусов: {bonus}\n"
        f"🔄 Сброс: 00:00 UTC"
    )
    await message.answer(text, parse_mode="Markdown", reply_markup=kb.main())

@dp.message(Command("profile"))
async def cmd_profile(message: Message):
    await show_profile(message.from_user.id, message)

async def show_profile(user_id: int, message: Message):
    tier, used, remaining, limit = await db.get_usage_stats(user_id)
    history_count = await db.get_history_count(user_id)
    invited, bonus = await db.get_referral_info(user_id)
    model = await db.get_user_model(user_id)
    banned, ban_reason = await db.is_banned(user_id)
    
    text = (
        f"👤 **Личный кабинет**\n\n"
        f"💎 Тариф: {Config.TIER_NAMES[tier]}\n"
        f"📈 Запросов сегодня: {used}/{limit}\n"
        f"✨ Осталось: {remaining}\n"
        f"💬 Всего сообщений: {history_count}\n"
        f"🤖 Модель: {Config.MODEL_NAMES[model]}\n"
        f"👥 Рефералов: {invited} (бонусов: {bonus})"
    )
    if banned:
        text += f"\n\n🚫 Забанен: {ban_reason}"
    
    await message.answer(text, parse_mode="Markdown", reply_markup=kb.profile())

@dp.message(Command("clear"))
async def cmd_clear(message: Message):
    await db.clear_history(message.from_user.id)
    await message.answer("✅ История диалога очищена", reply_markup=kb.main())

@dp.message(Command("export"))
async def cmd_export(message: Message):
    await message.answer("📤 **Выберите формат экспорта:**", parse_mode="Markdown", reply_markup=kb.export())

@dp.message(Command("help"))
async def cmd_help(message: Message):
    text = (
        f"❓ **Помощь**\n\n"
        f"**Команды:**\n"
        f"/start - Начать\n"
        f"/profile - Личный кабинет\n"
        f"/stats - Статистика\n"
        f"/models - Список моделей\n"
        f"/clear - Очистить историю\n"
        f"/export - Экспорт диалога\n"
        f"/help - Помощь\n\n"
        f"**Поддержка:** {Config.SUPPORT_URL}\n"
        f"**Канал:** {Config.CHANNEL_URL}"
    )
    await message.answer(text, parse_mode="Markdown", reply_markup=kb.main())

@dp.message(Command("models"))
async def cmd_models(message: Message):
    await message.answer("🤖 **Выберите модель:**", parse_mode="Markdown", reply_markup=kb.models())

# ==================== CALLBACK ХЕНДЛЕРЫ ====================
@dp.callback_query()
async def handle_callback(call: CallbackQuery):
    user_id = call.from_user.id
    data = call.data
    
    if data.startswith("model_"):
        model = data.replace("model_", "")
        await db.update_user_model(user_id, model)
        await call.message.edit_text(f"✅ Модель изменена на: **{Config.MODEL_NAMES[model]}**", parse_mode="Markdown", reply_markup=kb.main())
        await call.answer()
        return
    
    if data == "show_models":
        await call.message.edit_text("🤖 **Выберите модель:**", parse_mode="Markdown", reply_markup=kb.models())
        await call.answer()
        return
    
    if data == "back_to_main":
        await call.message.edit_text("🤖 **Главное меню**", parse_mode="Markdown", reply_markup=kb.main())
        await call.answer()
        return
    
    if data == "back_to_profile":
        await show_profile(user_id, call.message)
        await call.answer()
        return
    
    if data == "profile":
        await show_profile(user_id, call.message)
        await call.answer()
        return
    
    if data == "show_ref":
        code = await db.get_or_create_referral_code(user_id)
        invited, bonus = await db.get_referral_info(user_id)
        text = (
            f"🔗 **Реферальная программа**\n\n"
            f"👥 Приглашено друзей: {invited}\n"
            f"🎁 Бонусов: {bonus}\n"
            f"💰 За друга: +{Config.REFERRAL_BONUS} запросов\n\n"
            f"**Ваша ссылка:**\n"
            f"<code>https://t.me/{Config.BOT_USERNAME}?start={code}</code>"
        )
        await call.message.edit_text(text, parse_mode="HTML", reply_markup=kb.profile())
        await call.answer()
        return
    
    if data == "my_stats":
        tier, used, remaining, limit = await db.get_usage_stats(user_id)
        history_count = await db.get_history_count(user_id)
        text = (
            f"📊 **Ваша статистика**\n\n"
            f"💎 Тариф: {Config.TIER_NAMES[tier]}\n"
            f"📈 Сегодня: {used}/{limit}\n"
            f"✨ Осталось: {remaining}\n"
            f"💬 Всего: {history_count}"
        )
        await call.message.edit_text(text, parse_mode="Markdown", reply_markup=kb.profile())
        await call.answer()
        return
    
    if data == "clear_history":
        await db.clear_history(user_id)
        await call.message.edit_text("✅ История диалога очищена", reply_markup=kb.profile())
        await call.answer()
        return
    
    if data == "premium":
        await call.message.edit_text("💎 **Выберите тариф:**", parse_mode="Markdown", reply_markup=kb.premium())
        await call.answer()
        return
    
    if data == "buy_pro":
        url = await crypto_client.create_invoice(user_id, "pro")
        if url:
            await call.message.edit_text(f"💎 **Оплата PRO:**\n{url}\n\nПосле оплаты тариф повысится автоматически.", parse_mode="Markdown", reply_markup=kb.main(), disable_web_page_preview=True)
        else:
            await call.message.edit_text("❌ Ошибка создания счёта", reply_markup=kb.main())
        await call.answer()
        return
    
    if data == "buy_ultra":
        url = await crypto_client.create_invoice(user_id, "ultra")
        if url:
            await call.message.edit_text(f"👑 **Оплата ULTRA:**\n{url}\n\nПосле оплаты тариф повысится автоматически.", parse_mode="Markdown", reply_markup=kb.main(), disable_web_page_preview=True)
        else:
            await call.message.edit_text("❌ Ошибка создания счёта", reply_markup=kb.main())
        await call.answer()
        return
    
    if data == "export":
        await call.message.edit_text("📤 **Выберите формат:**", parse_mode="Markdown", reply_markup=kb.export())
        await call.answer()
        return
    
    if data == "export_txt":
        file = await export_service.export_txt(user_id)
        await call.message.answer_document(file, caption="📄 Ваш диалог")
        await call.message.edit_text("👤 Личный кабинет", reply_markup=kb.profile())
        await call.answer()
        return
    
    if data == "export_json":
        file = await export_service.export_json(user_id)
        await call.message.answer_document(file, caption="📦 Ваш диалог в JSON")
        await call.message.edit_text("👤 Личный кабинет", reply_markup=kb.profile())
        await call.answer()
        return
    
    if data == "referral":
        code = await db.get_or_create_referral_code(user_id)
        invited, bonus = await db.get_referral_info(user_id)
        text = (
            f"🔗 **Реферальная программа**\n\n"
            f"👥 Приглашено: {invited}\n"
            f"🎁 Бонусов: {bonus}\n"
            f"💰 За друга: +{Config.REFERRAL_BONUS} запросов\n\n"
            f"<code>https://t.me/{Config.BOT_USERNAME}?start={code}</code>"
        )
        await call.message.edit_text(text, parse_mode="HTML", reply_markup=kb.main())
        await call.answer()
        return
    
    if data == "help":
        await cmd_help(call.message)
        await call.answer()
        return
    
    if data == "stats":
        await cmd_stats(call.message)
        await call.answer()
        return
    
    # Admin panel
    if user_id not in Config.ADMIN_IDS:
        await call.answer()
        return
    
    if data == "admin_panel":
        await call.message.edit_text("👑 **Админ-панель**", parse_mode="Markdown", reply_markup=kb.admin())
        await call.answer()
        return
    
    if data == "admin_stats":
        total = await db.get_total_users()
        banned = await db.get_banned_count()
        active = await db.get_today_active_users()
        requests = await db.get_today_requests()
        week = await db.get_week_stats()
        queue_size_val = request_queue.queue.qsize()
        
        text = (
            f"📊 **Статистика бота**\n\n"
            f"👥 Всего: {total}\n"
            f"🚫 Забанено: {banned}\n"
            f"👤 Активных сегодня: {active}\n"
            f"📈 Запросов сегодня: {requests}\n"
            f"📊 За неделю: {week.get('week_requests', 0)}\n"
            f"⏳ В очереди: {queue_size_val}\n"
            f"🧠 Воркеров: {Config.QUEUE_WORKERS}"
        )
        await call.message.edit_text(text, parse_mode="Markdown", reply_markup=kb.admin())
        await call.answer()
        return
    
    if data == "admin_users":
        users = await db.get_users_list(limit=30)
        text = "👥 **Последние пользователи**\n\n"
        for u in users:
            status = "🚫" if u['is_banned'] else "✅"
            text += f"{status} `{u['user_id']}` | {u['tier']} | {u['created_at'].strftime('%d.%m')}\n"
        await call.message.edit_text(text, parse_mode="Markdown", reply_markup=kb.admin())
        await call.answer()
        return
    
    if data == "admin_broadcast":
        await call.message.edit_text("✉️ **Рассылка**\n\nВведите текст для рассылки:", parse_mode="Markdown", reply_markup=kb.admin())
        await call.answer()
    
    if data == "admin_metrics":
        metrics = groq_client.get_metrics()
        if not metrics:
            text = "📊 Нет данных о метриках"
        else:
            text = "📊 **Метрики моделей**\n\n"
            for model, m in metrics.items():
                text += f"**{Config.MODEL_NAMES.get(model, model)}**\n📈 {m['total_requests']} | ⏱ {m['avg_time']}с | ✅ {m['success_rate']}%\n\n"
        await call.message.edit_text(text, parse_mode="Markdown", reply_markup=kb.admin())
        await call.answer()
        return
    
    if data == "admin_payments":
        payments = await db.get_payments(limit=20)
        if not payments:
            text = "💸 Нет платежей"
        else:
            text = "💸 **Последние платежи**\n\n"
            for p in payments:
                text += f"👤 {p.user_id} | {p.tier.upper()} | {p.amount} USDT | {p.status}\n"
        await call.message.edit_text(text, parse_mode="Markdown", reply_markup=kb.admin())
        await call.answer()
        return

# ==================== ОБРАБОТЧИК СООБЩЕНИЙ ====================
@dp.message()
async def handle_message(message: Message):
    user_id = message.from_user.id
    
    # Rate limit
    if not await redis_manager.rate_limit(user_id, Config.RATE_LIMIT_REQUESTS, Config.RATE_LIMIT_PERIOD):
        await message.answer("⏳ Слишком часто! Подождите 1 секунду.")
        return
    
    # Проверка бана
    banned, ban_reason = await db.is_banned(user_id)
    if banned:
        await message.answer(f"🚫 Вы забанены.\nПричина: {ban_reason}")
        return
    
    # Антиспам
    is_spam, spam_warning = spam_manager.check(user_id)
    if is_spam:
        await db.ban_user(user_id, "Автобан: превышение лимита")
        await message.answer("🚫 Вы забанены за спам")
        return
    elif spam_warning:
        await message.answer(spam_warning)
    
    # Получение текста
    user_text = message.text or message.caption
    if not user_text:
        return
    
    user_text = SecurityManager.sanitize_input(user_text)
    if not user_text:
        return
    
    # Дедупликация
    if await redis_manager.deduplicate(user_id, user_text):
        return
    
    # Проверка лимитов
    allowed, remaining = await db.increment_usage(user_id)
    if not allowed:
        tier = await db.get_user_tier(user_id)
        await message.answer(f"❌ Лимит {Config.TIER_NAMES[tier]} исчерпан на сегодня.")
        return
    
    # Получение истории
    history = await db.get_history(user_id, 20)
    await db.add_history(user_id, "user", user_text)
    history.append({"role": "user", "content": user_text})
    
    # Проверка кэша
    model = await db.get_user_model(user_id)
    cache_key = hashlib.md5(f"{model}:{user_text[:100]}".encode()).hexdigest()
    cached = await redis_manager.cache_get(cache_key)
    if cached:
        await message.answer(cached[:4000], reply_markup=kb.main())
        return
    
    # Проверка на код
    is_code = bool(re.search(r'(код|скрипт|программу|функцию|напиши код)', user_text, re.I))
    
    # Отправка запроса
    status_msg = await message.answer("🤔 Думаю...")
    
    try:
        reply, used_model, tokens = await asyncio.wait_for(
            request_queue.add(user_id, model, history, is_code),
            timeout=Config.REQUEST_TIMEOUT
        )
        await db.add_history(user_id, "assistant", reply, used_model, tokens)
        
        # Кэширование коротких ответов
        if len(user_text) < 100 and len(reply) < 500:
            await redis_manager.cache_set(cache_key, reply[:1000])
        
    except asyncio.TimeoutError:
        reply = "⏳ Превышено время ожидания. Попробуйте позже."
    except Exception as e:
        logger.error(f"Ошибка: {e}")
        reply = "❌ Произошла ошибка. Попробуйте позже."
    
    await status_msg.delete()
    await message.answer(reply[:4000], reply_markup=kb.main())
    
    if remaining <= 5:
        await message.answer(f"⚠️ Осталось запросов сегодня: {remaining}")

# ==================== ВЕБ-СЕРВЕР ====================
async def health_check(request: web.Request) -> web.Response:
    status = {"status": "ok", "version": __version__, "timestamp": datetime.now().isoformat()}
    try:
        await db.get_total_users()
        status["postgresql"] = "ok"
    except Exception as e:
        status["postgresql"] = str(e)
        status["status"] = "degraded"
    status["queue_size"] = request_queue.queue.qsize()
    return web.Response(text=json.dumps(status, ensure_ascii=False), content_type="application/json")

async def crypto_webhook(request: web.Request) -> web.Response:
    try:
        data = await request.json()
        if data.get("update_type") == "invoice_paid":
            payload = data.get("payload", {})
            user_id = int(payload.get("payload", 0))
            amount = payload.get("paid_amount")
            if user_id:
                tier = "pro" if amount == "3.00" else "ultra" if amount == "10.00" else None
                if tier:
                    await db.set_user_tier(user_id, tier)
                    await bot.send_message(user_id, f"✅ Тариф повышен до {tier.upper()}!")
    except Exception as e:
        logger.error(f"Crypto webhook ошибка: {e}")
    return web.Response(text="OK")

# ==================== ЗАПУСК ====================
async def scheduled_cleanup():
    while True:
        now = datetime.now()
        next_run = now.replace(hour=3, minute=0, second=0, microsecond=0)
        if now >= next_run:
            next_run += timedelta(days=1)
        await asyncio.sleep((next_run - now).total_seconds())
        await db.cleanup_old_history()

async def on_startup():
    logger.info(f"🚀 Запуск LLM Hub Bot v{__version__}")
    await db.init(Config.DATABASE_URL)
    await redis_manager.init(Config.REDIS_URL)
    await groq_client.init()
    await request_queue.start()
    asyncio.create_task(scheduled_cleanup())
    
    app = web.Application()
    app.router.add_get("/health", health_check)
    app.router.add_post("/crypto", crypto_webhook)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", Config.PORT).start()
    
    logger.info(f"✅ Бот запущен | Порт: {Config.PORT} | Workers: {Config.QUEUE_WORKERS}")

async def on_shutdown():
    logger.info("🛑 Остановка бота...")
    await request_queue.stop()
    await groq_client.close()
    await redis_manager.close()
    await db.close()
    logger.info("✅ Бот остановлен")

async def main():
    try:
        Config.validate()
        await on_startup()
        await dp.start_polling(bot, on_shutdown=on_shutdown)
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
