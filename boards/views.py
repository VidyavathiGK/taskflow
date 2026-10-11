"""
Views for Board workspace CRUD, Member management, and Phases 2, 3 & 4 JSON API endpoints.
"""

import json
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count, Prefetch
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from .forms import AddMemberForm, BoardForm, UpdateMemberRoleForm
from .models import Activity, Board, BoardList, BoardMembership, Card, Comment, Notification
from .permissions import BoardAccessMixin, BoardOwnerRequiredMixin
from . import events, presence, services

User = get_user_model()


# ==============================================================================
# HTML TEMPLATE VIEWS (Phase 1)
# ==============================================================================

class MyBoardsView(LoginRequiredMixin, View):
    """
    Displays the dashboard of all boards where the authenticated user holds a membership.
    """
    template_name = "boards/my_boards.html"

    def get(self, request):
        memberships = (
            BoardMembership.objects.filter(user=request.user)
            .select_related("board", "board__owner")
            .order_by("-board__updated_at")
        )
        return render(request, self.template_name, {"memberships": memberships})


class BoardCreateView(LoginRequiredMixin, View):
    """
    Creates a new Board workspace.
    """
    template_name = "boards/board_create.html"

    def get(self, request):
        form = BoardForm()
        return render(request, self.template_name, {"form": form})

    def post(self, request):
        form = BoardForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                board = form.save(commit=False)
                board.owner = request.user
                board.save()

                BoardMembership.objects.create(
                    board=board,
                    user=request.user,
                    role=BoardMembership.Role.OWNER,
                )

                messages.success(request, f"Board '{board.title}' created successfully.")
                return redirect("boards:board_detail", board_id=board.id)

        return render(request, self.template_name, {"form": form})


class BoardDetailView(BoardAccessMixin, View):
    """
    Displays the Kanban board workspace with all lists, cards, members, presence, and activity.
    Optimized with Prefetch to prevent N+1 query bottlenecks (target: <= 4 queries).
    """
    template_name = "boards/board_detail.html"

    def get(self, request, board_id):
        lists = (
            self.board.lists.prefetch_related(
                Prefetch(
                    "cards",
                    queryset=Card.objects.select_related("created_by", "assignee")
                    .annotate(comments_count=Count("comments"))
                    .order_by("position", "created_at")
                )
            ).order_by("position", "created_at")
        )
        memberships = (
            self.board.memberships.select_related("user")
            .order_by("joined_at")
        )

        context = {
            "board": self.board,
            "lists": lists,
            "memberships": memberships,
            "current_membership": self.membership,
            "is_owner": self.membership.role == BoardMembership.Role.OWNER,
            "is_editor": self.membership.role in [
                BoardMembership.Role.OWNER,
                BoardMembership.Role.EDITOR,
            ],
            "can_edit": self.membership.role in [
                BoardMembership.Role.OWNER,
                BoardMembership.Role.EDITOR,
            ],
        }
        return render(request, self.template_name, context)


class BoardUpdateView(BoardOwnerRequiredMixin, View):
    """Allows the board OWNER to modify the board title and description."""
    template_name = "boards/board_edit.html"

    def get(self, request, board_id):
        form = BoardForm(instance=self.board)
        return render(request, self.template_name, {"form": form, "board": self.board})

    def post(self, request, board_id):
        form = BoardForm(request.POST, instance=self.board)
        if form.is_valid():
            form.save()
            messages.success(request, "Board details updated successfully.")
            return redirect("boards:board_detail", board_id=self.board.id)
        return render(request, self.template_name, {"form": form, "board": self.board})


class BoardDeleteView(BoardOwnerRequiredMixin, View):
    """Allows the board OWNER to delete the board and all associated resources."""
    template_name = "boards/board_confirm_delete.html"

    def get(self, request, board_id):
        return render(request, self.template_name, {"board": self.board})

    def post(self, request, board_id):
        services.delete_board(self.board, request.user)
        messages.success(request, f"Board '{self.board.title}' was deleted.")
        return redirect("boards:my_boards")


class BoardMembersView(BoardOwnerRequiredMixin, View):
    """
    Manage Members page (strictly restricted to board OWNER).
    """
    template_name = "boards/manage_members.html"

    def get_context(self, add_form=None):
        memberships = (
            self.board.memberships.select_related("user")
            .order_by("joined_at")
        )
        return {
            "board": self.board,
            "memberships": memberships,
            "add_form": add_form or AddMemberForm(board=self.board),
            "role_choices": [
                (BoardMembership.Role.EDITOR, "Editor"),
                (BoardMembership.Role.VIEWER, "Viewer"),
            ],
        }

    def get(self, request, board_id):
        return render(request, self.template_name, self.get_context())

    def post(self, request, board_id):
        action = request.POST.get("action")

        # Action 1: Add a new member
        if action == "add_member":
            add_form = AddMemberForm(request.POST, board=self.board)
            if add_form.is_valid():
                target_user = add_form.target_user
                role = add_form.cleaned_data["role"]
                BoardMembership.objects.create(
                    board=self.board,
                    user=target_user,
                    role=role,
                )
                messages.success(
                    request,
                    f"Successfully added @{target_user.username} as {role}."
                )
                return redirect("boards:manage_members", board_id=self.board.id)
            return render(request, self.template_name, self.get_context(add_form=add_form))

        # Action 2: Change role of an existing member
        elif action == "change_role":
            membership_id = request.POST.get("membership_id")
            new_role = request.POST.get("role")
            membership = get_object_or_404(
                BoardMembership,
                id=membership_id,
                board=self.board
            )

            try:
                services.change_member_role(self.board, membership, new_role, request.user)
                messages.success(
                    request,
                    f"Updated role for @{membership.user.username} to {new_role}."
                )
            except ValueError as e:
                messages.error(request, str(e))
            return redirect("boards:manage_members", board_id=self.board.id)

        # Action 3: Remove an existing member
        elif action == "remove_member":
            membership_id = request.POST.get("membership_id")
            membership = get_object_or_404(
                BoardMembership,
                id=membership_id,
                board=self.board
            )

            username = membership.user.username
            try:
                services.remove_member(self.board, membership, request.user)
                messages.success(request, f"Removed @{username} from the board.")
            except ValueError as e:
                messages.error(request, str(e))
            return redirect("boards:manage_members", board_id=self.board.id)

        messages.error(request, "Unknown action requested.")
        return redirect("boards:manage_members", board_id=self.board.id)


# ==============================================================================
# BASE JSON API VIEW
# ==============================================================================

class BoardApiView(BoardAccessMixin, View):
    """Base class for all JSON endpoints enforcing authentication and membership."""
    def parse_payload(self, request):
        try:
            return json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return None

    def get_client_id(self, request, payload=None):
        header_val = request.headers.get("X-Client-ID")
        if header_val:
            return header_val
        if payload and isinstance(payload, dict):
            return payload.get("client_id")
        return None

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return JsonResponse({"ok": False, "error": "Authentication required."}, status=401)
        try:
            return super().dispatch(request, *args, **kwargs)
        except PermissionDenied as e:
            return JsonResponse({"ok": False, "error": str(e)}, status=403)
        except (ValidationError, ValueError) as e:
            msg = e.messages[0] if hasattr(e, "messages") else str(e)
            return JsonResponse({"ok": False, "error": msg}, status=400)


# ==============================================================================
# SNAPSHOT & RECONCILIATION API
# ==============================================================================

class BoardSnapshotApiView(BoardAccessMixin, View):
    """
    GET /boards/<board_id>/snapshot/
    Returns serialized state of the entire board (lists, cards, current member role, presence).
    Used by clients upon WebSocket reconnection to reconcile DOM without full reload.
    """
    def get(self, request, board_id):
        lists = (
            self.board.lists.prefetch_related(
                Prefetch(
                    "cards",
                    queryset=Card.objects.select_related("created_by", "assignee")
                    .annotate(comments_count=Count("comments"))
                    .order_by("position", "created_at")
                )
            ).order_by("position", "created_at")
        )

        lists_data = []
        for lst in lists:
            cards_data = []
            for card in lst.cards.all():
                assignee_data = {
                    "id": card.assignee.id,
                    "username": card.assignee.username,
                    "avatar_color": getattr(card.assignee, "avatar_color", "#64748B"),
                    "initials": getattr(card.assignee, "initials", card.assignee.username[:2].upper()),
                } if card.assignee else None

                cards_data.append({
                    "id": card.id,
                    "title": card.title,
                    "description": card.description,
                    "position": card.position,
                    "list_id": card.board_list_id,
                    "author": card.created_by.username,
                    "author_initials": getattr(card.created_by, "initials", card.created_by.username[:2].upper()),
                    "author_color": getattr(card.created_by, "avatar_color", "#4F46E5"),
                    "assignee": assignee_data,
                    "comments_count": getattr(card, "comments_count", card.comments.count()),
                })

            lists_data.append({
                "id": lst.id,
                "title": lst.title,
                "position": lst.position,
                "cards": cards_data,
            })

        active_presence = presence.get_board_presence(self.board.id)

        return JsonResponse({
            "ok": True,
            "board": {
                "id": self.board.id,
                "title": self.board.title,
                "current_role": self.membership.role,
                "can_edit": self.membership.role in [BoardMembership.Role.OWNER, BoardMembership.Role.EDITOR],
                "lists": lists_data,
                "presence": active_presence,
            }
        })


# ==============================================================================
# LIST & CARD MUTATION ENDPOINTS (Phases 2 & 3)
# ==============================================================================

class ListCreateApiView(BoardApiView):
    """POST /boards/<board_id>/lists/create/"""
    def post(self, request, board_id):
        payload = self.parse_payload(request)
        if payload is None:
            return JsonResponse({"ok": False, "error": "Invalid JSON payload."}, status=400)

        title = payload.get("title")
        client_id = self.get_client_id(request, payload)
        new_list = services.create_list(self.board, title, request.user, client_id=client_id)
        return JsonResponse({
            "ok": True,
            "list": {
                "id": new_list.id,
                "title": new_list.title,
                "position": new_list.position,
            }
        })


class ListRenameApiView(BoardApiView):
    """POST /boards/<board_id>/lists/<list_id>/rename/"""
    def post(self, request, board_id, list_id):
        board_list = get_object_or_404(BoardList, id=list_id, board=self.board)
        payload = self.parse_payload(request)
        if payload is None:
            return JsonResponse({"ok": False, "error": "Invalid JSON payload."}, status=400)

        title = payload.get("title")
        client_id = self.get_client_id(request, payload)
        renamed_list = services.rename_list(board_list, title, request.user, client_id=client_id)
        return JsonResponse({
            "ok": True,
            "list": {
                "id": renamed_list.id,
                "title": renamed_list.title,
                "position": renamed_list.position,
            }
        })


class ListDeleteApiView(BoardApiView):
    """POST /boards/<board_id>/lists/<list_id>/delete/"""
    def post(self, request, board_id, list_id):
        board_list = get_object_or_404(BoardList, id=list_id, board=self.board)
        payload = self.parse_payload(request)
        client_id = self.get_client_id(request, payload) if payload else None
        services.delete_list(board_list, request.user, client_id=client_id)
        return JsonResponse({"ok": True})


class ListMoveApiView(BoardApiView):
    """POST /boards/<board_id>/lists/<list_id>/move/"""
    def post(self, request, board_id, list_id):
        board_list = get_object_or_404(BoardList, id=list_id, board=self.board)
        payload = self.parse_payload(request)
        if payload is None:
            return JsonResponse({"ok": False, "error": "Invalid JSON payload."}, status=400)

        new_position = payload.get("new_position")
        if new_position is None:
            return JsonResponse({"ok": False, "error": "Missing 'new_position'."}, status=400)

        try:
            new_position_int = int(new_position)
        except (ValueError, TypeError):
            return JsonResponse({"ok": False, "error": "'new_position' must be an integer."}, status=400)

        client_id = self.get_client_id(request, payload)
        moved_list = services.move_list(board_list, new_position_int, request.user, client_id=client_id)
        return JsonResponse({
            "ok": True,
            "list": {
                "id": moved_list.id,
                "position": moved_list.position,
            }
        })


class CardCreateApiView(BoardApiView):
    """POST /boards/<board_id>/lists/<list_id>/cards/create/"""
    def post(self, request, board_id, list_id):
        board_list = get_object_or_404(BoardList, id=list_id, board=self.board)
        payload = self.parse_payload(request)
        if payload is None:
            return JsonResponse({"ok": False, "error": "Invalid JSON payload."}, status=400)

        title = payload.get("title")
        description = payload.get("description", "")
        client_id = self.get_client_id(request, payload)

        new_card = services.create_card(board_list, title, description, request.user, client_id=client_id)
        return JsonResponse({
            "ok": True,
            "card": {
                "id": new_card.id,
                "title": new_card.title,
                "description": new_card.description,
                "position": new_card.position,
                "list_id": new_card.board_list_id,
                "author": request.user.username,
                "author_initials": getattr(request.user, "initials", request.user.username[:2].upper()),
                "author_color": getattr(request.user, "avatar_color", "#4F46E5"),
                "assignee": None,
                "comments_count": 0,
            }
        })


class CardUpdateApiView(BoardApiView):
    """POST /boards/<board_id>/cards/<card_id>/update/"""
    def post(self, request, board_id, card_id):
        card = get_object_or_404(Card, id=card_id, board_list__board=self.board)
        payload = self.parse_payload(request)
        if payload is None:
            return JsonResponse({"ok": False, "error": "Invalid JSON payload."}, status=400)

        title = payload.get("title")
        description = payload.get("description")
        client_id = self.get_client_id(request, payload)
        updated_card = services.update_card(card, title, description, request.user, client_id=client_id)
        return JsonResponse({
            "ok": True,
            "card": {
                "id": updated_card.id,
                "title": updated_card.title,
                "description": updated_card.description,
                "position": updated_card.position,
                "list_id": updated_card.board_list_id,
            }
        })


class CardDeleteApiView(BoardApiView):
    """POST /boards/<board_id>/cards/<card_id>/delete/"""
    def post(self, request, board_id, card_id):
        card = get_object_or_404(Card, id=card_id, board_list__board=self.board)
        payload = self.parse_payload(request)
        client_id = self.get_client_id(request, payload) if payload else None
        services.delete_card(card, request.user, client_id=client_id)
        return JsonResponse({"ok": True})


class CardMoveApiView(BoardApiView):
    """POST /boards/<board_id>/cards/<card_id>/move/"""
    def post(self, request, board_id, card_id):
        card = get_object_or_404(Card, id=card_id, board_list__board=self.board)
        payload = self.parse_payload(request)
        if payload is None:
            return JsonResponse({"ok": False, "error": "Invalid JSON payload."}, status=400)

        target_list_id = payload.get("target_list_id")
        new_position = payload.get("new_position")

        if target_list_id is None or new_position is None:
            return JsonResponse(
                {"ok": False, "error": "Missing 'target_list_id' or 'new_position'."},
                status=400
            )

        try:
            new_position_int = int(new_position)
        except (ValueError, TypeError):
            return JsonResponse({"ok": False, "error": "'new_position' must be an integer."}, status=400)

        target_list = get_object_or_404(BoardList, id=target_list_id, board=self.board)
        client_id = self.get_client_id(request, payload)
        moved_card = services.move_card(card, target_list, new_position_int, request.user, client_id=client_id)
        return JsonResponse({
            "ok": True,
            "card": {
                "id": moved_card.id,
                "list_id": moved_card.board_list_id,
                "position": moved_card.position,
            }
        })


# ==============================================================================
# PHASE 4 ENDPOINTS: ACTIVITY, COMMENTS, ASSIGNMENT, NOTIFICATIONS
# ==============================================================================

class ActivityListApiView(BoardAccessMixin, View):
    """
    GET /boards/<board_id>/activity/?before_id=<id>&limit=50
    Cursor-based pagination of board activity log.
    """
    def get(self, request, board_id):
        qs = Activity.objects.filter(board=self.board).select_related("actor").order_by("-id")
        before_id = request.GET.get("before_id")
        if before_id:
            try:
                qs = qs.filter(id__lt=int(before_id))
            except ValueError:
                pass

        limit = 50
        try:
            limit = min(50, max(1, int(request.GET.get("limit", 50))))
        except ValueError:
            pass

        items = list(qs[: limit + 1])
        has_more = len(items) > limit
        if has_more:
            items = items[:limit]

        activities_data = [
            {
                "id": a.id,
                "verb": a.verb,
                "actor": {
                    "id": a.actor.id if a.actor else None,
                    "username": a.actor.username if a.actor else "System",
                    "avatar_color": getattr(a.actor, "avatar_color", "#64748B"),
                    "initials": getattr(a.actor, "initials", a.actor.username[:2].upper() if a.actor else "SY"),
                },
                "summary": a.summary,
                "created_at": a.created_at.isoformat(),
                "metadata": a.metadata,
            }
            for a in items
        ]

        return JsonResponse({
            "ok": True,
            "activities": activities_data,
            "has_more": has_more,
            "next_cursor": activities_data[-1]["id"] if activities_data else None,
        })


class CardAssignApiView(BoardApiView):
    """POST /boards/<board_id>/cards/<card_id>/assign/"""
    def post(self, request, board_id, card_id):
        card = get_object_or_404(Card, id=card_id, board_list__board=self.board)
        payload = self.parse_payload(request)
        if payload is None:
            return JsonResponse({"ok": False, "error": "Invalid JSON payload."}, status=400)

        assignee_id = payload.get("assignee_id")
        assignee = None
        if assignee_id:
            try:
                assignee = User.objects.get(id=int(assignee_id))
            except (User.DoesNotExist, ValueError):
                return JsonResponse({"ok": False, "error": "User not found."}, status=404)

        client_id = self.get_client_id(request, payload)
        updated_card = services.assign_card(card, request.user, assignee, client_id=client_id)

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

        return JsonResponse({
            "ok": True,
            "card_id": updated_card.id,
            "assignee": assignee_data,
        })


class CardCommentsApiView(BoardAccessMixin, View):
    """
    GET /boards/<board_id>/cards/<card_id>/comments/
    POST /boards/<board_id>/cards/<card_id>/comments/
    """
    def get(self, request, board_id, card_id):
        card = get_object_or_404(Card, id=card_id, board_list__board=self.board)
        comments = card.comments.select_related("author").order_by("created_at")
        comments_data = [
            {
                "id": c.id,
                "card_id": card.id,
                "author": {
                    "id": c.author.id,
                    "username": c.author.username,
                    "avatar_color": getattr(c.author, "avatar_color", "#64748B"),
                    "initials": getattr(c.author, "initials", c.author.username[:2].upper()),
                },
                "body": c.body,
                "created_at": c.created_at.isoformat(),
                "edited_at": c.edited_at.isoformat() if c.edited_at else None,
                "can_edit": (c.author == request.user),
                "can_delete": (
                    c.author == request.user or self.membership.role == BoardMembership.Role.OWNER
                ),
            }
            for c in comments
        ]
        return JsonResponse({"ok": True, "comments": comments_data})

    def post(self, request, board_id, card_id):
        card = get_object_or_404(Card, id=card_id, board_list__board=self.board)
        try:
            payload = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({"ok": False, "error": "Invalid JSON payload."}, status=400)

        body = payload.get("body", "")
        client_id = request.headers.get("X-Client-ID") or payload.get("client_id")

        try:
            comment = services.add_comment(card=card, author=request.user, body=body, client_id=client_id)
            return JsonResponse({
                "ok": True,
                "comment": {
                    "id": comment.id,
                    "card_id": card.id,
                    "author": {
                        "id": request.user.id,
                        "username": request.user.username,
                        "avatar_color": getattr(request.user, "avatar_color", "#64748B"),
                        "initials": getattr(request.user, "initials", request.user.username[:2].upper()),
                    },
                    "body": comment.body,
                    "created_at": comment.created_at.isoformat(),
                    "edited_at": None,
                    "can_edit": True,
                    "can_delete": True,
                },
                "comments_count": card.comments.count(),
            }, status=201)
        except PermissionDenied as e:
            return JsonResponse({"ok": False, "error": str(e)}, status=403)
        except (ValidationError, ValueError) as e:
            msg = e.messages[0] if hasattr(e, "messages") else str(e)
            return JsonResponse({"ok": False, "error": msg}, status=400)


class CommentDetailApiView(BoardAccessMixin, View):
    """
    PATCH /boards/<board_id>/comments/<comment_id>/
    DELETE /boards/<board_id>/comments/<comment_id>/
    """
    def patch(self, request, board_id, comment_id):
        comment = get_object_or_404(Comment, id=comment_id, card__board_list__board=self.board)
        try:
            payload = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({"ok": False, "error": "Invalid JSON payload."}, status=400)

        body = payload.get("body", "")
        client_id = request.headers.get("X-Client-ID") or payload.get("client_id")

        try:
            updated = services.edit_comment(comment=comment, user=request.user, body=body, client_id=client_id)
            return JsonResponse({
                "ok": True,
                "comment": {
                    "id": updated.id,
                    "card_id": updated.card_id,
                    "body": updated.body,
                    "edited_at": updated.edited_at.isoformat(),
                }
            })
        except PermissionDenied as e:
            return JsonResponse({"ok": False, "error": str(e)}, status=403)
        except (ValidationError, ValueError) as e:
            msg = e.messages[0] if hasattr(e, "messages") else str(e)
            return JsonResponse({"ok": False, "error": msg}, status=400)

    def delete(self, request, board_id, comment_id):
        comment = get_object_or_404(Comment, id=comment_id, card__board_list__board=self.board)
        client_id = request.headers.get("X-Client-ID")

        try:
            services.delete_comment(comment=comment, user=request.user, client_id=client_id)
            return JsonResponse({"ok": True})
        except PermissionDenied as e:
            return JsonResponse({"ok": False, "error": str(e)}, status=403)


class NotificationListApiView(LoginRequiredMixin, View):
    """GET /notifications/"""
    def get(self, request):
        notifications = (
            Notification.objects.filter(recipient=request.user)
            .select_related("actor", "board", "card")
            .order_by("-created_at")[:30]
        )
        unread_count = Notification.objects.filter(recipient=request.user, is_read=False).count()

        data = [
            {
                "id": n.id,
                "board_id": n.board.id,
                "board_title": n.board.title,
                "card_id": n.card.id if n.card else None,
                "actor": {
                    "id": n.actor.id,
                    "username": n.actor.username,
                    "avatar_color": getattr(n.actor, "avatar_color", "#64748B"),
                    "initials": getattr(n.actor, "initials", n.actor.username[:2].upper()),
                },
                "kind": n.kind,
                "message": n.message,
                "is_read": n.is_read,
                "created_at": n.created_at.isoformat(),
            }
            for n in notifications
        ]
        return JsonResponse({"ok": True, "notifications": data, "unread_count": unread_count})


class NotificationMarkReadApiView(LoginRequiredMixin, View):
    """POST /notifications/<notification_id>/mark-read/"""
    def post(self, request, notification_id):
        notification = get_object_or_404(Notification, id=notification_id, recipient=request.user)
        if not notification.is_read:
            notification.is_read = True
            notification.save(update_fields=["is_read"])

        unread_count = Notification.objects.filter(recipient=request.user, is_read=False).count()
        transaction.on_commit(
            lambda: events.send_user_event(
                user_id=request.user.id,
                event_type="notification.count_updated",
                payload={"unread_count": unread_count},
            )
        )
        return JsonResponse({"ok": True, "unread_count": unread_count})


class NotificationMarkAllReadApiView(LoginRequiredMixin, View):
    """POST /notifications/mark-all-read/"""
    def post(self, request):
        Notification.objects.filter(recipient=request.user, is_read=False).update(is_read=True)
        transaction.on_commit(
            lambda: events.send_user_event(
                user_id=request.user.id,
                event_type="notification.count_updated",
                payload={"unread_count": 0},
            )
        )
        return JsonResponse({"ok": True, "unread_count": 0})
