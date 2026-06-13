#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LLM Hub Bot - Production Grade Telegram Bot
Version: 3.0.0
Author: LLM Hub Team
License: Proprietary
"""

import asyncio
import os
import sys
import json
import hashlib
import hmac
import base64
import string
import random
import logging
import time
import signal
import ipaddress
import re
import traceback
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from typing import Optional, Tuple, Dict, List, Any, Union
from dataclasses import dataclass, field
from enum import Enum
from functools import wraps
from contextlib import asynccontextmanager
import secrets

from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton, 
    BufferedInputFile, Message, CallbackQuery,
    Chat, User, Document, Voice, ErrorEvent
)
from aiogram.exceptions import (
    TelegramBadRequest, TelegramRetryAfter,
    TelegramForbiddenError, TelegramNetworkError
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
import aiohttp
from aiohttp import web, ClientTimeout, ClientSession
import asyncpg
from asyncpg.pool import Pool
import redis.asyncio as redis
from prometheus_client import Counter, Gauge, Histogram, generate_latest, CONTENT_TYPE_LATEST
import sentry_sdk
from sentry_sdk.integrations.asyncio import AsyncioIntegration

load_dotenv()

# ==================== ВЕРСИЯ И ИНФО ====================
__version__ = "3.0.0"
__author__ = "LLM Hub Team"
__license__ = "Proprietary"

# ==================== КОНФИГУРАЦИЯ ====================
class Config:
    """Централизованная конфигурация приложения"""
    
    # Telegram
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
    ADMIN_IDS: List[int] = [int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]
    
    # API Keys
    GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
    CRYPTOBOT_TOKEN: str = os.getenv("CRYPTOBOT_TOKEN", "")
    
    # Database
    DATABASE_URL: str = os.getenv("DATABASE_URL", "")
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379")
    
    # Security
    CRYPTOBOT_SECRET: Optional[str] = os.getenv("CRYPTOBOT_SECRET")
    ADMIN_SECRET: str = os.getenv("ADMIN_SECRET", secrets.token_urlsafe(32))
    SENTRY_DSN: Optional[str] = os.getenv("SENTRY_DSN")
    
    # Server
    PORT: int = int(os.environ.get("PORT", 10000))
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development")
    
    # Limits
    MAX_MESSAGE_LEN: int = 8000
    MAX_FILE_SIZE: int = 20 * 1024 * 1024
    MAX_HISTORY: int = 100
    REFERRAL_BONUS: int = 20
    
    # Rate Limits
    RATE_LIMIT_REQUESTS: int = 2
    RATE_LIMIT_PERIOD: int = 1
    SPAM_THRESHOLD: int = 50
    SPAM_WINDOW: int = 60
    SPAM_WARN_THRESHOLD: int = 30
    
    # Queue
    QUEUE_MAX_SIZE: int = 200
    QUEUE_WORKERS: int = 20
    GLOBAL_CONCURRENCY: int = 20
    REQUEST_TIMEOUT: int = 40
    
    # Circuit Breaker
    CIRCUIT_FAILURE_THRESHOLD: int = 5
    CIRCUIT_COOLDOWN: int = 30
    
    # Cache
    CACHE_TTL: int = 86400
    HISTORY_TTL: int = 86400
    
    # Models
    MODEL_LIST: List[str] = [
        "meta-llama/llama-4-scout-17b-16e-instruct",
        "llama-3.3-70b-versatile",
        "llama-3.1-8b-instant",
        "qwen/qwen3-32b",
        "openai/gpt-oss-20b",
        "openai/gpt-oss-120b",
    ]
    
    MODEL_NAMES: Dict[str, str] = {
        "meta-llama/llama-4-scout-17b-16e-instruct": "Llama 4 Scout",
        "llama-3.3-70b-versatile": "Llama 3.3 70B",
        "llama-3.1-8b-instant": "Llama 3.1 8B",
        "qwen/qwen3-32b": "Qwen 3 32B",
        "openai/gpt-oss-20b": "GPT-OSS 20B",
        "openai/gpt-oss-120b": "GPT-OSS 120B",
    }
    
    TIER_LIMITS: Dict[str, int] = {"free": 30, "pro": 500, "ultra": 10000}
    TIER_NAMES: Dict[str, str] = {"free": "Бесплатный", "pro": "PRO", "ultra": "ULTRA"}
    
    # Проверка обязательных переменных
    @classmethod
    def validate(cls) -> bool:
        if not cls.BOT_TOKEN:
            raise ValueError("BOT_TOKEN обязателен")
        if not cls.GROQ_API_KEY:
            raise ValueError("GROQ_API_KEY обязателен")
        if not cls.DATABASE_URL:
            raise ValueError("DATABASE_URL обязателен")
        return True

# ==================== SENTRY ====================
if Config.SENTRY_DSN:
    sentry_sdk.init(
        dsn=Config.SENTRY_DSN,
        integrations=[AsyncioIntegration()],
        traces_sample_rate=0.1 if Config.ENVIRONMENT == "production" else 1.0,
        environment=Config.ENVIRONMENT,
        release=f"llm-hub-bot@{__version__}"
    )
    logging.info("Sentry инициализирован")

# ==================== ПРОМЕТЕУС МЕТРИКИ ====================
# Счётчики запросов
requests_total = Counter('bot_requests_total', 'Total requests', ['model', 'status', 'endpoint'])
errors_total = Counter('bot_errors_total', 'Total errors', ['error_type', 'handler'])
active_users = Gauge('bot_active_users', 'Active users today')
queue_size = Gauge('bot_queue_size', 'Current queue size')
db_connections = Gauge('bot_db_connections', 'Database pool size')
redis_connections = Gauge('bot_redis_connections', 'Redis connection status')
response_time = Histogram('bot_response_time_seconds', 'Response time', ['model', 'endpoint'])

# ==================== ЛОГИРОВАНИЕ ====================
class Logger:
    """Централизованное логирование с ротацией"""
    
    _instance = None
    _initialized = False
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._setup_logging()
    
    def _setup_logging(self):
        os.makedirs("logs", exist_ok=True)
        
        # Основной лог-файл
        main_handler = RotatingFileHandler(
            "logs/bot.log",
            maxBytes=50*1024*1024,
            backupCount=10,
            encoding="utf-8"
        )
        main_handler.setFormatter(logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s'
        ))
        
        # Лог ошибок
        error_handler = RotatingFileHandler(
            "logs/error.log",
            maxBytes=50*1024*1024,
            backupCount=10,
            encoding="utf-8"
        )
        error_handler.setLevel(logging.ERROR)
        error_handler.setFormatter(logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        ))
        
        # Лог API вызовов
        api_handler = RotatingFileHandler(
            "logs/api.log",
            maxBytes=50*1024*1024,
            backupCount=5,
            encoding="utf-8"
        )
        api_handler.setFormatter(logging.Formatter(
            '%(asctime)s - %(message)s'
        ))
        
        # Консольный вывод
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
        
        # Настройка корневого логгера
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.INFO)
        root_logger.addHandler(main_handler)
        root_logger.addHandler(error_handler)
        root_logger.addHandler(console_handler)
        
        # API логгер
        self.api_logger = logging.getLogger("api")
        self.api_logger.addHandler(api_handler)
        self.api_logger.setLevel(logging.INFO)
        
        logging.info(f"Логирование настроено. Версия: {__version__}, Окружение: {Config.ENVIRONMENT}")

logger = Logger()

# ==================== МОДЕЛИ ДАННЫХ ====================
class UserRole(Enum):
    """Роли пользователей"""
    FREE = "free"
    PRO = "pro"
    ULTRA = "ultra"
    ADMIN = "admin"
    OWNER = "owner"

class RequestStatus(Enum):
    """Статусы запросов"""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"

@dataclass
class UserData:
    """Данные пользователя"""
    user_id: int
    model: str
    tier: str
    is_banned: bool
    ban_reason: Optional[str]
    referrer_id: Optional[int]
    referral_code: Optional[str]
    created_at: datetime
    
    @classmethod
    def from_row(cls, row: dict) -> "UserData":
        return cls(
            user_id=row['user_id'],
            model=row['model'],
            tier=row['tier'],
            is_banned=row['is_banned'],
            ban_reason=row.get('ban_reason'),
            referrer_id=row.get('referrer_id'),
            referral_code=row.get('referral_code'),
            created_at=row['created_at']
        )

@dataclass
class ModelMetrics:
    """Метрики модели"""
    total_requests: int = 0
    total_time: float = 0.0
    avg_time: float = 0.0
    success_count: int = 0
    error_count: int = 0
    
    @property
    def success_rate(self) -> float:
        total = self.total_requests
        if total == 0:
            return 100.0
        return (self.success_count / total) * 100
    
    def add_request(self, duration: float, success: bool):
        self.total_requests += 1
        self.total_time += duration
        self.avg_time = self.total_time / self.total_requests
        if success:
            self.success_count += 1
        else:
            self.error_count += 1

# ==================== FSМ СОСТОЯНИЯ ====================
class FormStates(StatesGroup):
    """Состояния для форм"""
    waiting_for_broadcast = State()
    waiting_for_ban_reason = State()
    waiting_for_export_format = State()
    waiting_for_image_prompt = State()

# ==================== БЕЗОПАСНОСТЬ ====================
class SecurityManager:
    """Менеджер безопасности"""
    
    # Запрещённые IP для SSRF защиты
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
        """Проверка URL на безопасность (защита от SSRF)"""
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            host = parsed.hostname
            
            if not host:
                return False
            
            # Проверка localhost
            if host in ['localhost', '127.0.0.1', '0.0.0.0', '::1']:
                return False
            
            # Проверка внутренних IP
            try:
                ip = ipaddress.ip_address(host)
                for network in cls.FORBIDDEN_NETWORKS:
                    if ip in network:
                        return False
            except ValueError:
                # Не IP адрес, пропускаем
                pass
            
            return True
        except:
            return False
    
    @classmethod
    def validate_file_size(cls, file_size: int, max_size: int = Config.MAX_FILE_SIZE) -> bool:
        """Проверка размера файла"""
        return file_size <= max_size
    
    @classmethod
    def sanitize_input(cls, text: str, max_length: int = Config.MAX_MESSAGE_LEN) -> str:
        """Очистка пользовательского ввода"""
        if not text:
            return ""
        # Удаляем опасные символы
        text = re.sub(r'[<>{}]', '', text)
        # Ограничиваем длину
        if len(text) > max_length:
            text = text[:max_length]
        return text.strip()

# ==================== БАЗА ДАННЫХ ====================
class DatabaseManager:
    """Менеджер базы данных PostgreSQL"""
    
    def __init__(self):
        self.pool: Optional[Pool] = None
        self._lock = asyncio.Lock()
    
    async def init(self, dsn: str) -> None:
        """Инициализация пула соединений"""
        async with self._lock:
            self.pool = await asyncpg.create_pool(
                dsn,
                min_size=2,
                max_size=20,
                command_timeout=30,
                max_queries=50000,
                max_inactive_connection_lifetime=300
            )
            db_connections.set(self.pool.get_size())
            await self._create_tables()
            logging.info("PostgreSQL подключён")
    
    async def _create_tables(self) -> None:
        """Создание всех необходимых таблиц"""
        async with self.pool.acquire() as conn:
            # Таблица пользователей
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
            
            # Добавляем недостающие колонки
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
            
            # Таблица истории
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
            
            await conn.execute('''
                DO $$
                BEGIN
                    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                                   WHERE table_name='history' AND column_name='tokens_used') THEN
                        ALTER TABLE history ADD COLUMN tokens_used INT DEFAULT 0;
                    END IF;
                    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                                   WHERE table_name='history' AND column_name='model') THEN
                        ALTER TABLE history ADD COLUMN model TEXT;
                    END IF;
                END $$;
            ''')
            
            # Таблица использования
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS usage (
                    user_id BIGINT,
                    date DATE,
                    count INT DEFAULT 0,
                    PRIMARY KEY (user_id, date)
                )
            ''')
            
            # Таблица рефералов
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS referrals (
                    user_id BIGINT PRIMARY KEY,
                    invited_count INT DEFAULT 0,
                    bonus_balance INT DEFAULT 0,
                    total_earned INT DEFAULT 0
                )
            ''')
            
            # Таблица платежей
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
            
            # Индексы
            await conn.execute('CREATE INDEX IF NOT EXISTS idx_history_user_id ON history(user_id, timestamp DESC)')
            await conn.execute('CREATE INDEX IF NOT EXISTS idx_usage_date ON usage(date)')
            await conn.execute('CREATE INDEX IF NOT EXISTS idx_users_referral_code ON users(referral_code)')
            await conn.execute('CREATE INDEX IF NOT EXISTS idx_payments_user_id ON payments(user_id)')
            
            logging.info("Таблицы созданы/проверены")
    
    async def close(self) -> None:
        """Закрытие пула соединений"""
        if self.pool:
            await self.pool.close()
            logging.info("PostgreSQL пул закрыт")
    
    async def get_user(self, user_id: int) -> Optional[UserData]:
        """Получение данных пользователя"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('SELECT * FROM users WHERE user_id = $1', user_id)
            if row:
                return UserData.from_row(dict(row))
            return None
    
    async def create_user(self, user_id: int, referral_code: str = None) -> UserData:
        """Создание нового пользователя"""
        async with self.pool.acquire() as conn:
            # Создаём пользователя
            await conn.execute('''
                INSERT INTO users (user_id) VALUES ($1)
                ON CONFLICT (user_id) DO NOTHING
            ''', user_id)
            
            await conn.execute('''
                INSERT INTO referrals (user_id) VALUES ($1)
                ON CONFLICT (user_id) DO NOTHING
            ''', user_id)
            
            # Обработка реферала
            if referral_code:
                referrer = await conn.fetchrow(
                    'SELECT user_id FROM users WHERE referral_code = $1', 
                    referral_code
                )
                if referrer and referrer['user_id'] != user_id:
                    existing = await conn.fetchval(
                        'SELECT referrer_id FROM users WHERE user_id = $1', 
                        user_id
                    )
                    if not existing:
                        await conn.execute('''
                            UPDATE users SET referrer_id = $1 WHERE user_id = $2
                        ''', referrer['user_id'], user_id)
                        
                        await conn.execute('''
                            UPDATE referrals SET bonus_balance = bonus_balance + $1 
                            WHERE user_id = $2
                        ''', Config.REFERRAL_BONUS, referrer['user_id'])
                        
                        await conn.execute('''
                            UPDATE referrals SET invited_count = invited_count + 1 
                            WHERE user_id = $1
                        ''', referrer['user_id'])
            
            return await self.get_user(user_id)
    
    async def update_user_model(self, user_id: int, model: str) -> None:
        """Обновление модели пользователя"""
        async with self.pool.acquire() as conn:
            await conn.execute('''
                UPDATE users SET model = $1, updated_at = NOW()
                WHERE user_id = $2
            ''', model, user_id)
    
    async def get_user_model(self, user_id: int) -> str:
        """Получение текущей модели пользователя"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('SELECT model FROM users WHERE user_id = $1', user_id)
            return row[0] if row else Config.MODEL_LIST[0]
    
    async def add_history(self, user_id: int, role: str, content: str, model: str = None) -> None:
        """Добавление записи в историю"""
        async with self.pool.acquire() as conn:
            await conn.execute('''
                INSERT INTO history (user_id, role, content, model)
                VALUES ($1, $2, $3, $4)
            ''', user_id, role, content[:4000], model)
            
            # Ограничиваем историю
            await conn.execute('''
                DELETE FROM history 
                WHERE id IN (
                    SELECT id FROM history 
                    WHERE user_id = $1 
                    ORDER BY timestamp DESC 
                    OFFSET $2
                )
            ''', user_id, Config.MAX_HISTORY)
    
    async def get_history(self, user_id: int, limit: int = 20) -> List[Dict[str, str]]:
        """Получение истории диалога"""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch('''
                SELECT role, content FROM history 
                WHERE user_id = $1 
                ORDER BY timestamp ASC 
                LIMIT $2
            ''', user_id, limit)
            return [{"role": r[0], "content": r[1]} for r in rows]
    
    async def clear_history(self, user_id: int) -> None:
        """Очистка истории пользователя"""
        async with self.pool.acquire() as conn:
            await conn.execute('DELETE FROM history WHERE user_id = $1', user_id)
    
    async def get_history_count(self, user_id: int) -> int:
        """Количество сообщений в истории"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('SELECT COUNT(*) FROM history WHERE user_id = $1', user_id)
            return row[0] if row else 0
    
    async def get_user_tier(self, user_id: int) -> str:
        """Получение тарифа пользователя"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('SELECT tier FROM users WHERE user_id = $1', user_id)
            return row[0] if row else "free"
    
    async def set_user_tier(self, user_id: int, tier: str) -> None:
        """Установка тарифа пользователя"""
        if tier not in Config.TIER_LIMITS:
            return
        async with self.pool.acquire() as conn:
            await conn.execute('UPDATE users SET tier = $1 WHERE user_id = $2', tier, user_id)
    
    async def get_usage_stats(self, user_id: int) -> Tuple[str, int, int, int]:
        """Получение статистики использования"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('SELECT tier FROM users WHERE user_id = $1', user_id)
            tier = row[0] if row else "free"
            limit = Config.TIER_LIMITS.get(tier, 30)
            today = datetime.now().strftime("%Y-%m-%d")
            row = await conn.fetchrow(
                'SELECT count FROM usage WHERE user_id = $1 AND date = $2',
                user_id, today
            )
            used = row[0] if row else 0
            return tier, used, max(0, limit - used), limit
    
    async def increment_usage(self, user_id: int) -> Tuple[bool, int]:
        """Увеличение счётчика использования"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('SELECT tier FROM users WHERE user_id = $1', user_id)
            tier = row[0] if row else "free"
            limit = Config.TIER_LIMITS.get(tier, 30)
            today = datetime.now().strftime("%Y-%m-%d")
            
            row = await conn.fetchrow(
                'SELECT count FROM usage WHERE user_id = $1 AND date = $2',
                user_id, today
            )
            current = row[0] if row else 0
            
            if current >= limit:
                return False, 0
            
            if row:
                await conn.execute('''
                    UPDATE usage SET count = count + 1 
                    WHERE user_id = $1 AND date = $2
                ''', user_id, today)
            else:
                await conn.execute('''
                    INSERT INTO usage (user_id, date, count) 
                    VALUES ($1, $2, 1)
                ''', user_id, today)
            
            remaining = limit - (current + 1)
            return True, remaining
    
    async def is_banned(self, user_id: int) -> Tuple[bool, str]:
        """Проверка бана пользователя"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                'SELECT is_banned, ban_reason FROM users WHERE user_id = $1',
                user_id
            )
            if row and row['is_banned']:
                return True, row['ban_reason'] or "Нарушение правил"
            return False, ""
    
    async def ban_user(self, user_id: int, reason: str = "Нарушение правил") -> None:
        """Бан пользователя"""
        async with self.pool.acquire() as conn:
            await conn.execute('''
                UPDATE users SET is_banned = TRUE, ban_reason = $1 
                WHERE user_id = $2
            ''', reason, user_id)
        logging.info(f"Пользователь {user_id} забанен: {reason}")
    
    async def unban_user(self, user_id: int) -> None:
        """Разбан пользователя"""
        async with self.pool.acquire() as conn:
            await conn.execute('''
                UPDATE users SET is_banned = FALSE, ban_reason = NULL 
                WHERE user_id = $1
            ''', user_id)
        logging.info(f"Пользователь {user_id} разбанен")
    
    async def get_total_users(self) -> int:
        """Общее количество пользователей"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('SELECT COUNT(*) FROM users')
            return row[0] if row else 0
    
    async def get_today_active_users(self) -> int:
        """Количество активных пользователей сегодня"""
        today = datetime.now().strftime("%Y-%m-%d")
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('''
                SELECT COUNT(DISTINCT user_id) FROM usage WHERE date = $1
            ''', today)
            return row[0] if row else 0
    
    async def get_today_requests(self) -> int:
        """Количество запросов сегодня"""
        today = datetime.now().strftime("%Y-%m-%d")
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('SELECT SUM(count) FROM usage WHERE date = $1', today)
            return row[0] if row else 0
    
    async def get_referral_info(self, user_id: int) -> Tuple[int, int]:
        """Получение реферальной информации"""
        async with self.pool.acquire() as conn:
            # Количество приглашённых
            invited = await conn.fetchval('''
                SELECT COUNT(*) FROM users WHERE referrer_id = $1
            ''', user_id) or 0
            
            # Бонусы
            bonus = await conn.fetchval('''
                SELECT bonus_balance FROM referrals WHERE user_id = $1
            ''', user_id) or 0
            
            return invited, bonus
    
    async def get_or_create_referral_code(self, user_id: int) -> str:
        """Получение или создание реферального кода"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                'SELECT referral_code FROM users WHERE user_id = $1',
                user_id
            )
            if row and row['referral_code']:
                return row['referral_code']
            
            code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
            await conn.execute('''
                UPDATE users SET referral_code = $1 WHERE user_id = $2
            ''', code, user_id)
            return code
    
    async def cleanup_old_history(self) -> None:
        """Очистка старой истории"""
        async with self.pool.acquire() as conn:
            result = await conn.execute('''
                DELETE FROM history 
                WHERE timestamp < NOW() - INTERVAL '30 days'
            ''')
            logging.info(f"Очистка истории: удалено записей")
    
    async def get_users_list(self, limit: int = 100, offset: int = 0) -> List[dict]:
        """Получение списка пользователей (для админов)"""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch('''
                SELECT user_id, tier, is_banned, created_at 
                FROM users 
                ORDER BY created_at DESC 
                LIMIT $1 OFFSET $2
            ''', limit, offset)
            return [dict(row) for row in rows]

db = DatabaseManager()

# ==================== REDIS МЕНЕДЖЕР ====================
class RedisManager:
    """Менеджер Redis для кэширования и rate limiting"""
    
    def __init__(self):
        self.client: Optional[redis.Redis] = None
        self._enabled = False
    
    async def init(self, url: str) -> None:
        """Инициализация подключения к Redis"""
        try:
            self.client = await redis.from_url(url, decode_responses=True)
            await self.client.ping()
            self._enabled = True
            redis_connections.set(1)
            logging.info("Redis подключён")
        except Exception as e:
            logging.warning(f"Redis недоступен: {e}, кэш и rate limit отключены")
            self._enabled = False
            redis_connections.set(0)
    
    async def close(self) -> None:
        """Закрытие подключения"""
        if self.client:
            await self.client.close()
            logging.info("Redis закрыт")
    
    @property
    def enabled(self) -> bool:
        return self._enabled
    
    async def rate_limit(self, user_id: int, limit: int = 2, period: int = 1) -> bool:
        """Проверка rate limit"""
        if not self._enabled:
            return True
        
        key = f"rl:{user_id}"
        current = await self.client.incr(key)
        if current == 1:
            await self.client.expire(key, period)
        return current <= limit
    
    async def deduplicate(self, user_id: int, text: str) -> bool:
        """Проверка на дубликаты сообщений"""
        if not self._enabled or len(text) < 10:
            return False
        
        key = f"dup:{user_id}"
        last = await self.client.get(key)
        if last and last == text:
            return True
        await self.client.setex(key, 5, text)
        return False
    
    async def cache_get(self, key: str) -> Optional[str]:
        """Получение из кэша"""
        if not self._enabled:
            return None
        return await self.client.get(key)
    
    async def cache_set(self, key: str, value: str, ttl: int = Config.CACHE_TTL) -> None:
        """Сохранение в кэш"""
        if self._enabled:
            await self.client.setex(key, ttl, value)
    
    async def cache_delete(self, key: str) -> None:
        """Удаление из кэша"""
        if self._enabled:
            await self.client.delete(key)
    
    async def get_user_state(self, user_id: int, state_key: str) -> Optional[dict]:
        """Получение состояния пользователя"""
        if not self._enabled:
            return None
        key = f"state:{user_id}:{state_key}"
        data = await self.client.get(key)
        return json.loads(data) if data else None
    
    async def set_user_state(self, user_id: int, state_key: str, data: dict, ttl: int = 3600) -> None:
        """Сохранение состояния пользователя"""
        if self._enabled:
            key = f"state:{user_id}:{state_key}"
            await self.client.setex(key, ttl, json.dumps(data))
    
    async def delete_user_state(self, user_id: int, state_key: str) -> None:
        """Удаление состояния пользователя"""
        if self._enabled:
            key = f"state:{user_id}:{state_key}"
            await self.client.delete(key)
    
    async def increment_spam_score(self, user_id: int) -> int:
        """Увеличение счётчика спама"""
        if not self._enabled:
            return 0
        
        key = f"spam:{user_id}"
        score = await self.client.incr(key)
        if score == 1:
            await self.client.expire(key, Config.SPAM_WINDOW)
        
        # Автобан при превышении порога
        if score >= Config.SPAM_THRESHOLD:
            await db.ban_user(user_id, f"Автобан: {score} запросов за {Config.SPAM_WINDOW} сек")
        
        return score

redis_manager = RedisManager()

# ==================== GROQ API КЛИЕНТ ====================
class GroqClient:
    """Клиент для работы с Groq API"""
    
    def __init__(self):
        self.session: Optional[ClientSession] = None
        self.api_key = Config.GROQ_API_KEY
        self.base_url = "https://api.groq.com/openai/v1"
        self._metrics: Dict[str, ModelMetrics] = {}
        self._circuit_breaker = {
            "failed": False,
            "failures": 0,
            "disabled_until": None
        }
    
    async def init(self) -> None:
        """Инициализация HTTP сессии"""
        timeout = ClientTimeout(total=30, connect=10)
        self.session = ClientSession(timeout=timeout)
        logging.info("Groq клиент инициализирован")
    
    async def close(self) -> None:
        """Закрытие HTTP сессии"""
        if self.session:
            await self.session.close()
            logging.info("Groq клиент закрыт")
    
    def _is_circuit_open(self) -> bool:
        """Проверка состояния circuit breaker"""
        if self._circuit_breaker["failed"]:
            if self._circuit_breaker["disabled_until"] and time.time() < self._circuit_breaker["disabled_until"]:
                return True
            self._circuit_breaker["failed"] = False
            self._circuit_breaker["failures"] = 0
        return False
    
    def _record_failure(self):
        """Запись неудачного запроса"""
        self._circuit_breaker["failures"] += 1
        if self._circuit_breaker["failures"] >= Config.CIRCUIT_FAILURE_THRESHOLD:
            self._circuit_breaker["failed"] = True
            self._circuit_breaker["disabled_until"] = time.time() + Config.CIRCUIT_COOLDOWN
            logging.warning(f"Circuit breaker активирован на {Config.CIRCUIT_COOLDOWN} сек")
    
    def _record_success(self):
        """Запись успешного запроса"""
        self._circuit_breaker["failures"] = 0
    
    def _get_metrics(self, model: str) -> ModelMetrics:
        """Получение метрик для модели"""
        if model not in self._metrics:
            self._metrics[model] = ModelMetrics()
        return self._metrics[model]
    
    async def ask(self, model: str, messages: List[Dict], is_code: bool = False) -> Tuple[str, bool]:
        """Отправка запроса к Groq"""
        if self._is_circuit_open():
            return "Сервис временно недоступен. Попробуйте позже.", False
        
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
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
                        duration = time.time() - start_time
                        metrics.add_request(duration, True)
                        self._record_success()
                        requests_total.labels(model=model, status="success", endpoint="chat").inc()
                        response_time.labels(model=model, endpoint="chat").observe(duration)
                        return result, True
                    elif resp.status == 429:
                        await asyncio.sleep(2 ** attempt)
                        continue
                    else:
                        error_text = await resp.text()
                        logging.error(f"Groq API ошибка {resp.status}: {error_text[:200]}")
                        break
            except asyncio.TimeoutError:
                logging.warning(f"Таймаут для модели {model}, попытка {attempt + 1}")
                await asyncio.sleep(2 ** attempt)
            except Exception as e:
                logging.error(f"Ошибка Groq API: {e}")
                break
        
        metrics.add_request(0, False)
        self._record_failure()
        requests_total.labels(model=model, status="error", endpoint="chat").inc()
        errors_total.labels(error_type="groq_api", handler="ask").inc()
        return "Сервис временно недоступен. Попробуйте позже.", False
    
    async def ask_with_fallback(self, model: str, messages: List[Dict], is_code: bool = False) -> Tuple[str, str]:
        """Запрос с автоматическим fallback на другие модели"""
        # Пробуем выбранную модель
        result, success = await self.ask(model, messages, is_code)
        if success:
            return result, model
        
        # Fallback на другие модели
        for fallback in Config.MODEL_LIST:
            if fallback == model:
                continue
            result, success = await self.ask(fallback, messages, is_code)
            if success:
                return f"[Переключено на {Config.MODEL_NAMES[fallback]}]\n\n{result}", fallback
        
        return "Сервис временно недоступен. Попробуйте позже.", "error"
    
    def get_metrics(self) -> Dict[str, dict]:
        """Получение всех метрик"""
        return {
            model: {
                "total_requests": m.total_requests,
                "avg_time": round(m.avg_time, 2),
                "success_rate": round(m.success_rate, 1),
                "error_count": m.error_count
            }
            for model, m in self._metrics.items()
        }

groq_client = GroqClient()

# ==================== CRYPTOBOT КЛИЕНТ ====================
class CryptoBotClient:
    """Клиент для работы с CryptoBot API"""
    
    def __init__(self):
        self.token = Config.CRYPTOBOT_TOKEN
        self.secret = Config.CRYPTOBOT_SECRET
        self.base_url = "https://pay.crypt.bot/api"
    
    async def create_invoice(self, user_id: int, tier: str) -> Optional[str]:
        """Создание счёта на оплату"""
        if not self.token:
            return None
        
        amount = 3.0 if tier == "pro" else 10.0
        url = f"{self.base_url}/createInvoice"
        headers = {
            "Crypto-Pay-API-Token": self.token,
            "Content-Type": "application/json"
        }
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
                logging.error(f"CryptoBot ошибка: {e}")
        return None
    
    def verify_signature(self, data: bytes, signature: str) -> bool:
        """Проверка подписи вебхука"""
        if not self.secret:
            return True
        
        expected = hmac.new(self.secret.encode(), data, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)

crypto_client = CryptoBotClient()

# ==================== КЛАВИАТУРЫ ====================
class Keyboards:
    """Централизованное создание клавиатур"""
    
    @staticmethod
    def main() -> InlineKeyboardMarkup:
        """Главное меню"""
        builder = InlineKeyboardBuilder()
        builder.button(text="👤 Личный кабинет", callback_data="profile")
        builder.button(text="🤖 Сменить модель", callback_data="show_models")
        if Config.CRYPTOBOT_TOKEN:
            builder.button(text="💎 Купить премиум", callback_data="premium")
        builder.button(text="📊 Статистика", callback_data="stats")
        builder.adjust(1)
        return builder.as_markup()
    
    @staticmethod
    def models() -> InlineKeyboardMarkup:
        """Выбор модели (2 колонки)"""
        builder = InlineKeyboardBuilder()
        for model_id in Config.MODEL_LIST:
            builder.button(
                text=Config.MODEL_NAMES[model_id],
                callback_data=f"model_{model_id}"
            )
        builder.button(text="◀️ Назад", callback_data="back")
        builder.adjust(2)
        return builder.as_markup()
    
    @staticmethod
    def premium() -> InlineKeyboardMarkup:
        """Выбор тарифа"""
        builder = InlineKeyboardBuilder()
        builder.button(text="⭐ PRO (500 запросов/день)", callback_data="buy_pro")
        builder.button(text="👑 ULTRA (10000 запросов/день)", callback_data="buy_ultra")
        builder.button(text="◀️ Назад", callback_data="back")
        builder.adjust(1)
        return builder.as_markup()
    
    @staticmethod
    def profile() -> InlineKeyboardMarkup:
        """Меню профиля"""
        builder = InlineKeyboardBuilder()
        builder.button(text="💎 Купить премиум", callback_data="premium")
        builder.button(text="🔗 Реферальная ссылка", callback_data="show_ref")
        builder.button(text="📊 Метрики моделей", callback_data="model_metrics")
        builder.button(text="🗑 Очистить историю", callback_data="clear_history")
        builder.button(text="◀️ Назад", callback_data="back")
        builder.adjust(1)
        return builder.as_markup()
    
    @staticmethod
    def admin() -> InlineKeyboardMarkup:
        """Админ-панель"""
        builder = InlineKeyboardBuilder()
        builder.button(text="📊 Статистика", callback_data="admin_stats")
        builder.button(text="👥 Пользователи", callback_data="admin_users")
        builder.button(text="✉️ Рассылка", callback_data="admin_broadcast")
        builder.button(text="◀️ Назад", callback_data="back")
        builder.adjust(2)
        return builder.as_markup()
    
    @staticmethod
    def export() -> InlineKeyboardMarkup:
        """Выбор формата экспорта"""
        builder = InlineKeyboardBuilder()
        builder.button(text="📄 TXT", callback_data="export_txt")
        builder.button(text="📦 JSON", callback_data="export_json")
        builder.button(text="◀️ Назад", callback_data="back")
        builder.adjust(2)
        return builder.as_markup()

kb = Keyboards()

# ==================== УПРАВЛЕНИЕ ОЧЕРЕДЬЮ ====================
class RequestQueue:
    """Управление очередью запросов с семaфорами"""
    
    def __init__(self, maxsize: int = Config.QUEUE_MAX_SIZE, concurrency: int = Config.GLOBAL_CONCURRENCY):
        self.queue = asyncio.Queue(maxsize=maxsize)
        self.semaphore = asyncio.Semaphore(concurrency)
        self._workers = []
        self._running = False
    
    async def start(self, num_workers: int = Config.QUEUE_WORKERS):
        """Запуск воркеров"""
        self._running = True
        for i in range(num_workers):
            worker = asyncio.create_task(self._worker(i))
            self._workers.append(worker)
        logging.info(f"Запущено {num_workers} воркеров")
    
    async def stop(self):
        """Остановка воркеров"""
        self._running = False
        for worker in self._workers:
            worker.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        logging.info("Все воркеры остановлены")
    
    async def _worker(self, worker_id: int):
        """Воркер для обработки запросов"""
        while self._running:
            try:
                future, user_id, model, messages = await self.queue.get()
                queue_size.set(self.queue.qsize())
                
                async with self.semaphore:
                    try:
                        result, used_model = await groq_client.ask_with_fallback(model, messages)
                        future.set_result((result, used_model))
                    except Exception as e:
                        future.set_exception(e)
                    finally:
                        self.queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logging.error(f"Воркер {worker_id} ошибка: {e}")
    
    async def add(self, user_id: int, model: str, messages: List[Dict]) -> Tuple[str, str]:
        """Добавление запроса в очередь"""
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        await self.queue.put((future, user_id, model, messages))
        return await future

request_queue = RequestQueue()

# ==================== АНТИСПАМ ====================
class SpamManager:
    """Менеджер антиспама"""
    
    def __init__(self):
        self.spam_tracker: Dict[int, List[float]] = {}
    
    def check(self, user_id: int) -> Tuple[bool, Optional[str]]:
        """
        Проверка на спам
        Returns: (is_spam, warning_message)
        """
        now = time.time()
        
        if user_id not in self.spam_tracker:
            self.spam_tracker[user_id] = []
        
        # Очищаем старые записи
        self.spam_tracker[user_id] = [
            t for t in self.spam_tracker[user_id] 
            if now - t < Config.SPAM_WINDOW
        ]
        self.spam_tracker[user_id].append(now)
        
        count = len(self.spam_tracker[user_id])
        
        if count >= Config.SPAM_THRESHOLD:
            return True, "Вы забанены за спам (50 запросов в минуту)"
        
        if count >= Config.SPAM_WARN_THRESHOLD:
            return False, "⚠️ Предупреждение: слишком много запросов! При превышении 50 в минуту вы будете забанены."
        
        return False, None

spam_manager = SpamManager()

# ==================== ЭКСПОРТ ДИАЛОГА ====================
class ExportService:
    """Сервис экспорта диалогов"""
    
    @staticmethod
    async def export_txt(user_id: int) -> BufferedInputFile:
        """Экспорт в TXT"""
        history = await db.get_history(user_id, 100)
        lines = []
        for msg in history:
            role = "👤 Пользователь" if msg["role"] == "user" else "🤖 Ассистент"
            lines.append(f"{role}:\n{msg['content']}\n{'-'*50}")
        
        text = "\n".join(lines)
        data = text.encode('utf-8')
        return BufferedInputFile(data, filename=f"dialog_{user_id}.txt")
    
    @staticmethod
    async def export_json(user_id: int) -> BufferedInputFile:
        """Экспорт в JSON"""
        history = await db.get_history(user_id, 100)
        data = json.dumps(history, ensure_ascii=False, indent=2).encode('utf-8')
        return BufferedInputFile(data, filename=f"dialog_{user_id}.json")

export_service = ExportService()

# ==================== ХЕНДЛЕРЫ КОМАНД ====================
@dp.message(Command("start"))
async def cmd_start(message: Message):
    """Обработчик команды /start"""
    user_id = message.from_user.id
    args = message.text.split()
    ref_code = args[1] if len(args) > 1 else None
    
    # Создаём пользователя
    await db.create_user(user_id, ref_code)
    
    # Приветственное сообщение
    welcome_text = (
        "🤖 **LLM Hub Bot**\n\n"
        "Добро пожаловать! Я предоставляю доступ к передовым AI моделям.\n\n"
        "**Доступные модели:**\n"
        "• Llama 4 Scout (самая новая)\n"
        "• Llama 3.3 70B (надёжная)\n"
        "• Llama 3.1 8B (быстрая)\n"
        "• Qwen 3 32B (баланс)\n"
        "• GPT-OSS 20B (быстрая)\n"
        "• GPT-OSS 120B (мощная)\n\n"
        "**Тарифы:**\n"
        "• Бесплатный: 30 запросов/день\n"
        "• PRO: 500 запросов/день (3 USDT)\n"
        "• ULTRA: 10000 запросов/день (10 USDT)\n\n"
        "Просто напишите сообщение, и я отвечу!"
    )
    
    await message.answer(welcome_text, parse_mode="Markdown", reply_markup=kb.main())

@dp.message(Command("admin"))
async def cmd_admin(message: Message):
    """Админ-панель"""
    user_id = message.from_user.id
    if user_id not in Config.ADMIN_IDS:
        await message.answer("⛔ Доступ запрещён")
        return
    
    await message.answer("👑 **Админ-панель**", parse_mode="Markdown", reply_markup=kb.admin())

@dp.message(Command("stats"))
async def cmd_stats(message: Message):
    """Статистика пользователя"""
    user_id = message.from_user.id
    tier, used, remaining, limit = await db.get_usage_stats(user_id)
    history_count = await db.get_history_count(user_id)
    invited, bonus = await db.get_referral_info(user_id)
    model = await db.get_user_model(user_id)
    
    stats_text = (
        f"📊 **Ваша статистика**\n\n"
        f"💎 Тариф: {Config.TIER_NAMES.get(tier, tier).upper()}\n"
        f"📈 Запросов сегодня: {used}/{limit}\n"
        f"✨ Осталось: {remaining}\n"
        f"💬 Всего сообщений: {history_count}\n"
        f"🤖 Текущая модель: {Config.MODEL_NAMES[model]}\n"
        f"👥 Приглашено друзей: {invited}\n"
        f"🎁 Бонусов: {bonus}\n"
        f"🔄 Сброс лимита: 00:00 UTC"
    )
    
    await message.answer(stats_text, parse_mode="Markdown", reply_markup=kb.main())

@dp.message(Command("profile"))
async def cmd_profile(message: Message):
    """Личный кабинет"""
    user_id = message.from_user.id
    tier, used, remaining, limit = await db.get_usage_stats(user_id)
    history_count = await db.get_history_count(user_id)
    invited, bonus = await db.get_referral_info(user_id)
    model = await db.get_user_model(user_id)
    
    banned, ban_reason = await db.is_banned(user_id)
    
    profile_text = (
        f"👤 **Личный кабинет**\n\n"
        f"💎 Тариф: {Config.TIER_NAMES.get(tier, tier).upper()}\n"
        f"📈 Запросов сегодня: {used}/{limit}\n"
        f"✨ Осталось: {remaining}\n"
        f"💬 Всего сообщений: {history_count}\n"
        f"🤖 Текущая модель: {Config.MODEL_NAMES[model]}\n"
        f"👥 Рефералов: {invited} (бонусов: {bonus})\n"
    )
    
    if banned:
        profile_text += f"\n🚫 **Забанен**\nПричина: {ban_reason}"
    
    await message.answer(profile_text, parse_mode="Markdown", reply_markup=kb.profile())

@dp.message(Command("models"))
async def cmd_models(message: Message):
    """Список моделей"""
    await message.answer("🤖 **Выберите модель:**", parse_mode="Markdown", reply_markup=kb.models())

@dp.message(Command("clear"))
async def cmd_clear(message: Message):
    """Очистка истории"""
    user_id = message.from_user.id
    await db.clear_history(user_id)
    await message.answer("✅ История диалога очищена", reply_markup=kb.main())

@dp.message(Command("export"))
async def cmd_export(message: Message):
    """Экспорт диалога"""
    await message.answer("📤 **Выберите формат экспорта:**", parse_mode="Markdown", reply_markup=kb.export())

# ==================== CALLBACK ХЕНДЛЕРЫ ====================
@dp.callback_query()
async def handle_callback(call: CallbackQuery):
    """Обработчик callback запросов"""
    user_id = call.from_user.id
    data = call.data
    
    # Смена модели
    if data.startswith("model_"):
        model = data.replace("model_", "")
        await db.update_user_model(user_id, model)
        await call.message.edit_text(
            f"✅ Модель изменена на: **{Config.MODEL_NAMES[model]}**",
            parse_mode="Markdown",
            reply_markup=kb.main()
        )
        await call.answer()
        return
    
    # Показать модели
    if data == "show_models":
        await call.message.edit_text(
            "🤖 **Выберите модель:**",
            parse_mode="Markdown",
            reply_markup=kb.models()
        )
        await call.answer()
        return
    
    # Назад в главное меню
    if data == "back":
        await call.message.edit_text(
            "🤖 **Главное меню**",
            parse_mode="Markdown",
            reply_markup=kb.main()
        )
        await call.answer()
        return
    
    # Личный кабинет
    if data == "profile":
        tier, used, remaining, limit = await db.get_usage_stats(user_id)
        history_count = await db.get_history_count(user_id)
        invited, bonus = await db.get_referral_info(user_id)
        model = await db.get_user_model(user_id)
        banned, ban_reason = await db.is_banned(user_id)
        
        text = (
            f"👤 **Личный кабинет**\n\n"
            f"💎 Тариф: {Config.TIER_NAMES.get(tier, tier).upper()}\n"
            f"📈 Запросов сегодня: {used}/{limit}\n"
            f"✨ Осталось: {remaining}\n"
            f"💬 Всего сообщений: {history_count}\n"
            f"🤖 Модель: {Config.MODEL_NAMES[model]}\n"
            f"👥 Рефералов: {invited} (бонусов: {bonus})"
        )
        if banned:
            text += f"\n\n🚫 Забанен: {ban_reason}"
        
        await call.message.edit_text(text, parse_mode="Markdown", reply_markup=kb.profile())
        await call.answer()
        return
    
    # Показать реферальную ссылку
    if data == "show_ref":
        code = await db.get_or_create_referral_code(user_id)
        invited, bonus = await db.get_referral_info(user_id)
        bot_username = (await bot.get_me()).username
        
        text = (
            f"🔗 **Реферальная программа**\n\n"
            f"👥 Приглашено друзей: {invited}\n"
            f"🎁 Бонусов на счету: {bonus}\n"
            f"💰 За каждого друга: +{Config.REFERRAL_BONUS} запросов\n\n"
            f"**Ваша ссылка:**\n"
            f"<code>https://t.me/{bot_username}?start={code}</code>\n\n"
            f"📎 Отправьте её другу, и вы оба получите бонусы!"
        )
        
        await call.message.edit_text(text, parse_mode="HTML", reply_markup=kb.profile())
        await call.answer()
        return
    
    # Метрики моделей
    if data == "model_metrics":
        metrics = groq_client.get_metrics()
        if not metrics:
            text = "📊 Нет данных о метриках моделей"
        else:
            text = "📊 **Метрики моделей**\n\n"
            for model, m in metrics.items():
                text += (
                    f"**{Config.MODEL_NAMES.get(model, model)}**\n"
                    f"📈 Запросов: {m['total_requests']}\n"
                    f"⏱ Среднее время: {m['avg_time']}с\n"
                    f"✅ Успешность: {m['success_rate']}%\n"
                    f"❌ Ошибок: {m['error_count']}\n\n"
                )
        
        await call.message.edit_text(text, parse_mode="Markdown", reply_markup=kb.profile())
        await call.answer()
        return
    
    # Очистить историю
    if data == "clear_history":
        await db.clear_history(user_id)
        await call.message.edit_text(
            "✅ История диалога очищена",
            reply_markup=kb.profile()
        )
        await call.answer()
        return
    
    # Премиум меню
    if data == "premium":
        await call.message.edit_text(
            "💎 **Выберите тариф:**\n\n"
            "⭐ **PRO** - 500 запросов/день - 3 USDT\n"
            "👑 **ULTRA** - 10000 запросов/день - 10 USDT",
            parse_mode="Markdown",
            reply_markup=kb.premium()
        )
        await call.answer()
        return
    
    # Купить PRO
    if data == "buy_pro":
        invoice_url = await crypto_client.create_invoice(user_id, "pro")
        if invoice_url:
            text = (
                f"💎 **Оплата тарифа PRO**\n\n"
                f"Сумма: 3 USDT\n"
                f"[Перейти к оплате]({invoice_url})\n\n"
                f"После оплаты тариф будет повышен автоматически."
            )
            await call.message.edit_text(text, parse_mode="Markdown", reply_markup=kb.main(), disable_web_page_preview=True)
        else:
            await call.message.edit_text("❌ Ошибка при создании счёта. Попробуйте позже.", reply_markup=kb.main())
        await call.answer()
        return
    
    # Купить ULTRA
    if data == "buy_ultra":
        invoice_url = await crypto_client.create_invoice(user_id, "ultra")
        if invoice_url:
            text = (
                f"👑 **Оплата тарифа ULTRA**\n\n"
                f"Сумма: 10 USDT\n"
                f"[Перейти к оплате]({invoice_url})\n\n"
                f"После оплаты тариф будет повышен автоматически."
            )
            await call.message.edit_text(text, parse_mode="Markdown", reply_markup=kb.main(), disable_web_page_preview=True)
        else:
            await call.message.edit_text("❌ Ошибка при создании счёта. Попробуйте позже.", reply_markup=kb.main())
        await call.answer()
        return
    
    # Экспорт
    if data == "export_txt":
        file = await export_service.export_txt(user_id)
        await call.message.answer_document(file, caption="📄 Ваш диалог")
        await call.message.edit_text("🤖 Главное меню", reply_markup=kb.main())
        await call.answer()
        return
    
    if data == "export_json":
        file = await export_service.export_json(user_id)
        await call.message.answer_document(file, caption="📦 Ваш диалог в JSON")
        await call.message.edit_text("🤖 Главное меню", reply_markup=kb.main())
        await call.answer()
        return
    
    # Статистика
    if data == "stats":
        tier, used, remaining, limit = await db.get_usage_stats(user_id)
        history_count = await db.get_history_count(user_id)
        invited, bonus = await db.get_referral_info(user_id)
        model = await db.get_user_model(user_id)
        
        text = (
            f"📊 **Ваша статистика**\n\n"
            f"💎 Тариф: {Config.TIER_NAMES.get(tier, tier).upper()}\n"
            f"📈 Запросов сегодня: {used}/{limit}\n"
            f"✨ Осталось: {remaining}\n"
            f"💬 Всего сообщений: {history_count}\n"
            f"🤖 Модель: {Config.MODEL_NAMES[model]}\n"
            f"👥 Рефералов: {invited}\n"
            f"🎁 Бонусов: {bonus}"
        )
        
        await call.message.edit_text(text, parse_mode="Markdown", reply_markup=kb.main())
        await call.answer()
        return
    
    # АДМИН-ПАНЕЛЬ
    if user_id not in Config.ADMIN_IDS:
        await call.answer()
        return
    
    if data == "admin_stats":
        total_users = await db.get_total_users()
        active_today = await db.get_today_active_users()
        requests_today = await db.get_today_requests()
        
        text = (
            f"📊 **Статистика бота**\n\n"
            f"👥 Всего пользователей: {total_users}\n"
            f"👤 Активных сегодня: {active_today}\n"
            f"📈 Запросов сегодня: {requests_today}\n"
            f"⏳ В очереди: {request_queue.queue.qsize()}\n"
            f"🧠 Воркеров: {Config.QUEUE_WORKERS}"
        )
        
        await call.message.edit_text(text, parse_mode="Markdown", reply_markup=kb.admin())
        await call.answer()
        return
    
    if data == "admin_users":
        users = await db.get_users_list(limit=20)
        text = "👥 **Последние пользователи**\n\n"
        for u in users:
            status = "🚫" if u['is_banned'] else "✅"
            text += f"{status} `{u['user_id']}` | {u['tier']} | {u['created_at'].strftime('%d.%m')}\n"
        
        await call.message.edit_text(text, parse_mode="Markdown", reply_markup=kb.admin())
        await call.answer()
        return
    
    if data == "admin_broadcast":
        await call.message.edit_text(
            "✉️ **Рассылка сообщений**\n\n"
            "Введите текст для рассылки:",
            parse_mode="Markdown",
            reply_markup=kb.admin()
        )
        await call.answer()

# ==================== ОБРАБОТЧИК СООБЩЕНИЙ ====================
@dp.message()
async def handle_message(message: Message):
    """Основной обработчик сообщений"""
    user_id = message.from_user.id
    user_text = message.text or message.caption
    
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
        await db.ban_user(user_id, "Автоматический бан: превышение лимита запросов")
        await message.answer("🚫 Вы забанены за спам (50 запросов в минуту).")
        return
    elif spam_warning:
        await message.answer(spam_warning)
    
    # Проверка текста
    if not user_text:
        return
    
    # Очистка ввода
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
        await message.answer(f"❌ Лимит {Config.TIER_NAMES.get(tier, tier).upper()} исчерпан на сегодня.")
        return
    
    # Получаем историю
    history = await db.get_history(user_id, 20)
    await db.add_history(user_id, "user", user_text)
    history.append({"role": "user", "content": user_text})
    
    # Проверяем кэш
    model = await db.get_user_model(user_id)
    cache_key = hashlib.md5(f"{model}:{user_text[:100]}".encode()).hexdigest()
    cached = await redis_manager.cache_get(cache_key)
    if cached:
        await message.answer(cached[:4000], reply_markup=kb.main())
        return
    
    # Отправляем запрос
    status_msg = await message.answer("🤔 Думаю...")
    queue_size.set(request_queue.queue.qsize())
    
    start_time = time.time()
    try:
        reply, used_model = await asyncio.wait_for(
            request_queue.add(user_id, model, history),
            timeout=Config.REQUEST_TIMEOUT
        )
        await db.add_history(user_id, "assistant", reply, used_model)
        
        # Кэшируем короткие ответы
        if len(user_text) < 100 and len(reply) < 500:
            await redis_manager.cache_set(cache_key, reply[:1000])
        
    except asyncio.TimeoutError:
        reply = "⏳ Превышено время ожидания ответа. Попробуйте позже."
        used_model = "timeout"
    except Exception as e:
        logging.error(f"Ошибка обработки: {e}")
        reply = "❌ Произошла ошибка. Попробуйте позже."
        used_model = "error"
    
    response_time.labels(model=used_model, endpoint="message").observe(time.time() - start_time)
    
    await status_msg.delete()
    await message.answer(reply[:4000], reply_markup=kb.main())
    
    # Предупреждение об остатке лимита
    if remaining <= 5:
        await message.answer(f"⚠️ Осталось запросов сегодня: {remaining}")

# ==================== WEBHOOK И МЕТРИКИ ====================
async def health_check(request: web.Request) -> web.Response:
    """Health check endpoint для Render"""
    status = {
        "status": "ok",
        "version": __version__,
        "timestamp": datetime.now().isoformat(),
        "environment": Config.ENVIRONMENT
    }
    
    # Проверка PostgreSQL
    try:
        await db.get_total_users()
        status["postgresql"] = "ok"
    except Exception as e:
        status["postgresql"] = f"error: {e}"
        status["status"] = "degraded"
    
    # Проверка Redis
    if redis_manager.enabled:
        try:
            await redis_manager.client.ping()
            status["redis"] = "ok"
        except:
            status["redis"] = "error"
    
    status["queue_size"] = request_queue.queue.qsize()
    
    return web.Response(text=json.dumps(status, ensure_ascii=False), content_type="application/json")

async def metrics_endpoint(request: web.Request) -> web.Response:
    """Prometheus metrics endpoint"""
    return web.Response(body=generate_latest(), content_type=CONTENT_TYPE_LATEST)

async def crypto_webhook(request: web.Request) -> web.Response:
    """CryptoBot вебхук для обработки платежей"""
    signature = request.headers.get("crypto-pay-api-signature", "")
    raw_body = await request.text()
    
    if not crypto_client.verify_signature(raw_body.encode(), signature):
        logging.warning("Неверная подпись CryptoBot")
        return web.Response(text="Invalid signature", status=403)
    
    try:
        data = json.loads(raw_body)
        if data.get("update_type") == "invoice_paid":
            payload = data.get("payload", {})
            user_id = int(payload.get("payload", 0))
            amount = payload.get("paid_amount")
            
            if user_id:
                tier = "pro" if amount == "3.00" else "ultra" if amount == "10.00" else None
                if tier:
                    await db.set_user_tier(user_id, tier)
                    await bot.send_message(user_id, f"✅ Тариф повышен до {tier.upper()}!")
                    
                    # Логируем платёж
                    logging.info(f"Платёж: пользователь {user_id}, тариф {tier}, сумма {amount} USDT")
    except Exception as e:
        logging.error(f"Crypto webhook ошибка: {e}")
    
    return web.Response(text="OK")

# ==================== АВТООЧИСТКА ====================
async def scheduled_cleanup():
    """Автоматическая очистка БД каждую ночь в 3:00"""
    while True:
        now = datetime.now()
        next_run = now.replace(hour=3, minute=0, second=0, microsecond=0)
        if now >= next_run:
            next_run += timedelta(days=1)
        
        wait_seconds = (next_run - now).total_seconds()
        await asyncio.sleep(wait_seconds)
        
        try:
            await db.cleanup_old_history()
            logging.info("Автоочистка БД выполнена")
        except Exception as e:
            logging.error(f"Ошибка автоочистки: {e}")

# ==================== ЗАПУСК И ОСТАНОВКА ====================
async def on_startup():
    """Действия при запуске бота"""
    logging.info(f"🚀 Запуск LLM Hub Bot v{__version__}")
    
    # Инициализация базы данных
    await db.init(Config.DATABASE_URL)
    
    # Инициализация Redis
    await redis_manager.init(Config.REDIS_URL)
    
    # Инициализация Groq клиента
    await groq_client.init()
    
    # Запуск очереди
    await request_queue.start()
    
    # Запуск автоочистки
    asyncio.create_task(scheduled_cleanup())
    
    # Запуск веб-сервера для health check
    app = web.Application()
    app.router.add_get("/health", health_check)
    app.router.add_get("/metrics", metrics_endpoint)
    app.router.add_post("/crypto", crypto_webhook)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", Config.PORT)
    await site.start()
    
    logging.info(f"✅ Бот запущен | Порт: {Config.PORT} | Workers: {Config.QUEUE_WORKERS}")
    logging.info(f"👑 Админы: {Config.ADMIN_IDS}")

async def on_shutdown():
    """Действия при остановке бота"""
    logging.info("🛑 Остановка бота...")
    
    await request_queue.stop()
    await groq_client.close()
    await redis_manager.close()
    await db.close()
    
    logging.info("✅ Бот остановлен")

async def main():
    """Главная функция"""
    try:
        Config.validate()
        await on_startup()
        await dp.start_polling(bot, on_shutdown=on_shutdown)
    except Exception as e:
        logging.error(f"Критическая ошибка: {e}")
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
