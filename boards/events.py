"""
Real-time event broadcasting module for TaskFlow.

Contains:
- broadcast(board_id, event_type, payload, actor_id, client_id):
  Dispatches real-time board delta events to the channel layer group "board_<id>".
- send_user_event(user_id, event_type, payload):
  Dispatches targeted notifications to personal group "user_<id>" (e.g. role demotion/kick).

ARCHITECTURAL PRINCIPLES:
-----------------------------------------------------------------------------
1. TRANSACTION ON_COMMIT GUARANTEE:
   Broadcasts must be invoked inside transaction.on_commit() hooks in services.py.
   This guarantees that if an exception causes a database rollback, clients
   never receive phantom events for state that was discarded.

2. BROKER RESILIENCE (REDIS FAULT-TOLERANCE):
   If the Redis broker is offline, network partitions occur, or the channel layer
   fails, broadcasting errors are caught and logged as warnings.
   A failed broadcast MUST NEVER cause the database mutation to fail.

3. ECHO CANCELLATION:
   The originating browser sends an ephemeral 'client_id' with HTTP requests.
   This client_id is included in the broadcast envelope so the originating tab
   ignores its own echo, preventing duplicate DOM renders.

DOCUMENTED EVENT SCHEMAS:
-----------------------------------------------------------------------------
- list.created:       {"list": {"id": int, "title": str, "position": int}}
- list.renamed:       {"list": {"id": int, "title": str}}
- list.deleted:       {"list_id": int}
- list.moved:         {"list_id": int, "new_position": int}
- card.created:       {"card": {"id": int, "title": str, "description": str, "position": int,
                                "list_id": int, "author": str, "author_initials": str, "author_color": str}}
- card.updated:       {"card": {"id": int, "title": str, "description": str}}
- card.deleted:       {"card_id": int, "list_id": int}
- card.moved:         {"card_id": int, "source_list_id": int, "target_list_id": int, "new_position": int}
- member.role_changed:{"user_id": int, "new_role": str}
- member.removed:     {"user_id": int, "board_id": int}
- board.deleted:      {"board_id": int}
"""

import logging
from typing import Any, Dict, Optional
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

logger = logging.getLogger("taskflow.events")


def broadcast(
    board_id: int,
    event_type: str,
    payload: Dict[str, Any],
    actor_id: Optional[int] = None,
    client_id: Optional[str] = None,
) -> None:
    """
    Publish an event to all WebSocket clients connected to board_<board_id>.

    Args:
        board_id: Target board workspace ID.
        event_type: Identifier of the mutation (e.g. 'card.moved').
        payload: Event payload dictionary adhering to documented schema.
        actor_id: User ID of the member who performed the action.
        client_id: Ephemeral tab identifier for client-side echo cancellation.
    """
    channel_layer = get_channel_layer()
    if not channel_layer:
        logger.warning(f"No channel layer configured. Dropping event '{event_type}' for board={board_id}.")
        return

    envelope = {
        "event": event_type,
        "payload": payload,
        "board_id": board_id,
        "actor_id": actor_id,
        "client_id": client_id,
    }

    try:
        async_to_sync(channel_layer.group_send)(
            f"board_{board_id}",
            {
                "type": "board_event",
                "payload": envelope,
            }
        )
    except Exception as exc:
        # Gracefully swallow Redis/broker connectivity issues so HTTP operations never abort
        logger.warning(
            f"Channel layer broadcast failed for event='{event_type}' on board={board_id}: {exc}"
        )


def send_user_event(
    user_id: int,
    event_type: str,
    payload: Dict[str, Any],
) -> None:
    """
    Publish a targeted event to a specific user's open WebSocket tabs (user_<user_id>).

    Args:
        user_id: Target recipient user ID.
        event_type: Identifier (e.g. 'member.role_changed', 'member.removed').
        payload: Event payload dictionary.
    """
    channel_layer = get_channel_layer()
    if not channel_layer:
        return

    envelope = {
        "event": event_type,
        "payload": payload,
        "user_id": user_id,
    }

    try:
        async_to_sync(channel_layer.group_send)(
            f"user_{user_id}",
            {
                "type": "user_event",
                "payload": envelope,
            }
        )
    except Exception as exc:
        logger.warning(
            f"Channel layer send_user_event failed for event='{event_type}' to user={user_id}: {exc}"
        )
