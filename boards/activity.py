"""
Activity logging engine for TaskFlow boards.

Provides centralized audit recording via log_activity(...).

ARCHITECTURAL PRINCIPLES:
-----------------------------------------------------------------------------
1. SAME-TRANSACTION DURABILITY:
   Activity records are created inside the exact same transaction.atomic()
   block as the domain mutation in services.py. If the mutation fails and
   rolls back, the activity record is rolled back with it. No phantom logs.

2. WRITE-TIME SUMMARY FREEZING:
   The human-readable summary string (e.g. "moved 'Fix login' from To Do to Doing")
   is computed and stored at write time rather than constructed dynamically on read.
   Why? Because cards, lists, and members can subsequently be renamed, reassigned,
   or deleted from the database. Dynamic rendering would alter historical fact or
   raise ObjectDoesNotExist errors. Write-time snapshots preserve an immutable,
   accurate audit trail.

3. TRANSACTION ON_COMMIT BROADCAST:
   The 'activity.created' broadcast event is dispatched inside transaction.on_commit().
   Connected browser tabs receive the event in real time and prepend it to the
   Activity slide-out panel immediately.
"""

import logging
from typing import Any, Dict, Optional
from django.contrib.auth import get_user_model
from django.db import transaction

from .models import Activity, Board
from . import events

logger = logging.getLogger("taskflow.activity")
User = get_user_model()


def log_activity(
    board: Board,
    actor: Optional[Any],
    verb: str,
    target_type: str,
    target_id: Optional[int],
    summary: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> Activity:
    """
    Record an immutable audit activity and schedule a live 'activity.created' broadcast.

    Args:
        board: The Board workspace where the activity occurred.
        actor: The User who initiated the change (None for system actions).
        verb: Machine-readable action code (Activity.Verb or str).
        target_type: Entity category (card, list, comment, member, board).
        target_id: Primary key of target item at time of action.
        summary: Human-readable narrative frozen at write time.
        metadata: Optional dictionary with supplementary structured details.

    Returns:
        The newly persisted Activity instance.
    """
    if metadata is None:
        metadata = {}

    activity = Activity.objects.create(
        board=board,
        actor=actor,
        verb=verb,
        target_type=target_type,
        target_id=target_id,
        summary=summary,
        metadata=metadata,
    )

    actor_data = {
        "id": actor.id if actor else None,
        "username": actor.username if actor else "System",
        "avatar_color": getattr(actor, "avatar_color", "#64748B"),
        "initials": getattr(actor, "initials", (actor.username[:2].upper() if actor else "SY")),
    }

    payload = {
        "activity": {
            "id": activity.id,
            "verb": activity.verb,
            "actor": actor_data,
            "summary": activity.summary,
            "created_at": activity.created_at.isoformat(),
            "time_ago": "just now",
            "metadata": activity.metadata,
        }
    }

    # Dispatch real-time event to board WebSocket group only after DB commit succeeds
    transaction.on_commit(
        lambda: events.broadcast(
            board_id=board.id,
            event_type="activity.created",
            payload=payload,
            actor_id=actor.id if actor else None,
        )
    )

    return activity
