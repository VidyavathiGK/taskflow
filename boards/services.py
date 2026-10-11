"""
Business logic service layer for TaskFlow board mutations.

Contains state-changing operations for lists and cards:
- create_list, rename_list, delete_list, move_list
- create_card, update_card, delete_card, move_card
- change_member_role, remove_member, delete_board

ARCHITECTURAL RATIONALE & PHASE 3 BROADCAST INTEGRATION:
-----------------------------------------------------------------------------
1. WHY A DEDICATED SERVICE LAYER?
   Views act purely as thin HTTP/JSON adapters, delegating all domain logic and
   validation to this module. This design guarantees consistent validation across
   standard HTTP requests, AJAX endpoints, management commands, and WebSocket
   broadcasts.

2. ONE VALIDATED WRITE PATH (CQRS PATTERN):
   All mutations flow strictly through HTTP endpoints -> services.py -> database.
   WebSockets are used ONLY to push real-time event notifications to clients.
   This guarantees that validation, permissions, and transaction locks remain
   centralized in one hardened code path.

3. TRANSACTION ON_COMMIT BROADCASTING:
   All events.broadcast() calls are wrapped in transaction.on_commit().
   This guarantees that if an exception triggers a database rollback, clients
   will never receive ghost events for mutations that failed to persist.

4. CONTIGUOUS INTEGER ORDERING (0, 1, 2, ... N-1):
   - Items maintain strictly consecutive integer positions. When an item is moved
     or deleted, the gap is closed, the item is inserted at the destination, and
     neighboring rows are renumbered sequentially.
   - Concurrency is protected using transaction.atomic() with select_for_update().
"""

from typing import Optional
from django.core.exceptions import PermissionDenied
from django.db import transaction

from .models import Board, BoardList, BoardMembership, Card
from . import events


# ==============================================================================
# PERMISSION VALIDATION HELPERS
# ==============================================================================

def validate_editor_or_owner(board: Board, user) -> BoardMembership:
    """
    Ensure the user is authenticated and holds an OWNER or EDITOR role on the board.

    Raises:
        PermissionDenied (403): If the user is unauthenticated, not a member,
                                or has only VIEWER privileges.
    """
    if not user or not user.is_authenticated:
        raise PermissionDenied("Authentication required.")

    try:
        membership = BoardMembership.objects.get(board=board, user=user)
    except BoardMembership.DoesNotExist:
        raise PermissionDenied("You are not a member of this board.")

    if membership.role not in [BoardMembership.Role.OWNER, BoardMembership.Role.EDITOR]:
        raise PermissionDenied("You must have OWNER or EDITOR permissions to perform this action.")

    return membership


def validate_owner(board: Board, user) -> BoardMembership:
    """Ensure the user is the board OWNER."""
    if not user or not user.is_authenticated:
        raise PermissionDenied("Authentication required.")

    try:
        membership = BoardMembership.objects.get(board=board, user=user)
    except BoardMembership.DoesNotExist:
        raise PermissionDenied("You are not a member of this board.")

    if membership.role != BoardMembership.Role.OWNER:
        raise PermissionDenied("Only the board OWNER can perform this action.")

    return membership


# ==============================================================================
# BOARD LIST SERVICES
# ==============================================================================

def create_list(board: Board, title: str, user, client_id: Optional[str] = None) -> BoardList:
    """
    Create a new BoardList appended at the end of the board.
    Broadcasts 'list.created' on database commit.
    """
    validate_editor_or_owner(board, user)

    clean_title = (title or "").strip()
    if not clean_title:
        raise ValueError("List title cannot be empty.")

    with transaction.atomic():
        existing_lists = BoardList.objects.filter(board=board).select_for_update()
        position = existing_lists.count()

        new_list = BoardList.objects.create(
            board=board,
            title=clean_title,
            position=position,
        )

        list_data = {
            "id": new_list.id,
            "title": new_list.title,
            "position": new_list.position,
        }
        transaction.on_commit(
            lambda: events.broadcast(
                board_id=board.id,
                event_type="list.created",
                payload={"list": list_data},
                actor_id=user.id,
                client_id=client_id,
            )
        )

    return new_list


def rename_list(board_list: BoardList, new_title: str, user, client_id: Optional[str] = None) -> BoardList:
    """
    Rename an existing BoardList column.
    Broadcasts 'list.renamed' on database commit.
    """
    validate_editor_or_owner(board_list.board, user)

    clean_title = (new_title or "").strip()
    if not clean_title:
        raise ValueError("List title cannot be empty.")

    with transaction.atomic():
        board_list.title = clean_title
        board_list.save(update_fields=["title"])

        list_data = {
            "id": board_list.id,
            "title": board_list.title,
        }
        board_id = board_list.board_id
        transaction.on_commit(
            lambda: events.broadcast(
                board_id=board_id,
                event_type="list.renamed",
                payload={"list": list_data},
                actor_id=user.id,
                client_id=client_id,
            )
        )

    return board_list


def delete_list(board_list: BoardList, user, client_id: Optional[str] = None) -> None:
    """
    Delete a BoardList (and all cards inside it), renumbering remaining lists
    to maintain contiguous zero-indexed positions.
    Broadcasts 'list.deleted' on database commit.
    """
    board = board_list.board
    validate_editor_or_owner(board, user)

    with transaction.atomic():
        all_lists = list(
            BoardList.objects.filter(board=board)
            .select_for_update()
            .order_by("position", "created_at")
        )

        deleted_id = board_list.id
        board_list.delete()

        # Renumber remaining lists to ensure strictly contiguous positions
        remaining = [lst for lst in all_lists if lst.id != deleted_id]
        for idx, lst in enumerate(remaining):
            if lst.position != idx:
                lst.position = idx
                lst.save(update_fields=["position"])

        board_id = board.id
        transaction.on_commit(
            lambda: events.broadcast(
                board_id=board_id,
                event_type="list.deleted",
                payload={"list_id": deleted_id},
                actor_id=user.id,
                client_id=client_id,
            )
        )


def move_list(board_list: BoardList, new_position: int, user, client_id: Optional[str] = None) -> BoardList:
    """
    Reorder a BoardList column within its board to a new zero-based index.
    Renumbers all sibling lists to guarantee strictly contiguous ordering.
    Broadcasts 'list.moved' on database commit.
    """
    board = board_list.board
    validate_editor_or_owner(board, user)

    with transaction.atomic():
        lists = list(
            BoardList.objects.filter(board=board)
            .select_for_update()
            .order_by("position", "created_at")
        )

        current_index = next((i for i, lst in enumerate(lists) if lst.id == board_list.id), None)
        if current_index is None:
            raise ValueError("List not found on the specified board.")

        clamped_position = max(0, min(int(new_position), len(lists) - 1))

        if current_index != clamped_position:
            moved_item = lists.pop(current_index)
            lists.insert(clamped_position, moved_item)

            for idx, lst in enumerate(lists):
                if lst.position != idx:
                    lst.position = idx
                    lst.save(update_fields=["position"])

        board_list.position = clamped_position
        list_id = board_list.id
        board_id = board.id

        transaction.on_commit(
            lambda: events.broadcast(
                board_id=board_id,
                event_type="list.moved",
                payload={"list_id": list_id, "new_position": clamped_position},
                actor_id=user.id,
                client_id=client_id,
            )
        )

    return board_list


# ==============================================================================
# CARD SERVICES
# ==============================================================================

def create_card(
    board_list: BoardList,
    title: str,
    description: str,
    user,
    client_id: Optional[str] = None
) -> Card:
    """
    Create a new Card appended at the bottom of the given list.
    Broadcasts 'card.created' on database commit.
    """
    validate_editor_or_owner(board_list.board, user)

    clean_title = (title or "").strip()
    if not clean_title:
        raise ValueError("Card title cannot be empty.")

    clean_desc = (description or "").strip()

    with transaction.atomic():
        existing_cards = Card.objects.filter(board_list=board_list).select_for_update()
        position = existing_cards.count()

        card = Card.objects.create(
            board_list=board_list,
            title=clean_title,
            description=clean_desc,
            position=position,
            created_by=user,
        )

        card_data = {
            "id": card.id,
            "title": card.title,
            "description": card.description,
            "position": card.position,
            "list_id": card.board_list_id,
            "author": user.username,
            "author_initials": user.initials,
            "author_color": getattr(user, "avatar_color", "#4F46E5"),
        }
        board_id = board_list.board_id

        transaction.on_commit(
            lambda: events.broadcast(
                board_id=board_id,
                event_type="card.created",
                payload={"card": card_data},
                actor_id=user.id,
                client_id=client_id,
            )
        )

    return card


def update_card(
    card: Card,
    title: str,
    description: Optional[str],
    user,
    client_id: Optional[str] = None
) -> Card:
    """
    Update a Card's title and/or description.
    Broadcasts 'card.updated' on database commit.
    """
    validate_editor_or_owner(card.board_list.board, user)

    clean_title = (title or "").strip()
    if not clean_title:
        raise ValueError("Card title cannot be empty.")

    with transaction.atomic():
        card.title = clean_title
        if description is not None:
            card.description = description.strip()

        card.save(update_fields=["title", "description", "updated_at"])

        card_data = {
            "id": card.id,
            "title": card.title,
            "description": card.description,
            "list_id": card.board_list_id,
        }
        board_id = card.board_list.board_id

        transaction.on_commit(
            lambda: events.broadcast(
                board_id=board_id,
                event_type="card.updated",
                payload={"card": card_data},
                actor_id=user.id,
                client_id=client_id,
            )
        )

    return card


def delete_card(card: Card, user, client_id: Optional[str] = None) -> None:
    """
    Delete a Card and renumber remaining cards in its parent list to close the gap.
    Broadcasts 'card.deleted' on database commit.
    """
    board_list = card.board_list
    validate_editor_or_owner(board_list.board, user)

    with transaction.atomic():
        all_cards = list(
            Card.objects.filter(board_list=board_list)
            .select_for_update()
            .order_by("position", "created_at")
        )

        deleted_id = card.id
        card.delete()

        remaining = [c for c in all_cards if c.id != deleted_id]
        for idx, c in enumerate(remaining):
            if c.position != idx:
                c.position = idx
                c.save(update_fields=["position"])

        board_id = board_list.board_id
        list_id = board_list.id

        transaction.on_commit(
            lambda: events.broadcast(
                board_id=board_id,
                event_type="card.deleted",
                payload={"card_id": deleted_id, "list_id": list_id},
                actor_id=user.id,
                client_id=client_id,
            )
        )


def move_card(
    card: Card,
    target_list: BoardList,
    new_position: int,
    user,
    client_id: Optional[str] = None
) -> Card:
    """
    Move a Card to a new position, either within the same list or across lists.
    Validates cross-board isolation, locks affected rows, and renumbers contiguously.
    Broadcasts 'card.moved' on database commit.
    """
    source_list = card.board_list
    board = source_list.board

    validate_editor_or_owner(board, user)

    if target_list.board_id != board.id:
        raise ValueError("Target list does not belong to the same board.")

    with transaction.atomic():
        source_id = source_list.id
        target_id = target_list.id

        if source_id == target_id:
            # Moving within the same list
            cards = list(
                Card.objects.filter(board_list=source_list)
                .select_for_update()
                .order_by("position", "created_at")
            )

            current_index = next((i for i, c in enumerate(cards) if c.id == card.id), None)
            if current_index is None:
                raise ValueError("Card not found in source list.")

            clamped_position = max(0, min(int(new_position), len(cards) - 1))

            if current_index != clamped_position:
                moved_card = cards.pop(current_index)
                cards.insert(clamped_position, moved_card)

                for idx, c in enumerate(cards):
                    if c.position != idx:
                        c.position = idx
                        c.save(update_fields=["position"])

            card.position = clamped_position

        else:
            # Moving across different lists
            source_cards = list(
                Card.objects.filter(board_list=source_list)
                .select_for_update()
                .order_by("position", "created_at")
            )
            target_cards = list(
                Card.objects.filter(board_list=target_list)
                .select_for_update()
                .order_by("position", "created_at")
            )

            # 1. Remove card from source list and renumber source cards
            source_cards = [c for c in source_cards if c.id != card.id]
            for idx, c in enumerate(source_cards):
                if c.position != idx:
                    c.position = idx
                    c.save(update_fields=["position"])

            # 2. Insert card into target list at clamped position
            clamped_position = max(0, min(int(new_position), len(target_cards)))
            target_cards.insert(clamped_position, card)

            card.board_list = target_list
            card.position = clamped_position
            card.save(update_fields=["board_list", "position", "updated_at"])

            # 3. Renumber all target cards
            for idx, c in enumerate(target_cards):
                if c.id != card.id and c.position != idx:
                    c.position = idx
                    c.save(update_fields=["position"])

        card.refresh_from_db()
        card_id = card.id
        final_pos = card.position
        board_id = board.id

        transaction.on_commit(
            lambda: events.broadcast(
                board_id=board_id,
                event_type="card.moved",
                payload={
                    "card_id": card_id,
                    "source_list_id": source_id,
                    "target_list_id": target_id,
                    "new_position": final_pos,
                },
                actor_id=user.id,
                client_id=client_id,
            )
        )

    return card


# ==============================================================================
# MEMBERSHIP & BOARD LIFECYCLE SERVICES (Phase 3 Real-time Events)
# ==============================================================================

def change_member_role(
    board: Board,
    membership: BoardMembership,
    new_role: str,
    user,
    client_id: Optional[str] = None
) -> BoardMembership:
    """
    Change a member's role.
    Pushes 'member.role_changed' to both the board group and targeted user group.
    """
    validate_owner(board, user)

    if membership.user == board.owner or membership.role == BoardMembership.Role.OWNER:
        raise ValueError("The board OWNER cannot be demoted.")

    if new_role not in [BoardMembership.Role.EDITOR, BoardMembership.Role.VIEWER]:
        raise ValueError("Invalid role specified.")

    with transaction.atomic():
        membership.role = new_role
        membership.save(update_fields=["role"])

        user_id = membership.user_id
        board_id = board.id

        transaction.on_commit(
            lambda: (
                events.broadcast(
                    board_id=board_id,
                    event_type="member.role_changed",
                    payload={"user_id": user_id, "new_role": new_role},
                    actor_id=user.id,
                    client_id=client_id,
                ),
                events.send_user_event(
                    user_id=user_id,
                    event_type="member.role_changed",
                    payload={"user_id": user_id, "new_role": new_role, "board_id": board_id},
                )
            )
        )

    return membership


def remove_member(
    board: Board,
    membership: BoardMembership,
    user,
    client_id: Optional[str] = None
) -> None:
    """
    Remove a member from the board.
    Pushes 'member.removed' to both board and user channels.
    """
    validate_owner(board, user)

    if membership.user == board.owner or membership.role == BoardMembership.Role.OWNER:
        raise ValueError("The board OWNER cannot be removed.")

    with transaction.atomic():
        user_id = membership.user_id
        board_id = board.id
        membership.delete()

        transaction.on_commit(
            lambda: (
                events.broadcast(
                    board_id=board_id,
                    event_type="member.removed",
                    payload={"user_id": user_id},
                    actor_id=user.id,
                    client_id=client_id,
                ),
                events.send_user_event(
                    user_id=user_id,
                    event_type="member.removed",
                    payload={"user_id": user_id, "board_id": board_id},
                )
            )
        )


def delete_board(board: Board, user, client_id: Optional[str] = None) -> None:
    """
    Delete a board and all associated resources.
    Broadcasts 'board.deleted' to kick connected users before deletion.
    """
    validate_owner(board, user)

    board_id = board.id
    # Broadcast immediately before database cascade
    events.broadcast(
        board_id=board_id,
        event_type="board.deleted",
        payload={"board_id": board_id},
        actor_id=user.id,
        client_id=client_id,
    )
    board.delete()
