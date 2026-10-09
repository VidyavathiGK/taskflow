"""
Management command to seed realistic demo data for TaskFlow.

Creates:
- 3 users: alice (Owner), bob (Editor), charlie (Viewer)
- 1 board: 'Product Roadmap & Sprint 1'
- 3 lists: 'Backlog', 'In Progress', 'Completed'
- 8 task cards distributed across lists with descriptions and authors
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from boards.models import Board, BoardList, BoardMembership, Card

User = get_user_model()


class Command(BaseCommand):
    help = "Seeds demo users, a board workspace, lists, and task cards for instant local testing."

    def handle(self, *args, **options):
        self.stdout.write(self.style.NOTICE("Seeding TaskFlow demo environment..."))

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
                },
                {
                    "list": columns["Backlog"],
                    "title": "Design drag-and-drop animation transitions",
                    "description": "Card hover elevation and smooth displacement transitions between columns.",
                    "position": 1,
                    "created_by": users["bob"],
                },
                {
                    "list": columns["Backlog"],
                    "title": "Add email invitation notifications",
                    "description": "Send invite confirmation emails when users are added to board workspaces.",
                    "position": 2,
                    "created_by": users["charlie"],
                },
                # In Progress
                {
                    "list": columns["In Progress"],
                    "title": "Setup PostgreSQL schema & RBAC permissions",
                    "description": "Board, BoardMembership, BoardList, and Card models with optimized composite indexes.",
                    "position": 0,
                    "created_by": users["alice"],
                },
                {
                    "list": columns["In Progress"],
                    "title": "Build workspace member management modal",
                    "description": "Allow owner to invite by username/email, promote/demote editors, and remove members.",
                    "position": 1,
                    "created_by": users["bob"],
                },
                {
                    "list": columns["In Progress"],
                    "title": "Create responsive Kanban board layout",
                    "description": "Clean Vanilla CSS flex/grid column layout with dark navigation and vibrant badges.",
                    "position": 2,
                    "created_by": users["alice"],
                },
                # Completed
                {
                    "list": columns["Completed"],
                    "title": "Project repository initialization",
                    "description": "Django 5 project skeleton with python-decouple environment settings and .env.example.",
                    "position": 0,
                    "created_by": users["alice"],
                },
                {
                    "list": columns["Completed"],
                    "title": "Custom User model with avatar_color",
                    "description": "Extends AbstractUser from initial migration for future-proof WebSocket presence.",
                    "position": 1,
                    "created_by": users["bob"],
                },
            ]

            # Clear existing cards on this board if re-seeding to keep exact ~8 cards
            Card.objects.filter(board_list__board=board).delete()

            for item in cards_data:
                Card.objects.create(
                    board_list=item["list"],
                    title=item["title"],
                    description=item["description"],
                    position=item["position"],
                    created_by=item["created_by"],
                )

        self.stdout.write(self.style.SUCCESS(
            "\nSuccessfully seeded TaskFlow demo data!\n"
            "--------------------------------------------------\n"
            "Demo Credentials (all share password: 'password123'):\n"
            "  1. alice   (Role: OWNER)  - full administrative control\n"
            "  2. bob     (Role: EDITOR) - member with edit privileges\n"
            "  3. charlie (Role: VIEWER) - member with read-only access\n"
            "--------------------------------------------------"
        ))
