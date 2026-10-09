from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from boards.models import Board, BoardList, BoardMembership, Card

User = get_user_model()


class BoardsPhase1Tests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="alice", password="password123")
        self.editor = User.objects.create_user(username="bob", password="password123")
        self.viewer = User.objects.create_user(username="charlie", password="password123")
        self.outsider = User.objects.create_user(username="stranger", password="password123")

        # Create sample board with memberships
        self.board = Board.objects.create(
            title="Sprint Board",
            description="Testing board",
            owner=self.owner,
        )
        self.owner_membership = BoardMembership.objects.create(
            board=self.board, user=self.owner, role=BoardMembership.Role.OWNER
        )
        self.editor_membership = BoardMembership.objects.create(
            board=self.board, user=self.editor, role=BoardMembership.Role.EDITOR
        )
        self.viewer_membership = BoardMembership.objects.create(
            board=self.board, user=self.viewer, role=BoardMembership.Role.VIEWER
        )

        # Create column and card
        self.col = BoardList.objects.create(board=self.board, title="To Do", position=0)
        self.card = Card.objects.create(
            board_list=self.col,
            title="Write Tests",
            description="Phase 1 verification",
            position=0,
            created_by=self.owner,
        )

    def test_my_boards_lists_user_memberships(self):
        self.client.login(username="bob", password="password123")
        response = self.client.get(reverse("boards:my_boards"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sprint Board")
        self.assertContains(response, "EDITOR")

    def test_create_board_automatically_creates_owner_membership(self):
        self.client.login(username="bob", password="password123")
        response = self.client.post(reverse("boards:board_create"), {
            "title": "Bob's Project",
            "description": "Created by Bob",
        })
        new_board = Board.objects.get(title="Bob's Project")
        self.assertRedirects(response, reverse("boards:board_detail", kwargs={"board_id": new_board.id}))
        self.assertEqual(new_board.owner, self.editor)
        membership = BoardMembership.objects.get(board=new_board, user=self.editor)
        self.assertEqual(membership.role, BoardMembership.Role.OWNER)

    def test_board_detail_accessible_by_member(self):
        self.client.login(username="charlie", password="password123")
        response = self.client.get(reverse("boards:board_detail", kwargs={"board_id": self.board.id}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sprint Board")
        self.assertContains(response, "To Do")
        self.assertContains(response, "Write Tests")

    def test_non_member_gets_404_not_403(self):
        """Crucial security test: non-members must receive 404, not 403."""
        self.client.login(username="stranger", password="password123")
        detail_response = self.client.get(reverse("boards:board_detail", kwargs={"board_id": self.board.id}))
        self.assertEqual(detail_response.status_code, 404)

        edit_response = self.client.get(reverse("boards:board_edit", kwargs={"board_id": self.board.id}))
        self.assertEqual(edit_response.status_code, 404)

        members_response = self.client.get(reverse("boards:manage_members", kwargs={"board_id": self.board.id}))
        self.assertEqual(members_response.status_code, 404)

    def test_only_owner_can_edit_or_delete_board(self):
        # Non-owner member (Editor) gets 403 PermissionDenied
        self.client.login(username="bob", password="password123")
        response = self.client.get(reverse("boards:board_edit", kwargs={"board_id": self.board.id}))
        self.assertEqual(response.status_code, 403)

        del_response = self.client.post(reverse("boards:board_delete", kwargs={"board_id": self.board.id}))
        self.assertEqual(del_response.status_code, 403)

        # Owner can edit successfully
        self.client.login(username="alice", password="password123")
        edit_post = self.client.post(reverse("boards:board_edit", kwargs={"board_id": self.board.id}), {
            "title": "Renamed Board",
            "description": "Updated description",
        })
        self.assertRedirects(edit_post, reverse("boards:board_detail", kwargs={"board_id": self.board.id}))
        self.board.refresh_from_db()
        self.assertEqual(self.board.title, "Renamed Board")

    def test_manage_members_add_change_role_and_remove(self):
        self.client.login(username="alice", password="password123")
        members_url = reverse("boards:manage_members", kwargs={"board_id": self.board.id})

        # Add stranger by username
        response = self.client.post(members_url, {
            "action": "add_member",
            "identifier": "stranger",
            "role": BoardMembership.Role.VIEWER,
        })
        self.assertRedirects(response, members_url)
        self.assertTrue(BoardMembership.objects.filter(board=self.board, user=self.outsider, role="VIEWER").exists())

        # Change role from VIEWER to EDITOR
        outsider_membership = BoardMembership.objects.get(board=self.board, user=self.outsider)
        change_resp = self.client.post(members_url, {
            "action": "change_role",
            "membership_id": outsider_membership.id,
            "role": BoardMembership.Role.EDITOR,
        })
        self.assertRedirects(change_resp, members_url)
        outsider_membership.refresh_from_db()
        self.assertEqual(outsider_membership.role, BoardMembership.Role.EDITOR)

        # Remove member
        remove_resp = self.client.post(members_url, {
            "action": "remove_member",
            "membership_id": outsider_membership.id,
        })
        self.assertRedirects(remove_resp, members_url)
        self.assertFalse(BoardMembership.objects.filter(board=self.board, user=self.outsider).exists())

    def test_owner_cannot_be_removed_or_demoted(self):
        self.client.login(username="alice", password="password123")
        members_url = reverse("boards:manage_members", kwargs={"board_id": self.board.id})

        # Attempt to change owner's role
        self.client.post(members_url, {
            "action": "change_role",
            "membership_id": self.owner_membership.id,
            "role": BoardMembership.Role.VIEWER,
        })
        self.owner_membership.refresh_from_db()
        self.assertEqual(self.owner_membership.role, BoardMembership.Role.OWNER)

        # Attempt to remove owner
        self.client.post(members_url, {
            "action": "remove_member",
            "membership_id": self.owner_membership.id,
        })
        self.assertTrue(BoardMembership.objects.filter(id=self.owner_membership.id).exists())
