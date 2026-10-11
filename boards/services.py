"""
Business logic service layer for TaskFlow board mutations.

Contains state-changing operations for lists, cards, comments, and members:
- create_list, rename_list, delete_list, move_list
- create_card, update_card, delete_card, move_card, assign_card
- add_comment, edit_comment, delete_comment
- change_member_role, remove_member, delete_board

ARCHITECTURAL PRINCIPLES:
-----------------------------------------------------------------------------
1. ONE VALIDATED WRITE PATH (CQRS):
   All mutations flow strictly through HTTP endpoints -> services.py -> database.
   WebSockets are used ONLY to push real-time event notifications to clients.
   Mutations directly over WebSockets are rejected.

2. SAME-TRANSACTION ACTIVITY AUDITING:
   Activity records are created inside the same transaction.atomic() block
   as the mutation via boards.activity.log_activity(...). Rolled-back operations
   leave zero trace in the audit log. Summaries are stored at write time to
   preserve historical integrity across future entity renames or deletions.

3. TRANSACTION ON_COMMIT BROADCASTING:
   All events.broadcast() and events.send_user_event() calls execute inside
   transaction.on_commit(). Clients never receive ghost events for rolled-back state.

4. CONTIGUOUS INTEGER ORDERING:
   Lists and cards maintain strictly contiguous 0-indexed positions protected
   by select_for_update() row locking.
"""

import re
from typing import Any, Optional, Set
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from .models import Board, BoardList, BoardMembership, Card, Comment, Notification
from . import events, activity


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
    Logs activity and broadcasts 'list.created' on commit.
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

        activity.log_activity(
            board=board,
            actor=user,
            verb="list.created",
            target_type="list",
            target_id=new_list.id,
            summary=f"created list '{new_list.title}'",
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
    Logs activity and broadcasts 'list.renamed' on commit.
    """
    board = board_list.board
    validate_editor_or_owner(board, user)

    clean_title = (new_title or "").strip()
    if not clean_title:
        raise ValueError("List title cannot be empty.")

    with transaction.atomic():
        old_title = board_list.title
        board_list.title = clean_title
        board_list.save(update_fields=["title"])

        activity.log_activity(
            board=board,
            actor=user,
            verb="list.renamed",
            target_type="list",
            target_id=board_list.id,
            summary=f"renamed list from '{old_title}' to '{board_list.title}'",
        )

        list_data = {
            "id": board_list.id,
            "title": board_list.title,
        }
        board_id = board.id
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
    Logs activity and broadcasts 'list.deleted' on commit.
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
        list_title = board_list.title
        board_list.delete()

        # Renumber remaining lists to ensure strictly contiguous positions
        remaining = [lst for lst in all_lists if lst.id != deleted_id]
        for idx, lst in enumerate(remaining):
            if lst.position != idx:
                lst.position = idx
                lst.save(update_fields=["position"])

        activity.log_activity(
            board=board,
            actor=user,
            verb="list.deleted",
            target_type="list",
            target_id=deleted_id,
            summary=f"deleted list '{list_title}'",
        )

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
    Logs activity and broadcasts 'list.moved' on commit.
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

        activity.log_activity(
            board=board,
            actor=user,
            verb="list.moved",
            target_type="list",
            target_id=list_id,
            summary=f"moved list '{board_list.title}' to position {clamped_position + 1}",
        )

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
    Logs activity and broadcasts 'card.created' on database commit.
    """
    board = board_list.board
    validate_editor_or_owner(board, user)

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

        activity.log_activity(
            board=board,
            actor=user,
            verb="card.created",
            target_type="card",
            target_id=card.id,
            summary=f"created card '{card.title}' in '{board_list.title}'",
        )

        card_data = {
            "id": card.id,
            "title": card.title,
            "description": card.description,
            "position": card.position,
            "list_id": card.board_list_id,
            "author": user.username,
            "author_initials": getattr(user, "initials", user.username[:2].upper()),
            "author_color": getattr(user, "avatar_color", "#4F46E5"),
            "assignee": None,
            "comments_count": 0,
        }
        board_id = board.id

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
    Logs activity and broadcasts 'card.updated' on commit.
    """
    board = card.board_list.board
    validate_editor_or_owner(board, user)

    clean_title = (title or "").strip()
    if not clean_title:
        raise ValueError("Card title cannot be empty.")

    with transaction.atomic():
        card.title = clean_title
        if description is not None:
            card.description = description.strip()

        card.save(update_fields=["title", "description", "updated_at"])

        activity.log_activity(
            board=board,
            actor=user,
            verb="card.updated",
            target_type="card",
            target_id=card.id,
            summary=f"updated card '{card.title}'",
        )

        card_data = {
            "id": card.id,
            "title": card.title,
            "description": card.description,
            "list_id": card.board_list_id,
        }
        board_id = board.id

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
    Delete a Card and renumber remaining cards in its parent list.
    Logs activity and broadcasts 'card.deleted' on commit.
    """
    board_list = card.board_list
    board = board_list.board
    validate_editor_or_owner(board, user)

    with transaction.atomic():
        all_cards = list(
            Card.objects.filter(board_list=board_list)
            .select_for_update()
            .order_by("position", "created_at")
        )

        deleted_id = card.id
        card_title = card.title
        card.delete()

        remaining = [c for c in all_cards if c.id != deleted_id]
        for idx, c in enumerate(remaining):
            if c.position != idx:
                c.position = idx
                c.save(update_fields=["position"])

        activity.log_activity(
            board=board,
            actor=user,
            verb="card.deleted",
            target_type="card",
            target_id=deleted_id,
            summary=f"deleted card '{card_title}' from '{board_list.title}'",
        )

        board_id = board.id
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
    Logs activity and broadcasts 'card.moved' on commit.
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
            summary = f"reordered card '{card.title}' in '{source_list.title}'"

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

            summary = f"moved card '{card.title}' from '{source_list.title}' to '{target_list.title}'"

        card.refresh_from_db()
        card_id = card.id
        final_pos = card.position
        board_id = board.id

        activity.log_activity(
            board=board,
            actor=user,
            verb="card.moved",
            target_type="card",
            target_id=card_id,
            summary=summary,
            metadata={
                "source_list_id": source_id,
                "target_list_id": target_id,
                "new_position": final_pos,
            },
        )

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


def assign_card(
    card: Card,
    user,
    assignee: Optional[Any],
    client_id: Optional[str] = None
) -> Card:
    """
    Assign a Card to a board member, or unassign (assignee=None).
    Verifies membership, logs activity, creates notification, and broadcasts 'card.assigned'.
    """
    board = card.board_list.board
    validate_editor_or_owner(board, user)

    with transaction.atomic():
        if assignee is not None:
            if not BoardMembership.objects.filter(board=board, user=assignee).exists():
                raise PermissionDenied("Assignee must be an active member of this board.")

        card.assignee = assignee
        card.save(update_fields=["assignee", "updated_at"])

        if assignee:
            summary = f"assigned '{card.title}' to {assignee.username}"
        else:
            summary = f"unassigned '{card.title}'"

        activity.log_activity(
            board=board,
            actor=user,
            verb="card.assigned",
            target_type="card",
            target_id=card.id,
            summary=summary,
            metadata={"assignee_id": assignee.id if assignee else None},
        )

        # Notify assignee if assigned to someone other than the assigner
        if assignee and assignee != user:
            notif = Notification.objects.create(
                recipient=assignee,
                board=board,
                card=card,
                actor=user,
                kind=Notification.Kind.ASSIGNED,
                message=f"{user.username} assigned you to '{card.title}'",
            )
            unread_count = Notification.objects.filter(recipient=assignee, is_read=False).count()
            notif_data = {
                "id": notif.id,
                "board_id": board.id,
                "card_id": card.id,
                "actor": {
                    "id": user.id,
                    "username": user.username,
                    "avatar_color": getattr(user, "avatar_color", "#64748B"),
                },
                "kind": notif.kind,
                "message": notif.message,
                "created_at": notif.created_at.isoformat(),
                "is_read": False,
            }
            target_user_id = assignee.id
            transaction.on_commit(
                lambda: events.send_user_event(
                    user_id=target_user_id,
                    event_type="notification.created",
                    payload={"notification": notif_data, "unread_count": unread_count},
                )
            )

        assignee_data = (
            {
                "id": assignee.id,
                "username": assignee.username,
                "avatar_color": getattr(assignee, "avatar_color", "#64748B"),
                "initials": getattr(assignee, "initials", assignee.username[:2].upper()),
            }
            if assignee
            else None
        )

        board_id = board.id
        card_id = card.id
        transaction.on_commit(
            lambda: events.broadcast(
                board_id=board_id,
                event_type="card.assigned",
                payload={"card_id": card_id, "assignee": assignee_data},
                actor_id=user.id,
                client_id=client_id,
            )
        )

    return card


# ==============================================================================
# COMMENT SERVICES
# ==============================================================================

def add_comment(
    card: Card,
    author,
    body: str,
    client_id: Optional[str] = None
) -> Comment:
    """
    Add a comment to a Card. Parses @mentions, creates notifications,
    logs activity, and broadcasts 'comment.added'.
    """
    board = card.board_list.board
    validate_editor_or_owner(board, author)

    clean_body = (body or "").strip()
    if not clean_body or len(clean_body) > 2000:
        raise ValidationError("Comment cannot be empty and must not exceed 2000 characters.")

    with transaction.atomic():
        comment = Comment.objects.create(
            card=card,
            author=author,
            body=clean_body,
        )

        # Detect @username mentions
        mentioned_usernames: Set[str] = set(re.findall(r"@([a-zA-Z0-9_]+)", clean_body))
        if mentioned_usernames:
            active_memberships = (
                BoardMembership.objects.filter(board=board, user__username__in=mentioned_usernames)
                .exclude(user=author)
                .select_related("user")
            )
            for membership in active_memberships:
                recipient = membership.user
                notif = Notification.objects.create(
                    recipient=recipient,
                    board=board,
                    card=card,
                    actor=author,
                    kind=Notification.Kind.MENTION,
                    message=f"{author.username} mentioned you in a comment on '{card.title}'",
                )
                unread_count = Notification.objects.filter(recipient=recipient, is_read=False).count()
                notif_payload = {
                    "notification": {
                        "id": notif.id,
                        "board_id": board.id,
                        "card_id": card.id,
                        "actor": {
                            "id": author.id,
                            "username": author.username,
                            "avatar_color": getattr(author, "avatar_color", "#64748B"),
                        },
                        "kind": notif.kind,
                        "message": notif.message,
                        "created_at": notif.created_at.isoformat(),
                        "is_read": False,
                    },
                    "unread_count": unread_count,
                }
                recip_id = recipient.id
                transaction.on_commit(
                    lambda rid=recip_id, pld=notif_payload: events.send_user_event(
                        user_id=rid,
                        event_type="notification.created",
                        payload=pld,
                    )
                )

        activity.log_activity(
            board=board,
            actor=author,
            verb="comment.added",
            target_type="comment",
            target_id=comment.id,
            summary=f"commented on '{card.title}'",
            metadata={"card_id": card.id, "comment_id": comment.id},
        )

        comments_count = card.comments.count()
        comment_data = {
            "id": comment.id,
            "card_id": card.id,
            "author": {
                "id": author.id,
                "username": author.username,
                "avatar_color": getattr(author, "avatar_color", "#64748B"),
                "initials": getattr(author, "initials", author.username[:2].upper()),
            },
            "body": comment.body,
            "created_at": comment.created_at.isoformat(),
            "edited_at": None,
        }

        board_id = board.id
        transaction.on_commit(
            lambda: events.broadcast(
                board_id=board_id,
                event_type="comment.added",
                payload={"comment": comment_data, "comments_count": comments_count},
                actor_id=author.id,
                client_id=client_id,
            )
        )

    return comment


def edit_comment(
    comment: Comment,
    user,
    body: str,
    client_id: Optional[str] = None
) -> Comment:
    """
    Edit an existing comment. Only the author may edit their own comment.
    Logs activity and broadcasts 'comment.edited'.
    """
    if comment.author != user:
        raise PermissionDenied("Only the author can edit their own comment.")

    clean_body = (body or "").strip()
    if not clean_body or len(clean_body) > 2000:
        raise ValidationError("Comment cannot be empty and must not exceed 2000 characters.")

    with transaction.atomic():
        comment.body = clean_body
        comment.edited_at = timezone.now()
        comment.save(update_fields=["body", "edited_at"])

        board = comment.card.board_list.board
        activity.log_activity(
            board=board,
            actor=user,
            verb="comment.edited",
            target_type="comment",
            target_id=comment.id,
            summary=f"edited a comment on '{comment.card.title}'",
            metadata={"card_id": comment.card.id, "comment_id": comment.id},
        )

        board_id = board.id
        card_id = comment.card.id
        comment_id = comment.id
        edited_iso = comment.edited_at.isoformat()

        transaction.on_commit(
            lambda: events.broadcast(
                board_id=board_id,
                event_type="comment.edited",
                payload={
                    "comment": {
                        "id": comment_id,
                        "card_id": card_id,
                        "body": clean_body,
                        "edited_at": edited_iso,
                    }
                },
                actor_id=user.id,
                client_id=client_id,
            )
        )

    return comment


def delete_comment(
    comment: Comment,
    user,
    client_id: Optional[str] = None
) -> bool:
    """
    Delete a comment. Allowed for the comment author OR the board OWNER.
    Logs activity and broadcasts 'comment.deleted'.
    """
    card = comment.card
    board = card.board_list.board

    is_author = (comment.author == user)
    is_owner = BoardMembership.objects.filter(
        board=board, user=user, role=BoardMembership.Role.OWNER
    ).exists()

    if not (is_author or is_owner):
        raise PermissionDenied("Only the author or board owner can delete this comment.")

    with transaction.atomic():
        comment_id = comment.id
        card_id = card.id
        card_title = card.title
        comment.delete()

        comments_count = card.comments.count()

        activity.log_activity(
            board=board,
            actor=user,
            verb="comment.deleted",
            target_type="comment",
            target_id=comment_id,
            summary=f"deleted a comment on '{card_title}'",
            metadata={"card_id": card_id, "comment_id": comment_id},
        )

        board_id = board.id
        transaction.on_commit(
            lambda: events.broadcast(
                board_id=board_id,
                event_type="comment.deleted",
                payload={
                    "comment_id": comment_id,
                    "card_id": card_id,
                    "comments_count": comments_count,
                },
                actor_id=user.id,
                client_id=client_id,
            )
        )

    return True


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
        target_username = membership.user.username
        board_id = board.id

        activity.log_activity(
            board=board,
            actor=user,
            verb="member.role_changed",
            target_type="member",
            target_id=user_id,
            summary=f"changed {target_username}'s role to {new_role}",
        )

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
                ),
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
        target_username = membership.user.username
        board_id = board.id
        membership.delete()

        activity.log_activity(
            board=board,
            actor=user,
            verb="member.removed",
            target_type="member",
            target_id=user_id,
            summary=f"removed {target_username} from the board",
        )

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
                ),
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
