"""
Forms for Board workspace CRUD and Member management.
"""

from django import forms
from django.contrib.auth import get_user_model
from django.db.models import Q
from .models import Board, BoardMembership

User = get_user_model()


class BoardForm(forms.ModelForm):
    """Form for creating and editing a Board workspace."""

    class Meta:
        model = Board
        fields = ["title", "description"]
        widgets = {
            "title": forms.TextInput(attrs={
                "class": "form-control",
                "placeholder": "e.g., Q4 Product Launch, Sprint 23...",
                "autofocus": "autofocus",
            }),
            "description": forms.Textarea(attrs={
                "class": "form-control",
                "rows": 3,
                "placeholder": "Optional description or goals for this board...",
            }),
        }


class AddMemberForm(forms.Form):
    """
    Form for adding a new collaborator to a board via username or email address.
    """
    identifier = forms.CharField(
        label="Username or Email",
        max_length=150,
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "placeholder": "Enter exact username or email",
        }),
        help_text="Find an existing user registered in the system."
    )
    role = forms.ChoiceField(
        choices=[
            (BoardMembership.Role.EDITOR, "Editor (Can edit lists and tasks)"),
            (BoardMembership.Role.VIEWER, "Viewer (Read-only access)"),
        ],
        initial=BoardMembership.Role.VIEWER,
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    def __init__(self, *args, board: Board = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.board = board

    def clean_identifier(self):
        ident = self.cleaned_data.get("identifier", "").strip()
        if not ident:
            raise forms.ValidationError("Please provide a username or email address.")

        try:
            user = User.objects.get(Q(username__iexact=ident) | Q(email__iexact=ident))
        except User.DoesNotExist:
            raise forms.ValidationError(f"No user found matching '{ident}'.")
        except User.MultipleObjectsReturned:
            # If multiple users share the same email, match strictly by username
            user = User.objects.filter(username__iexact=ident).first()
            if not user:
                raise forms.ValidationError("Multiple users found with that email; please specify the username.")

        if self.board and BoardMembership.objects.filter(board=self.board, user=user).exists():
            raise forms.ValidationError(f"User '{user.username}' is already a member of this board.")

        self.target_user = user
        return ident


class UpdateMemberRoleForm(forms.Form):
    """
    Form for changing an existing member's role.
    Owner role cannot be reassigned or demoted via this form.
    """
    role = forms.ChoiceField(
        choices=[
            (BoardMembership.Role.EDITOR, "Editor"),
            (BoardMembership.Role.VIEWER, "Viewer"),
        ],
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )
