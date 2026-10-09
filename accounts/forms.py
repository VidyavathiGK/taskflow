from django import forms
from django.contrib.auth.forms import UserCreationForm
from .models import User, get_random_avatar_color


class SignUpForm(UserCreationForm):
    """
    Form for registering a new user with username, optional email, and avatar color.
    """
    email = forms.EmailField(
        required=False,
        help_text="Optional. Used for board member invitations and notifications.",
        widget=forms.EmailInput(attrs={"placeholder": "you@example.com", "class": "form-control"})
    )
    avatar_color = forms.CharField(
        max_length=7,
        required=False,
        initial=get_random_avatar_color,
        widget=forms.TextInput(attrs={"type": "color", "class": "form-control-color", "title": "Choose your avatar color"}),
        help_text="Choose a presence color for your user avatar badge."
    )

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "email", "avatar_color")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].widget.attrs.update({
            "placeholder": "Choose a username",
            "class": "form-control"
        })
        if not self.initial.get("avatar_color"):
            self.initial["avatar_color"] = get_random_avatar_color()

    def clean_avatar_color(self):
        color = self.cleaned_data.get("avatar_color")
        if not color:
            return get_random_avatar_color()
        if not color.startswith("#") or len(color) != 7:
            raise forms.ValidationError("Please provide a valid 7-character hex color (e.g. #4F46E5).")
        return color
