import asyncpg
from asyncpg.pool import Pool
import logging
from typing import Optional, List, Dict, Tuple
from modules.config import config

logger = logging.getLogger(__name__)

class DatabaseManager:
    def __init__(self):
        self.pool: Optional[Pool] = None
    
    async def init(self, dsn: str) -> None:
        self.pool = await asyncpg.create_pool(
            dsn,
            min_size=config.DATABASE_POOL_MIN,
            max_size=config.DATABASE_POOL_MAX,
            command_timeout=30,
            max_queries=50000
        )
        await self._create_tables()
        logger.info("PostgreSQL connected")
    
    async def _create_tables(self) -> None:
        async with self.pool.acquire() as conn:
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
                CREATE TABLE IF NOT EXISTS usage (
                    user_id BIGINT,
                    date DATE,
                    count INT DEFAULT 0,
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
            
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS payments (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT,
                    amount DECIMAL(10,2),
                    tier TEXT,
                    status TEXT DEFAULT 'pending',
                    created_at TIMESTAMP DEFAULT NOW()
                )
            ''')
            
            await conn.execute('CREATE INDEX IF NOT EXISTS idx_history_user_id ON history(user_id, timestamp DESC)')
            await conn.execute('CREATE INDEX IF NOT EXISTS idx_usage_date ON usage(date)')
            await conn.execute('CREATE INDEX IF NOT EXISTS idx_users_referral_code ON users(referral_code)')
    
    async def get_user(self, user_id: int) -> Optional[dict]:
        async with self.pool.acquire() as conn:
            return await conn.fetchrow('SELECT * FROM users WHERE user_id = $1', user_id)
    
    async def create_user(self, user_id: int, referral_code: str = None) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute('INSERT INTO users (user_id) VALUES ($1) ON CONFLICT DO NOTHING', user_id)
            await conn.execute('INSERT INTO referrals (user_id) VALUES ($1) ON CONFLICT DO NOTHING', user_id)
    
    async def get_user_model(self, user_id: int) -> str:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('SELECT model FROM users WHERE user_id = $1', user_id)
            return row[0] if row else config.MODEL_LIST[0]
    
    async def update_user_model(self, user_id: int, model: str) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute('UPDATE users SET model = $1, updated_at = NOW() WHERE user_id = $2', model, user_id)
    
    async def add_history(self, user_id: int, role: str, content: str, model: str = None) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute('INSERT INTO history (user_id, role, content, model) VALUES ($1, $2, $3, $4)',
                              user_id, role, content[:4000], model)
            await conn.execute('DELETE FROM history WHERE id IN (SELECT id FROM history WHERE user_id = $1 ORDER BY timestamp DESC OFFSET $2)',
                              user_id, config.MAX_HISTORY)
    
    async def get_history(self, user_id: int, limit: int = 20) -> List[Dict[str, str]]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch('SELECT role, content FROM history WHERE user_id = $1 ORDER BY timestamp ASC LIMIT $2', user_id, limit)
            return [{"role": r[0], "content": r[1]} for r in rows]
    
    async def clear_history(self, user_id: int) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute('DELETE FROM history WHERE user_id = $1', user_id)
    
    async def get_usage_stats(self, user_id: int) -> Tuple[str, int, int, int]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('SELECT tier FROM users WHERE user_id = $1', user_id)
            tier = row[0] if row else "free"
            limit = config.TIER_LIMITS.get(tier, 30)
            today = datetime.now().strftime("%Y-%m-%d")
            row = await conn.fetchrow('SELECT count FROM usage WHERE user_id = $1 AND date = $2', user_id, today)
            used = row[0] if row else 0
            return tier, used, max(0, limit - used), limit
    
    async def increment_usage(self, user_id: int) -> Tuple[bool, int]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('SELECT tier FROM users WHERE user_id = $1', user_id)
            tier = row[0] if row else "free"
            limit = config.TIER_LIMITS.get(tier, 30)
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
                return True, row['ban_reason'] or "Rules violation"
            return False, ""
    
    async def ban_user(self, user_id: int, reason: str = "Rules violation") -> None:
        async with self.pool.acquire() as conn:
            await conn.execute('UPDATE users SET is_banned = TRUE, ban_reason = $1 WHERE user_id = $2', reason, user_id)
        logger.info(f"User {user_id} banned: {reason}")
    
    async def unban_user(self, user_id: int) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute('UPDATE users SET is_banned = FALSE, ban_reason = NULL WHERE user_id = $1', user_id)
        logger.info(f"User {user_id} unbanned")
    
    async def get_total_users(self) -> int:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('SELECT COUNT(*) FROM users')
            return row[0] if row else 0
    
    async def get_today_requests(self) -> int:
        today = datetime.now().strftime("%Y-%m-%d")
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('SELECT SUM(count) FROM usage WHERE date = $1', today)
            return row[0] if row else 0
    
    async def get_or_create_referral_code(self, user_id: int) -> str:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('SELECT referral_code FROM users WHERE user_id = $1', user_id)
            if row and row['referral_code']:
                return row['referral_code']
            code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
            await conn.execute('UPDATE users SET referral_code = $1 WHERE user_id = $2', code, user_id)
            return code
    
    async def get_referral_info(self, user_id: int) -> Tuple[int, int]:
        async with self.pool.acquire() as conn:
            invited = await conn.fetchval('SELECT COUNT(*) FROM users WHERE referrer_id = $1', user_id) or 0
            bonus = await conn.fetchval('SELECT bonus_balance FROM referrals WHERE user_id = $1', user_id) or 0
            return invited, bonus
    
    async def cleanup_old_history(self) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute('DELETE FROM history WHERE timestamp < NOW() - INTERVAL \'30 days\'')
            logger.info("Old history cleaned")

db = DatabaseManager()
