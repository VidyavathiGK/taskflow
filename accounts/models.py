import random
from django.contrib.auth.models import AbstractUser
from django.db import models


# Palette of vibrant, distinctive colors for default avatar assignments
DEFAULT_AVATAR_PALETTE = [
    "#4F46E5",  # Indigo
    "#059669",  # Emerald
    "#D97706",  # Amber
    "#DC2626",  # Red
    "#7C3AED",  # Violet
    "#2563EB",  # Blue
    "#DB2777",  # Pink
    "#0D9488",  # Teal
]


def get_random_avatar_color() -> str:
    """Return a randomly chosen hex color from the curated avatar palette."""
    return random.choice(DEFAULT_AVATAR_PALETTE)


class User(AbstractUser):
    """
    Custom User model extending Django's AbstractUser.

    INTERVIEW RATIONALE:
    Django's default auth user model is hard to change mid-project once migrations
    have run and foreign keys exist across tables. Defining a custom User model
    from migration 0001 is best practice because it provides future-proof flexibility
    to add fields (such as avatar_color, profile data, or OAuth IDs) without schema surgery.
    """

    # Hex color code used for UI user badge and live presence indicators in collaborative phases
    avatar_color = models.CharField(
        max_length=7,
        blank=True,
        default=get_random_avatar_color,
        help_text="Hex color code (e.g. #4F46E5) used for avatar badges and WebSocket presence.",
    )

    class Meta:
        verbose_name = "User"
        verbose_name_plural = "Users"
        ordering = ["username"]

    def __str__(self) -> str:
        return self.username

    @property
    def initials(self) -> str:
        """Return the uppercase first letter of the username for avatar badges."""
        return self.username[0].upper() if self.username else "?"
