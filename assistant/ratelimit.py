from django.core.cache import cache


def allow(key, limit, window_seconds):
    """Count one hit for `key`; False once it exceeds `limit` hits within the window."""
    cache_key = f'ratelimit:{key}'
    if cache.add(cache_key, 1, window_seconds):
        return True
    try:
        hits = cache.incr(cache_key)
    except ValueError:   # expired between add() and incr()
        cache.add(cache_key, 1, window_seconds)
        return True
    return hits <= limit
