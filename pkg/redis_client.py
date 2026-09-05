import asyncio
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Optional

import redis.asyncio as aioredis

from pkg.config import get_settings
from pkg.logger import get_logger

logger = get_logger("redis")
settings = get_settings()

_redis_pool: Optional[aioredis.Redis] = None


def get_redis_client() -> aioredis.Redis:
    """Returns a shared Redis client instance using connection pool."""
    global _redis_pool
    if _redis_pool is None:
        _redis_pool = aioredis.from_url(
            settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
            max_connections=50,
        )
    return _redis_pool


async def close_redis() -> None:
    """Closes Redis connections."""
    global _redis_pool
    if _redis_pool is not None:
        await _redis_pool.close()
        _redis_pool = None


@asynccontextmanager
async def distributed_lock(
    lock_key: str,
    timeout_seconds: int = 30,
    retry_interval: float = 0.1,
    max_retries: int = 50,
) -> AsyncGenerator[bool, None]:
    """
    Acquires a distributed lock using Redis SETNX with expiration.
    Ensures safe idempotent task and scheduler execution.
    """
    try:
        client = get_redis_client()
        key = f"lock:{lock_key}"
        acquired = False

        for _ in range(max_retries):
            # SET key 1 NX EX timeout
            if await client.set(key, "1", nx=True, ex=timeout_seconds):
                acquired = True
                break
            await asyncio.sleep(retry_interval)

        if not acquired:
            yield False
            return

        try:
            yield True
        finally:
            try:
                await client.delete(key)
            except Exception as e:
                logger.warning(f"Error releasing lock {key}: {e}")
    except Exception as e:
        logger.warning(f"Redis distributed lock unavailable: {e}. Falling back to single-node grant.")
        yield True


async def check_idempotency(key: str) -> Optional[str]:
    """Retrieves saved execution result or status for an idempotency key."""
    try:
        client = get_redis_client()
        return await client.get(f"idempotency:{key}")
    except Exception as e:
        logger.warning(f"Redis idempotency check bypassed: {e}")
        return None


async def store_idempotency(key: str, value: str, ttl_seconds: int = 86400) -> bool:
    """Stores execution state for an idempotency key with TTL."""
    try:
        client = get_redis_client()
        return bool(await client.set(f"idempotency:{key}", value, ex=ttl_seconds))
    except Exception as e:
        logger.warning(f"Redis store idempotency bypassed: {e}")
        return False


async def check_rate_limit(key: str, limit: int = 100, window_seconds: int = 60) -> bool:
    """
    Simple sliding window rate limiter. Returns True if within limit, False if rate-limited.
    """
    try:
        client = get_redis_client()
        rate_key = f"rate_limit:{key}"
        current = await client.incr(rate_key)
        if current == 1:
            await client.expire(rate_key, window_seconds)
        return current <= limit
    except Exception as e:
        logger.warning(f"Redis rate limiter bypassed: {e}")
        return True


async def check_redis_health() -> bool:
    """Performs a ping against Redis."""
    try:
        client = get_redis_client()
        return await client.ping()
    except Exception as e:
        logger.error(f"Redis health check failed: {e}")
        return False


_local_execution_mode: str = "auto"


async def get_execution_mode() -> str:
    """Returns current task execution mode: 'auto' (default) or 'manual'."""
    global _local_execution_mode
    try:
        client = get_redis_client()
        val = await client.get("cloudtask:execution_mode")
        if val:
            return str(val)
    except Exception:
        pass
    return _local_execution_mode


async def set_execution_mode(mode: str) -> str:
    """Sets task execution mode: 'manual' or 'auto'."""
    global _local_execution_mode
    _local_execution_mode = "auto" if mode.lower() == "auto" else "manual"
    try:
        client = get_redis_client()
        await client.set("cloudtask:execution_mode", _local_execution_mode)
    except Exception:
        pass
    return _local_execution_mode

