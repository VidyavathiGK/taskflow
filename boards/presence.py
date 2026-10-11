"""
Live board presence tracking using Redis sorted sets.

Tracks active viewers on boards in real time without taxing PostgreSQL:
- Key: presence:board:<board_id>
- Member: "<user_id>:<channel_name>"
- Score: Epoch timestamp (time.time())

LIFECYCLE & STALE PRUNING:
-----------------------------------------------------------------------------
- Connect: Adds membership with current timestamp; prunes scores older than 60s.
- Keepalive Ping: Updates score every ~20s; prunes stale entries.
- Disconnect: Removes that connection's member; prunes stale entries.
- Deduplication: Multiple tabs from the same user appear as one avatar.
- Graceful Degradation: If Redis is offline, all methods swallow exceptions,
  log a warning, and return safe defaults (empty presence list).
"""

import logging
import sys
import time
from typing import Any, Dict, List
from django.conf import settings
from django.contrib.auth import get_user_model
import redis

logger = logging.getLogger("taskflow.presence")
User = get_user_model()

STALE_TIMEOUT_SECONDS = 60.0
_redis_pool = None
_in_memory_presence: Dict[int, Dict[str, float]] = {}


def _use_in_memory() -> bool:
    """Return True if unit tests are running or in-memory channel layer is explicitly configured."""
    return getattr(settings, "USE_IN_MEMORY_CHANNEL_LAYER", False) or ("test" in sys.argv)


def get_redis_client():
    """Return a redis client with low timeouts so Redis downtime never hangs requests."""
    global _redis_pool
    if _redis_pool is None:
        redis_url = getattr(settings, "REDIS_URL", "redis://127.0.0.1:6379/1")
        _redis_pool = redis.ConnectionPool.from_url(
            redis_url,
            socket_timeout=1.0,
            socket_connect_timeout=1.0,
        )
    return redis.Redis(connection_pool=_redis_pool)


def add_presence(board_id: int, user_id: int, channel_name: str) -> None:
    """Register or heartbeat an active WebSocket connection."""
    now = time.time()
    member = f"{user_id}:{channel_name}"

    if _use_in_memory():
        board_store = _in_memory_presence.setdefault(board_id, {})
        board_store[member] = now
        # Prune dead connections older than STALE_TIMEOUT_SECONDS
        for m, score in list(board_store.items()):
            if score < now - STALE_TIMEOUT_SECONDS:
                del board_store[m]
        return

    try:
        r = get_redis_client()
        key = f"presence:board:{board_id}"
        # Add or update member score
        r.zadd(key, {member: now})
        # Prune dead connections older than STALE_TIMEOUT_SECONDS
        r.zremrangebyscore(key, 0, now - STALE_TIMEOUT_SECONDS)
        # Set 2-hour TTL on the presence key to prevent orphaned Redis keys
        r.expire(key, 7200)
    except Exception as exc:
        logger.warning(f"Redis presence tracking offline (add_presence board={board_id}): {exc}")


def refresh_presence(board_id: int, user_id: int, channel_name: str) -> None:
    """Heartbeat an active connection on client ping."""
    add_presence(board_id, user_id, channel_name)


def remove_presence(board_id: int, user_id: int, channel_name: str) -> None:
    """Remove a disconnected channel from the presence sorted set."""
    member = f"{user_id}:{channel_name}"

    if _use_in_memory():
        board_store = _in_memory_presence.get(board_id)
        if board_store:
            board_store.pop(member, None)
            now = time.time()
            for m, score in list(board_store.items()):
                if score < now - STALE_TIMEOUT_SECONDS:
                    del board_store[m]
        return

    try:
        r = get_redis_client()
        key = f"presence:board:{board_id}"
        now = time.time()
        r.zrem(key, member)
        r.zremrangebyscore(key, 0, now - STALE_TIMEOUT_SECONDS)
    except Exception as exc:
        logger.warning(f"Redis presence tracking offline (remove_presence board={board_id}): {exc}")


def get_board_presence(board_id: int) -> List[Dict[str, Any]]:
    """
    Retrieve deduplicated list of active users viewing this board.
    
    Returns:
        List of dicts: [{"id": int, "username": str, "avatar_color": str, "initials": str}, ...]
    """
    now = time.time()
    user_ids = []

    if _use_in_memory():
        board_store = _in_memory_presence.get(board_id, {})
        for m, score in list(board_store.items()):
            if score < now - STALE_TIMEOUT_SECONDS:
                del board_store[m]
            else:
                try:
                    uid = int(m.split(":", 1)[0])
                    if uid not in user_ids:
                        user_ids.append(uid)
                except (ValueError, IndexError):
                    continue
    else:
        try:
            r = get_redis_client()
            key = f"presence:board:{board_id}"
            # Prune stale records before querying
            r.zremrangebyscore(key, 0, now - STALE_TIMEOUT_SECONDS)
            members = r.zrange(key, 0, -1)
            if not members:
                return []

            # Extract unique user IDs preserving order of appearance
            for m in members:
                m_str = m.decode("utf-8") if isinstance(m, bytes) else str(m)
                parts = m_str.split(":", 1)
                try:
                    uid = int(parts[0])
                    if uid not in user_ids:
                        user_ids.append(uid)
                except (ValueError, IndexError):
                    continue
        except Exception as exc:
            logger.warning(f"Redis presence tracking offline (get_board_presence board={board_id}): {exc}")
            return []

    if not user_ids:
        return []

    # Query User model for display attributes
    users = User.objects.filter(id__in=user_ids)
    user_map = {u.id: u for u in users}

    presence_list = []
    for uid in user_ids:
        if uid in user_map:
            u = user_map[uid]
            presence_list.append({
                "id": u.id,
                "username": u.username,
                "avatar_color": getattr(u, "avatar_color", "#64748B"),
                "initials": getattr(u, "initials", u.username[:2].upper()),
            })
    return presence_list
