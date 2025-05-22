import time
import asyncio
from functools import wraps
from typing import Any, Callable, Dict, Tuple

_CACHE: Dict[Tuple, Tuple[Any, float]] = {}


def ttl_cache(ttl: int = 30):
    """Simple async-aware TTL cache decorator."""

    def decorator(fn: Callable):
        @wraps(fn)
        async def wrapper(*args, **kwargs):
            key = (fn.__name__, args, tuple(sorted(kwargs.items())))
            value, ts = _CACHE.get(key, (None, 0))
            if time.time() - ts < ttl:
                return value
            value = await fn(*args, **kwargs)
            _CACHE[key] = (value, time.time())
            return value

        return wrapper

    return decorator


async def clear_cache():
    _CACHE.clear() 