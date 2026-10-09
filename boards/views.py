"""
Views for Board workspace CRUD and Member management.
"""

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from .forms import AddMemberForm, BoardForm, UpdateMemberRoleForm
from .models import Board, BoardMembership
from .permissions import BoardAccessMixin, BoardOwnerRequiredMixin


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
    Renders the board workspace detail view.
    Displays title, description, member roster, and read-only lists and cards.
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
    Supports:
    - Adding a member by username or email with an initial role (EDITOR or VIEWER)
    - Changing an existing member's role (OWNER cannot be demoted)
    - Removing a member (OWNER cannot be removed)
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
