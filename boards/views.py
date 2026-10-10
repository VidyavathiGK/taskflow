"""
Views for Board workspace CRUD, Member management, and Phase 2 JSON API endpoints.
"""

import json
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from .forms import AddMemberForm, BoardForm, UpdateMemberRoleForm
from .models import Board, BoardList, BoardMembership, Card
from .permissions import BoardAccessMixin, BoardOwnerRequiredMixin
from . import services


# ==============================================================================
# HTML TEMPLATE VIEWS (Phase 1)
# ==============================================================================

class MyBoardsView(LoginRequiredMixin, View):
    """
    Displays the dashboard of all boards where the authenticated user holds a membership.
    Shows the board title, description snippet, owner, and current user's membership role.
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
    In an atomic transaction, the board is created and the creator is automatically
    registered as the OWNER in the BoardMembership table.
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

                # Automatically establish creator as board OWNER
                BoardMembership.objects.create(
                    board=board,
                    user=request.user,
                    role=BoardMembership.Role.OWNER,
                )

            messages.success(request, f"Board '{board.title}' created successfully!")
            return redirect("boards:board_detail", board_id=board.id)

        return render(request, self.template_name, {"form": form})


class BoardDetailView(BoardAccessMixin, View):
    """
    Renders the interactive board workspace detail view.
    Displays title, description, member roster, and Kanban lists and cards.
    """
    template_name = "boards/board_detail.html"

    def get(self, request, board_id):
        # Fetch columns and nested cards with prefetch to avoid N+1 queries
        lists = (
            self.board.lists.prefetch_related(
                "cards",
                "cards__created_by"
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
    """
    Allows the board OWNER to modify the board title and description.
    Non-members receive 404; non-owner members receive 403.
    """
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
    """
    Allows the board OWNER to delete the board and all associated resources.
    """
    template_name = "boards/board_confirm_delete.html"

    def get(self, request, board_id):
        return render(request, self.template_name, {"board": self.board})

    def post(self, request, board_id):
        title = self.board.title
        self.board.delete()
        messages.success(request, f"Board '{title}' has been deleted.")
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

            # Safety check: Owner cannot be demoted
            if membership.user == self.board.owner or membership.role == BoardMembership.Role.OWNER:
                messages.error(request, "The board OWNER cannot be demoted or have their role changed.")
            elif new_role in [BoardMembership.Role.EDITOR, BoardMembership.Role.VIEWER]:
                membership.role = new_role
                membership.save()
                messages.success(
                    request,
                    f"Updated role for @{membership.user.username} to {new_role}."
                )
            else:
                messages.error(request, "Invalid role specified.")
            return redirect("boards:manage_members", board_id=self.board.id)

        # Action 3: Remove an existing member
        elif action == "remove_member":
            membership_id = request.POST.get("membership_id")
            membership = get_object_or_404(
                BoardMembership,
                id=membership_id,
                board=self.board
            )

            # Safety check: Owner cannot be removed
            if membership.user == self.board.owner or membership.role == BoardMembership.Role.OWNER:
                messages.error(request, "The board OWNER cannot be removed from the board.")
            else:
                username = membership.user.username
                membership.delete()
                messages.success(request, f"Removed @{username} from the board.")
            return redirect("boards:manage_members", board_id=self.board.id)

        messages.error(request, "Unknown action requested.")
        return redirect("boards:manage_members", board_id=self.board.id)


# ==============================================================================
# PHASE 2: JSON API BASE & MUTATION ENDPOINTS
# ==============================================================================

class BoardApiView(BoardAccessMixin, View):
    """
    Base view for Phase 2 JSON API endpoints.
    Reuses Phase 1 BoardAccessMixin:
    - Non-members automatically get 404 Http404 (preventing workspace enumeration).
    - Unauthenticated requests are rejected.
    - Captures domain and permission exceptions, converting them into standard JSON responses.
    """

    def parse_payload(self, request):
        """Parse request body as JSON if Content-Type is application/json, else fallback to POST."""
        content_type = request.content_type or ""
        if "application/json" in content_type:
            try:
                body_str = request.body.decode("utf-8") if request.body else "{}"
                return json.loads(body_str or "{}")
            except (json.JSONDecodeError, UnicodeDecodeError):
                return None
        return request.POST

    def dispatch(self, request, *args, **kwargs):
        if request.method != "POST":
            return JsonResponse({"ok": False, "error": "Method not allowed. Use POST."}, status=405)

        try:
            return super().dispatch(request, *args, **kwargs)
        except PermissionDenied as e:
            return JsonResponse({"ok": False, "error": str(e)}, status=403)
        except ValueError as e:
            return JsonResponse({"ok": False, "error": str(e)}, status=400)


class ListCreateApiView(BoardApiView):
    """POST /boards/<board_id>/lists/create/"""
    def post(self, request, board_id):
        payload = self.parse_payload(request)
        if payload is None:
            return JsonResponse({"ok": False, "error": "Invalid JSON payload."}, status=400)

        title = payload.get("title")
        new_list = services.create_list(self.board, title, request.user)
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
        updated_list = services.rename_list(board_list, title, request.user)
        return JsonResponse({
            "ok": True,
            "list": {
                "id": updated_list.id,
                "title": updated_list.title,
                "position": updated_list.position,
            }
        })


class ListDeleteApiView(BoardApiView):
    """POST /boards/<board_id>/lists/<list_id>/delete/"""
    def post(self, request, board_id, list_id):
        board_list = get_object_or_404(BoardList, id=list_id, board=self.board)
        services.delete_list(board_list, request.user)
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
            return JsonResponse({"ok": False, "error": "Missing 'new_position' field."}, status=400)

        try:
            new_position_int = int(new_position)
        except (ValueError, TypeError):
            return JsonResponse({"ok": False, "error": "'new_position' must be an integer."}, status=400)

        moved_list = services.move_list(board_list, new_position_int, request.user)
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
        card = services.create_card(board_list, title, description, request.user)
        return JsonResponse({
            "ok": True,
            "card": {
                "id": card.id,
                "title": card.title,
                "description": card.description,
                "position": card.position,
                "list_id": card.board_list_id,
                "author": card.created_by.username,
                "author_initials": card.created_by.initials,
                "author_color": getattr(card.created_by, "avatar_color", "#4F46E5"),
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
        updated_card = services.update_card(card, title, description, request.user)
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
        services.delete_card(card, request.user)
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

        # Enforce target list belongs to the same board workspace (returns 404 otherwise)
        target_list = get_object_or_404(BoardList, id=target_list_id, board=self.board)

        moved_card = services.move_card(card, target_list, new_position_int, request.user)
        return JsonResponse({
            "ok": True,
            "card": {
                "id": moved_card.id,
                "list_id": moved_card.board_list_id,
                "position": moved_card.position,
            }
        })
