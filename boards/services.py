"""
Business logic service layer for TaskFlow board mutations.

Contains state-changing operations for lists and cards:
- create_list, rename_list, delete_list, move_list
- create_card, update_card, delete_card, move_card

ARCHITECTURAL RATIONALE & ORDERING STRATEGY TRADEOFF ANALYSIS:
-----------------------------------------------------------------------------
1. WHY A DEDICATED SERVICE LAYER?
   Views act purely as thin HTTP/JSON adapters, delegating all domain logic and
   validation to this module. This design guarantees consistent validation across
   standard HTTP requests, AJAX endpoints, management commands, and future
   WebSocket consumers in Phase 3. Each function returns the updated domain
   entity, enabling Phase 3 event channels to broadcast mutations seamlessly.

2. CONTIGUOUS INTEGER ORDERING (0, 1, 2, ... N-1) VS. ALTERNATIVES:
   - Contiguous Integer Renumbering (Selected):
     * How it works: Items have consecutive integer positions. When an item is
       moved or deleted, the gap is closed, the item is inserted at the destination,
       and affected rows are renumbered (0, 1, 2...).
     * Benefits: Completely deterministic, human-readable, zero risk of
       floating-point precision exhaustion (e.g. 0.0000000000001 underflow in
       fractional indexing), and requires no background rebalancing jobs.
     * Tradeoff: Moves require O(N) database updates for items in the affected
       column. However, in Kanban workflows where a column rarely exceeds 50-100
       cards, an indexed batch update executes in <5ms.
   - Fractional / Float Indexing (Rejected for Phase 2):
     * Generates midpoints (e.g. (pos_a + pos_b) / 2). Avoids multi-row writes
       initially, but rapidly collapses due to IEEE 754 precision limits when
       items are repeatedly dropped into the same slot, requiring complex
       rebalancing algorithms.
   - LexoRank / String Keys (Rejected for Phase 2):
     * Avoids multi-row writes by interleaving alphanumeric strings (e.g. '0|aaaaa:'),
       but introduces string storage bloat, rebalancing logic, and query complexity.

3. CONCURRENCY & RACE CONDITIONS:
   Multi-row renumbering is wrapped in `transaction.atomic()` with `select_for_update()`.
   This locks the rows of the affected board or list, preventing two concurrent drags
   from producing duplicate positions or corrupted sequences.
"""

from typing import Optional
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import QuerySet

from .models import Board, BoardList, BoardMembership, Card


# ==============================================================================
# PERMISSION VALIDATION HELPER
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
        # User is not a member of this board
        raise PermissionDenied("You are not a member of this board.")

    if membership.role not in [BoardMembership.Role.OWNER, BoardMembership.Role.EDITOR]:
        raise PermissionDenied("You must have OWNER or EDITOR permissions to perform this action.")

    return membership


# ==============================================================================
# BOARD LIST SERVICES
# ==============================================================================

def create_list(board: Board, title: str, user) -> BoardList:
    """
    Create a new BoardList appended at the end of the board.

    Args:
        board: Target board workspace.
        title: Title of the new column.
        user: Acting user performing the action.

    Returns:
        The newly created BoardList instance.
    """
    validate_editor_or_owner(board, user)

    clean_title = (title or "").strip()
    if not clean_title:
        raise ValueError("List title cannot be empty.")

    with transaction.atomic():
        # Lock existing lists on the board to avoid race condition on position assignment
        existing_lists = BoardList.objects.filter(board=board).select_for_update()
        position = existing_lists.count()

        new_list = BoardList.objects.create(
            board=board,
            title=clean_title,
            position=position,
        )

    return new_list


def rename_list(board_list: BoardList, new_title: str, user) -> BoardList:
    """
    Rename an existing BoardList column.

    Args:
        board_list: The BoardList instance to rename.
        new_title: The updated column title.
        user: Acting user performing the action.

    Returns:
        The updated BoardList instance.
    """
    validate_editor_or_owner(board_list.board, user)

    clean_title = (new_title or "").strip()
    if not clean_title:
        raise ValueError("List title cannot be empty.")

    board_list.title = clean_title
    board_list.save(update_fields=["title"])
    return board_list


def delete_list(board_list: BoardList, user) -> None:
    """
    Delete a BoardList (and all cards inside it), renumbering remaining lists
    to maintain contiguous zero-indexed positions.

    Args:
        board_list: The BoardList instance to delete.
        user: Acting user performing the action.
    """
    board = board_list.board
    validate_editor_or_owner(board, user)

    with transaction.atomic():
        # Lock all lists for this board
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


def move_list(board_list: BoardList, new_position: int, user) -> BoardList:
    """
    Reorder a BoardList column within its board to a new zero-based index.
    Renumbers all sibling lists to guarantee strictly contiguous ordering.

    Args:
        board_list: The BoardList instance to reposition.
        new_position: The desired target index (0-indexed).
        user: Acting user performing the action.

    Returns:
        The updated BoardList instance with its new position.
    """
    board = board_list.board
    validate_editor_or_owner(board, user)

    with transaction.atomic():
        # Lock all lists belonging to this board
        lists = list(
            BoardList.objects.filter(board=board)
            .select_for_update()
            .order_by("position", "created_at")
        )

        # Locate the item in the locked queryset
        current_index = next((i for i, lst in enumerate(lists) if lst.id == board_list.id), None)
        if current_index is None:
            raise ValueError("List not found on the specified board.")

        # Clamp new_position within bounds [0, len(lists) - 1]
        clamped_position = max(0, min(int(new_position), len(lists) - 1))

        if current_index != clamped_position:
            moved_item = lists.pop(current_index)
            lists.insert(clamped_position, moved_item)

            for idx, lst in enumerate(lists):
                if lst.position != idx:
                    lst.position = idx
                    lst.save(update_fields=["position"])

        board_list.position = clamped_position

    return board_list


# ==============================================================================
# CARD SERVICES
# ==============================================================================

def create_card(board_list: BoardList, title: str, description: str, user) -> Card:
    """
    Create a new Card appended at the bottom of the given list.

    Args:
        board_list: The column container for the new card.
        title: Title of the task card.
        description: Optional details or acceptance criteria.
        user: Acting user (saved as created_by author).

    Returns:
        The newly created Card instance.
    """
    validate_editor_or_owner(board_list.board, user)

    clean_title = (title or "").strip()
    if not clean_title:
        raise ValueError("Card title cannot be empty.")

    clean_desc = (description or "").strip()

    with transaction.atomic():
        # Lock cards in this list to accurately compute the next position
        existing_cards = Card.objects.filter(board_list=board_list).select_for_update()
        position = existing_cards.count()

        card = Card.objects.create(
            board_list=board_list,
            title=clean_title,
            description=clean_desc,
            position=position,
            created_by=user,
        )

    return card


def update_card(card: Card, title: str, description: Optional[str], user) -> Card:
    """
    Update a Card's title and/or description.

    Args:
        card: The Card instance to edit.
        title: Updated task title.
        description: Updated description body.
        user: Acting user performing the action.

    Returns:
        The updated Card instance.
    """
    validate_editor_or_owner(card.board_list.board, user)

    clean_title = (title or "").strip()
    if not clean_title:
        raise ValueError("Card title cannot be empty.")

    card.title = clean_title
    if description is not None:
        card.description = description.strip()

    card.save(update_fields=["title", "description", "updated_at"])
    return card


def delete_card(card: Card, user) -> None:
    """
    Delete a Card and renumber remaining cards in its parent list to close the gap.

    Args:
        card: The Card instance to delete.
        user: Acting user performing the action.
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

        # Renumber remaining cards contiguous
        remaining = [c for c in all_cards if c.id != deleted_id]
        for idx, c in enumerate(remaining):
            if c.position != idx:
                c.position = idx
                c.save(update_fields=["position"])


def move_card(card: Card, target_list: BoardList, new_position: int, user) -> Card:
    """
    Move a Card to a new position, either within the same list or across lists.

    SECURITY INVARIANT:
    Ensures both source list and target list belong to the EXACT same board.
    Cross-board card injection is strictly rejected.

    Args:
        card: The Card instance being repositioned.
        target_list: The destination BoardList column.
        new_position: The desired target index (0-indexed).
        user: Acting user performing the action.

    Returns:
        The updated Card instance with its new list and position.
    """
    source_list = card.board_list
    board = source_list.board

    validate_editor_or_owner(board, user)

    if target_list.board_id != board.id:
        raise ValueError("Target list does not belong to the same board.")

    with transaction.atomic():
        if source_list.id == target_list.id:
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

            # Update the card's foreign key and position
            card.board_list = target_list
            card.position = clamped_position
            card.save(update_fields=["board_list", "position", "updated_at"])

            # 3. Renumber all target cards
            for idx, c in enumerate(target_cards):
                if c.id != card.id and c.position != idx:
                    c.position = idx
                    c.save(update_fields=["position"])

        card.refresh_from_db()

    return card
