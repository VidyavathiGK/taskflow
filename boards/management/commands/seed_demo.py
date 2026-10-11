"""
Management command to seed realistic demo data for TaskFlow.

Creates:
- 3 users: alice (Owner), bob (Editor), charlie (Viewer)
- 1 board: 'Product Roadmap & Sprint 1'
- 3 lists: 'Backlog', 'In Progress', 'Completed'
- 8 task cards with descriptions, authors, and card assignments
- Card discussion comments with @mentions
- In-app notification alerts
- Immutable activity log entries
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from boards.models import Activity, Board, BoardList, BoardMembership, Card, Comment, Notification

User = get_user_model()


class Command(BaseCommand):
    help = "Seeds demo users, a board workspace, lists, cards, assignments, comments, and activities for testing."

    def handle(self, *args, **options):
        self.stdout.write(self.style.NOTICE("Seeding TaskFlow demo environment (Phase 4)..."))

        with transaction.atomic():
            # 1. Create or retrieve demo users
            demo_users_data = [
                {
                    "username": "alice",
                    "email": "alice@taskflow.dev",
                    "avatar_color": "#4F46E5",  # Indigo
                    "first_name": "Alice",
                    "last_name": "Vance",
                },
                {
                    "username": "bob",
                    "email": "bob@taskflow.dev",
                    "avatar_color": "#10B981",  # Emerald
                    "first_name": "Bob",
                    "last_name": "Stone",
                },
                {
                    "username": "charlie",
                    "email": "charlie@taskflow.dev",
                    "avatar_color": "#F59E0B",  # Amber
                    "first_name": "Charlie",
                    "last_name": "Day",
                },
            ]

            users = {}
            for user_info in demo_users_data:
                user, created = User.objects.get_or_create(
                    username=user_info["username"],
                    defaults={
                        "email": user_info["email"],
                        "avatar_color": user_info["avatar_color"],
                        "first_name": user_info["first_name"],
                        "last_name": user_info["last_name"],
                    },
                )
                user.set_password("password123")
                user.avatar_color = user_info["avatar_color"]
                user.email = user_info["email"]
                user.save()
                users[user_info["username"]] = user
                status_str = "Created" if created else "Updated"
                self.stdout.write(f"  - {status_str} demo user: {user.username} (password: password123)")

            # 2. Create demo board
            board, board_created = Board.objects.get_or_create(
                title="Product Roadmap & Sprint 1",
                defaults={
                    "description": "High-priority development milestones for the TaskFlow real-time collaboration engine.",
                    "owner": users["alice"],
                },
            )
            if not board_created:
                board.owner = users["alice"]
                board.description = "High-priority development milestones for the TaskFlow real-time collaboration engine."
                board.save()
            self.stdout.write(f"  - Board: '{board.title}' (Owner: alice)")

            # 3. Create memberships
            memberships = [
                (users["alice"], BoardMembership.Role.OWNER),
                (users["bob"], BoardMembership.Role.EDITOR),
                (users["charlie"], BoardMembership.Role.VIEWER),
            ]
            for user, role in memberships:
                membership, _ = BoardMembership.objects.update_or_create(
                    board=board,
                    user=user,
                    defaults={"role": role},
                )
                self.stdout.write(f"    * Member: @{user.username} -> {role}")

            # 4. Create BoardLists (columns)
            columns_data = [
                ("Backlog", 0),
                ("In Progress", 1),
                ("Completed", 2),
            ]
            columns = {}
            for title, position in columns_data:
                col, _ = BoardList.objects.get_or_create(
                    board=board,
                    title=title,
                    defaults={"position": position},
                )
                col.position = position
                col.save()
                columns[title] = col

            # 5. Create Cards
            cards_data = [
                # Backlog
                {
                    "list": columns["Backlog"],
                    "title": "Implement WebSocket presence indicators",
                    "description": "Show live avatar badges with avatar_color when collaborators are active on the board.",
                    "position": 0,
                    "created_by": users["alice"],
                    "assignee": users["bob"],
                },
                {
                    "list": columns["Backlog"],
                    "title": "Design drag-and-drop animation transitions",
                    "description": "Card hover elevation and smooth displacement transitions between columns.",
                    "position": 1,
                    "created_by": users["bob"],
                    "assignee": users["alice"],
                },
                {
                    "list": columns["Backlog"],
                    "title": "Add email invitation notifications",
                    "description": "Send invite confirmation emails when users are added to board workspaces.",
                    "position": 2,
                    "created_by": users["charlie"],
                    "assignee": None,
                },
                # In Progress
                {
                    "list": columns["In Progress"],
                    "title": "Setup PostgreSQL schema & RBAC permissions",
                    "description": "Board, BoardMembership, BoardList, and Card models with optimized composite indexes.",
                    "position": 0,
                    "created_by": users["alice"],
                    "assignee": users["alice"],
                },
                {
                    "list": columns["In Progress"],
                    "title": "Build workspace member management modal",
                    "description": "Allow owner to invite by username/email, promote/demote editors, and remove members.",
                    "position": 1,
                    "created_by": users["bob"],
                    "assignee": users["bob"],
                },
                {
                    "list": columns["In Progress"],
                    "title": "Create responsive Kanban board layout",
                    "description": "Clean Vanilla CSS flex/grid column layout with dark navigation and vibrant badges.",
                    "position": 2,
                    "created_by": users["alice"],
                    "assignee": None,
                },
                # Completed
                {
                    "list": columns["Completed"],
                    "title": "Project repository initialization",
                    "description": "Django 5 project skeleton with python-decouple environment settings and .env.example.",
                    "position": 0,
                    "created_by": users["alice"],
                    "assignee": users["alice"],
                },
                {
                    "list": columns["Completed"],
                    "title": "Custom User model with avatar_color",
                    "description": "Extends AbstractUser from initial migration for future-proof WebSocket presence.",
                    "position": 1,
                    "created_by": users["bob"],
                    "assignee": users["bob"],
                },
            ]

            # Clear existing cards and activities on this board to seed cleanly
            Card.objects.filter(board_list__board=board).delete()
            Activity.objects.filter(board=board).delete()
            Notification.objects.filter(board=board).delete()

            created_cards = []
            for item in cards_data:
                c = Card.objects.create(
                    board_list=item["list"],
                    title=item["title"],
                    description=item["description"],
                    position=item["position"],
                    created_by=item["created_by"],
                    assignee=item["assignee"],
                )
                created_cards.append(c)

                # Record creation activity
                Activity.objects.create(
                    board=board,
                    actor=item["created_by"],
                    verb="card.created",
                    target_type="card",
                    target_id=c.id,
                    summary=f"created card '{c.title}' in '{item['list'].title}'",
                )

                if item["assignee"]:
                    Activity.objects.create(
                        board=board,
                        actor=item["created_by"],
                        verb="card.assigned",
                        target_type="card",
                        target_id=c.id,
                        summary=f"assigned '{c.title}' to {item['assignee'].username}",
                    )

            # 6. Create sample comments with @mentions and notifications
            first_card = created_cards[0]
            comment1 = Comment.objects.create(
                card=first_card,
                author=users["bob"],
                body="@alice please review the WebSocket presence architecture when ready!",
            )
            Activity.objects.create(
                board=board,
                actor=users["bob"],
                verb="comment.added",
                target_type="comment",
                target_id=comment1.id,
                summary=f"commented on '{first_card.title}'",
            )
            Notification.objects.create(
                recipient=users["alice"],
                board=board,
                card=first_card,
                actor=users["bob"],
                kind=Notification.Kind.MENTION,
                message=f"bob mentioned you in a comment on '{first_card.title}'",
                is_read=False,
            )

            comment2 = Comment.objects.create(
                card=first_card,
                author=users["alice"],
                body="Looks solid! Redis sorted sets are configured and pruning works cleanly.",
            )
            Activity.objects.create(
                board=board,
                actor=users["alice"],
                verb="comment.added",
                target_type="comment",
                target_id=comment2.id,
                summary=f"commented on '{first_card.title}'",
            )

            # Notification for card assignment to Bob
            Notification.objects.create(
                recipient=users["bob"],
                board=board,
                card=first_card,
                actor=users["alice"],
                kind=Notification.Kind.ASSIGNED,
                message=f"alice assigned you to '{first_card.title}'",
                is_read=False,
            )

            fourth_card = created_cards[3]
            comment3 = Comment.objects.create(
                card=fourth_card,
                author=users["alice"],
                body="@bob composite indexes are deployed on PostgreSQL.",
            )
            Activity.objects.create(
                board=board,
                actor=users["alice"],
                verb="comment.added",
                target_type="comment",
                target_id=comment3.id,
                summary=f"commented on '{fourth_card.title}'",
            )
            Notification.objects.create(
                recipient=users["bob"],
                board=board,
                card=fourth_card,
                actor=users["alice"],
                kind=Notification.Kind.MENTION,
                message=f"alice mentioned you in a comment on '{fourth_card.title}'",
                is_read=False,
            )

        self.stdout.write(self.style.SUCCESS(
            "\nSuccessfully seeded TaskFlow Phase 4 demo data!\n"
            "--------------------------------------------------\n"
            "Demo Credentials (all share password: 'password123'):\n"
            "  1. alice   (Role: OWNER)  - full administrative control\n"
            "  2. bob     (Role: EDITOR) - member with edit privileges\n"
            "  3. charlie (Role: VIEWER) - member with read-only access\n"
            "Features Seeded: Cards, Assignees, Comments with @mentions, Notifications & Activities.\n"
            "--------------------------------------------------"
        ))
