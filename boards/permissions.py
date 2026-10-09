"""
Centralized permission logic and access control for TaskFlow boards.

SECURITY REQUIREMENT:
A user who is not an authorized member of a board must receive an Http404 (Not Found)
rather than an Http403 (Forbidden). This prevents malicious users from enumerating
board IDs and discovering private organizational workspaces.
"""

from typing import Iterable, Optional, Tuple
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpRequest
from .models import Board, BoardMembership


def get_board_for_member(
    board_id: int,
    user,
    required_roles: Optional[Iterable[str]] = None,
) -> Tuple[Board, BoardMembership]:
    """
    Fetch a board and verify that the given user has membership access.

    Returns:
        Tuple of (Board, BoardMembership)

    Raises:
        Http404: If the board does not exist OR if the user is not a member.
        PermissionDenied (403): If the user is a member, but lacks the required role(s).
    """
    try:
        board = Board.objects.select_related("owner").get(pk=board_id)
    except Board.DoesNotExist:
        raise Http404("Board not found.")

    try:
        membership = BoardMembership.objects.select_related("user").get(
            board=board,
            user=user
        )
    except BoardMembership.DoesNotExist:
        # Crucial security pattern: Return 404 instead of 403 to prevent board enumeration
        raise Http404("Board not found.")

    if required_roles and membership.role not in required_roles:
        raise PermissionDenied("You do not have the required permissions for this action.")

    return board, membership


class BoardAccessMixin(LoginRequiredMixin):
    """
    Class-based view mixin ensuring the requesting user is a member of the board.

    Attributes:
        board_url_kwarg: URL pattern parameter name containing the board primary key.
        required_roles: Optional iterable of permitted roles (e.g. ['OWNER']).
    """
    board_url_kwarg: str = "board_id"
    required_roles: Optional[Iterable[str]] = None

    board: Board = None
    membership: BoardMembership = None

    def dispatch(self, request: HttpRequest, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()

        board_id = kwargs.get(self.board_url_kwarg)
        if not board_id:
            raise Http404("Board identifier missing.")

        self.board, self.membership = get_board_for_member(
            board_id=board_id,
            user=request.user,
            required_roles=self.required_roles,
        )
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["board"] = self.board
        context["current_membership"] = self.membership
        context["is_owner"] = self.membership.role == BoardMembership.Role.OWNER
        context["is_editor"] = self.membership.role in [
            BoardMembership.Role.OWNER,
            BoardMembership.Role.EDITOR,
        ]
        return context


class BoardOwnerRequiredMixin(BoardAccessMixin):
    """
    Specialized mixin restricting view access strictly to the board OWNER.
    Non-members receive 404; non-owner members receive 403 PermissionDenied.
    """
    required_roles = [BoardMembership.Role.OWNER]
