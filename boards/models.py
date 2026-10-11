"""
Database schema for TaskFlow collaborative task boards.

Contains four core models:
- Board: High-level workspace entity
- BoardMembership: Explicit relational join table for RBAC (Role-Based Access Control)
- BoardList: Column container for cards within a board
- Card: Discrete task item belonging to a list
"""

from django.conf import settings
from django.db import models


# ==============================================================================
# 1. BOARD MODEL
# ==============================================================================

class Board(models.Model):
    """
    Represents a collaborative task board workspace (analogous to a Trello board).

    INTERVIEW RATIONALE:
    The Board serves as the primary multi-tenant container for tasks, lists, and
    collaborators. Tracking both an explicit 'owner' foreign key and timestamp metadata
    enables straightforward auditability, permission inheritance, and recency sorting.
    """

    # Human-readable title of the board workspace (e.g. "Q4 Product Launch")
    title = models.CharField(
        max_length=255,
        help_text="Workspace title visible to all board members."
    )

    # Optional descriptive summary of the board's purpose or workflow guidelines
    description = models.TextField(
        blank=True,
        help_text="Optional context, purpose, or guidelines for this board."
    )

    # Top-level owner who created the board; retains ultimate authority (deletion, demotions)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="owned_boards",
        help_text="The primary administrator and creator of this board."
    )

    # Automatically recorded when the board record is first inserted
    created_at = models.DateTimeField(
        auto_now_add=True,
        help_text="Timestamp when the board was created."
    )

    # Automatically updated whenever any field on the board record is modified
    updated_at = models.DateTimeField(
        auto_now=True,
        help_text="Timestamp of the most recent modification to board attributes."
    )

    class Meta:
        # Sort boards so the most recently updated board appears first in dashboard lists
        ordering = ["-updated_at"]
        indexes = [
            # Index on owner foreign key speeds up queries filtering boards owned by a user
            models.Index(fields=["owner"]),
            # Index on created_at speeds up chronological sorting and analytics queries
            models.Index(fields=["created_at"]),
        ]
        verbose_name = "Board"
        verbose_name_plural = "Boards"

    def __str__(self) -> str:
        return self.title


# ==============================================================================
# 2. BOARD MEMBERSHIP MODEL (RBAC Join Table)
# ==============================================================================

class BoardMembership(models.Model):
    """
    Explicit join table modeling membership and Role-Based Access Control (RBAC).

    INTERVIEW RATIONALE:
    Instead of a simple ManyToManyField(User), an explicit through model ('BoardMembership')
    allows storing vital contextual metadata on the relationship: the user's role
    (OWNER, EDITOR, VIEWER) and the timestamp they joined. It also supports database-level
    uniqueness constraints to prevent duplicate memberships.
    """

    class Role(models.TextChoices):
        OWNER = "OWNER", "Owner"
        EDITOR = "EDITOR", "Editor"
        VIEWER = "VIEWER", "Viewer"

    # Foreign key referencing the parent board workspace
    board = models.ForeignKey(
        Board,
        on_delete=models.CASCADE,
        related_name="memberships",
        help_text="Board workspace this membership grants access to."
    )

    # Foreign key referencing the user granted membership
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="board_memberships",
        help_text="User granted membership and permissions on this board."
    )

    # RBAC role determining capabilities (OWNER=full admin, EDITOR=write/move, VIEWER=read-only)
    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        default=Role.VIEWER,
        help_text="Access control tier for this member on this board."
    )

    # Automatically recorded timestamp when the user was invited or joined
    joined_at = models.DateTimeField(
        auto_now_add=True,
        help_text="Timestamp when the member was added to the board."
    )

    class Meta:
        # Enforce that a user can only have one active membership record per board
        constraints = [
            models.UniqueConstraint(
                fields=["board", "user"],
                name="unique_board_user_membership"
            )
        ]
        indexes = [
            # Composite index on (board, user) accelerates membership lookup during access checks
            models.Index(fields=["board", "user"]),
            # Index on user foreign key speeds up "My Boards" dashboard queries
            models.Index(fields=["user"]),
            # Index on role allows rapid filtering of members by privilege level
            models.Index(fields=["role"]),
        ]
        ordering = ["joined_at"]
        verbose_name = "Board Membership"
        verbose_name_plural = "Board Memberships"

    def __str__(self) -> str:
        return f"{self.user.username} ({self.role}) on '{self.board.title}'"


# ==============================================================================
# 3. BOARD LIST MODEL (Column container)
# ==============================================================================

class BoardList(models.Model):
    """
    Represents an ordered workflow column within a board (e.g. "To Do", "In Progress").

    INTERVIEW RATIONALE:
    Named 'BoardList' instead of 'List' to prevent shadowing the Python built-in 'list' type.
    Columns use an explicit integer 'position' field to enable user-defined column ordering
    without relying on primary key sequence, making reordering in subsequent phases seamless.
    """

    # Board workspace that owns this workflow column
    board = models.ForeignKey(
        Board,
        on_delete=models.CASCADE,
        related_name="lists",
        help_text="Board workspace that owns this column."
    )

    # Column title (e.g., 'To Do', 'Backlog', 'Done')
    title = models.CharField(
        max_length=255,
        help_text="Header title of the column."
    )

    # 0-indexed position determining display sequence from left to right on the board
    position = models.PositiveIntegerField(
        default=0,
        help_text="Zero-based ordering sequence of the column within the board."
    )

    # Timestamp when this list was instantiated
    created_at = models.DateTimeField(
        auto_now_add=True,
        help_text="Timestamp when this column was created."
    )

    class Meta:
        # Default ordering displays lists strictly in increasing order of position, then created_at
        ordering = ["position", "created_at"]
        indexes = [
            # Composite index on (board, position) speeds up querying ordered lists for a board
            models.Index(fields=["board", "position"]),
        ]
        verbose_name = "Board List"
        verbose_name_plural = "Board Lists"

    def __str__(self) -> str:
        return f"{self.title} ({self.board.title})"


# ==============================================================================
# 4. CARD MODEL (Discrete task item)
# ==============================================================================

class Card(models.Model):
    """
    Represents an individual task card nested inside a specific BoardList column.

    INTERVIEW RATIONALE:
    Cards are the atomic work items in Kanban/Scrum boards. Storing 'position' enables
    vertical drag-and-drop ordering within a column. Tracking 'created_by' preserves
    authorship provenance even if the card is assigned or moved by other team members later.
    """

    # Workflow column list currently holding this task card
    board_list = models.ForeignKey(
        BoardList,
        on_delete=models.CASCADE,
        related_name="cards",
        help_text="The list column currently containing this card."
    )

    # Concise title summarizing the task
    title = models.CharField(
        max_length=255,
        help_text="Actionable title of the task card."
    )

    # Detailed task description, specifications, or acceptance criteria
    description = models.TextField(
        blank=True,
        help_text="Detailed body text, checklist, or instructions for the task."
    )

    # 0-indexed vertical position within the parent list column
    position = models.PositiveIntegerField(
        default=0,
        help_text="Zero-based vertical ordering sequence within its column."
    )

    # User who originally created the task card
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="created_cards",
        help_text="User who originally authored this task card."
    )

    # Optional member assigned to this task card
    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="assigned_cards",
        help_text="Board member assigned to this task card."
    )

    # Timestamp when the task was initially drafted
    created_at = models.DateTimeField(
        auto_now_add=True,
        help_text="Timestamp when the card was created."
    )

    # Timestamp when the task card attributes were last updated
    updated_at = models.DateTimeField(
        auto_now=True,
        help_text="Timestamp when the card was last edited."
    )

    class Meta:
        # Default vertical ordering within a column
        ordering = ["position", "created_at"]
        indexes = [
            # Composite index on (board_list, position) ensures fast retrieval of ordered cards
            models.Index(fields=["board_list", "position"]),
            # Index on created_by foreign key speeds up activity and author filtering
            models.Index(fields=["created_by"]),
            # Index on assignee foreign key speeds up assigned task queries
            models.Index(fields=["assignee"]),
        ]
        verbose_name = "Card"
        verbose_name_plural = "Cards"

    def __str__(self) -> str:
        return self.title


# ==============================================================================
# 5. ACTIVITY MODEL (Immutable board audit log)
# ==============================================================================

class Activity(models.Model):
    """
    Records an immutable audit event for actions occurring within a board workspace.

    ARCHITECTURAL RATIONALE:
    Summary text is stored as a frozen string at write time rather than constructed
    dynamically on read. This ensures historical integrity: if a card named 'Fix Auth'
    is logged and subsequently renamed to 'Refactor Login' or deleted entirely,
    the activity log retains the precise factual summary of what occurred at that moment.
    """

    class Verb(models.TextChoices):
        CARD_CREATED = "card.created", "Card Created"
        CARD_MOVED = "card.moved", "Card Moved"
        CARD_UPDATED = "card.updated", "Card Updated"
        CARD_DELETED = "card.deleted", "Card Deleted"
        CARD_ASSIGNED = "card.assigned", "Card Assigned"
        LIST_CREATED = "list.created", "List Created"
        LIST_RENAMED = "list.renamed", "List Renamed"
        LIST_DELETED = "list.deleted", "List Deleted"
        LIST_MOVED = "list.moved", "List Moved"
        COMMENT_ADDED = "comment.added", "Comment Added"
        COMMENT_EDITED = "comment.edited", "Comment Edited"
        COMMENT_DELETED = "comment.deleted", "Comment Deleted"
        MEMBER_ADDED = "member.added", "Member Added"
        MEMBER_REMOVED = "member.removed", "Member Removed"
        MEMBER_ROLE_CHANGED = "member.role_changed", "Member Role Changed"
        BOARD_UPDATED = "board.updated", "Board Updated"

    board = models.ForeignKey(
        Board,
        on_delete=models.CASCADE,
        related_name="activities",
        help_text="Board workspace where this activity occurred."
    )

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="activities",
        help_text="User who performed the action (null if system-generated)."
    )

    verb = models.CharField(
        max_length=64,
        choices=Verb.choices,
        help_text="Machine-readable action identifier."
    )

    target_type = models.CharField(
        max_length=50,
        help_text="Entity type acted upon (card, list, comment, member, board)."
    )

    target_id = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Primary key of target entity at time of action."
    )

    summary = models.CharField(
        max_length=500,
        help_text="Human-readable summary frozen at write time."
    )

    metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text="Supplemental structured context (e.g. source/target column names, diffs)."
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
        help_text="Timestamp when the event transpired."
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            # High-performance index for board-filtered chronological cursor pagination
            models.Index(fields=["board", "-created_at"]),
        ]
        verbose_name = "Activity"
        verbose_name_plural = "Activities"

    def __str__(self) -> str:
        return f"[{self.board.title}] {self.summary}"


# ==============================================================================
# 6. COMMENT MODEL (Card discussion thread)
# ==============================================================================

class Comment(models.Model):
    """
    Represents an author comment attached to a specific Card.
    """

    card = models.ForeignKey(
        Card,
        on_delete=models.CASCADE,
        related_name="comments",
        help_text="Task card this comment is attached to."
    )

    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="card_comments",
        help_text="User authoring this comment."
    )

    body = models.TextField(
        max_length=2000,
        help_text="Comment text (up to 2000 characters)."
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
        help_text="Timestamp when comment was submitted."
    )

    edited_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp when comment was last modified (null if never edited)."
    )

    class Meta:
        ordering = ["created_at"]
        indexes = [
            # Fast chronological retrieval of comments for a card
            models.Index(fields=["card", "created_at"]),
        ]
        verbose_name = "Comment"
        verbose_name_plural = "Comments"

    def __str__(self) -> str:
        return f"Comment by {self.author.username} on {self.card.title}"


# ==============================================================================
# 7. NOTIFICATION MODEL (In-app real-time alerts)
# ==============================================================================

class Notification(models.Model):
    """
    Represents an in-app alert dispatched to a specific recipient user.
    """

    class Kind(models.TextChoices):
        MENTION = "MENTION", "Mention"
        ASSIGNED = "ASSIGNED", "Assigned"

    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
        help_text="User receiving this notification alert."
    )

    board = models.ForeignKey(
        Board,
        on_delete=models.CASCADE,
        related_name="notifications",
        help_text="Board workspace related to this alert."
    )

    card = models.ForeignKey(
        Card,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="notifications",
        help_text="Card related to this alert (null if deleted or board-level)."
    )

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="authored_notifications",
        help_text="User whose action triggered this notification."
    )

    kind = models.CharField(
        max_length=20,
        choices=Kind.choices,
        help_text="Classification category of this notification."
    )

    message = models.CharField(
        max_length=255,
        help_text="Human-readable notification text."
    )

    is_read = models.BooleanField(
        default=False,
        help_text="Read/unread status flag."
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
        help_text="Timestamp when notification was created."
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            # Compound index for instant unread count badge queries and reverse-chronological list
            models.Index(fields=["recipient", "is_read", "-created_at"]),
        ]
        verbose_name = "Notification"
        verbose_name_plural = "Notifications"

    def __str__(self) -> str:
        return f"[{self.kind}] to {self.recipient.username}: {self.message}"

