from collections import defaultdict
from datetime import datetime, timedelta

_store: dict[str, list[datetime]] = defaultdict(list)


def check_rate_limit(key: str, max_calls: int, window_seconds: int) -> bool:
    now = datetime.utcnow()
    cutoff = now - timedelta(seconds=window_seconds)
    recent = [t for t in _store[key] if t > cutoff]
    if len(recent) >= max_calls:
        return False
    recent.append(now)
    _store[key] = recent
    return True


def reset_rate_limit(key: str) -> None:
    _store.pop(key, None)


def clear_all_rate_limits() -> None:
    _store.clear()
