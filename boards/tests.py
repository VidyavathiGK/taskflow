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


# ==============================================================================
# PHASE 2 TESTS: SERVICE LAYER & JSON API ENDPOINTS
# ==============================================================================

from django.core.exceptions import PermissionDenied
from boards import services


class BoardsPhase2ServiceTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="alice", password="password123")
        self.editor = User.objects.create_user(username="bob", password="password123")
        self.viewer = User.objects.create_user(username="charlie", password="password123")
        self.outsider = User.objects.create_user(username="stranger", password="password123")

        self.board = Board.objects.create(title="Dev Board", owner=self.owner)
        BoardMembership.objects.create(board=self.board, user=self.owner, role=BoardMembership.Role.OWNER)
        BoardMembership.objects.create(board=self.board, user=self.editor, role=BoardMembership.Role.EDITOR)
        BoardMembership.objects.create(board=self.board, user=self.viewer, role=BoardMembership.Role.VIEWER)

        self.other_board = Board.objects.create(title="Other Board", owner=self.owner)
        BoardMembership.objects.create(board=self.other_board, user=self.owner, role=BoardMembership.Role.OWNER)
        self.other_list = BoardList.objects.create(board=self.other_board, title="Other Col", position=0)

        # Create 3 initial lists on self.board
        self.col0 = BoardList.objects.create(board=self.board, title="Todo", position=0)
        self.col1 = BoardList.objects.create(board=self.board, title="Doing", position=1)
        self.col2 = BoardList.objects.create(board=self.board, title="Done", position=2)

        # Create cards in col0
        self.card0 = Card.objects.create(board_list=self.col0, title="C0", position=0, created_by=self.owner)
        self.card1 = Card.objects.create(board_list=self.col0, title="C1", position=1, created_by=self.owner)
        self.card2 = Card.objects.create(board_list=self.col0, title="C2", position=2, created_by=self.owner)

    def test_create_list_appends_contiguously(self):
        new_col = services.create_list(self.board, "QA", self.editor)
        self.assertEqual(new_col.position, 3)
        self.assertEqual(new_col.title, "QA")

    def test_rename_list(self):
        updated = services.rename_list(self.col0, "Backlog", self.editor)
        self.assertEqual(updated.title, "Backlog")
        self.col0.refresh_from_db()
        self.assertEqual(self.col0.title, "Backlog")

    def test_delete_list_closes_gap(self):
        # Deleting col1 (pos 1) should leave col0 at 0 and move col2 to 1
        services.delete_list(self.col1, self.editor)
        self.assertFalse(BoardList.objects.filter(id=self.col1.id).exists())
        self.col0.refresh_from_db()
        self.col2.refresh_from_db()
        self.assertEqual(self.col0.position, 0)
        self.assertEqual(self.col2.position, 1)

    def test_move_list_renumbers_contiguously(self):
        # Move col0 (pos 0) to pos 2 -> order should be [col1, col2, col0]
        services.move_list(self.col0, 2, self.editor)
        self.col0.refresh_from_db()
        self.col1.refresh_from_db()
        self.col2.refresh_from_db()
        self.assertEqual(self.col1.position, 0)
        self.assertEqual(self.col2.position, 1)
        self.assertEqual(self.col0.position, 2)

    def test_create_card_appends_contiguously(self):
        new_card = services.create_card(self.col0, "C3", "desc", self.editor)
        self.assertEqual(new_card.position, 3)
        self.assertEqual(new_card.created_by, self.editor)

    def test_update_card(self):
        updated = services.update_card(self.card0, "Updated Title", "Updated Desc", self.editor)
        self.assertEqual(updated.title, "Updated Title")
        self.assertEqual(updated.description, "Updated Desc")
        self.card0.refresh_from_db()
        self.assertEqual(self.card0.title, "Updated Title")

    def test_delete_card_closes_gap(self):
        # Deleting card1 (pos 1) should leave card0 at 0 and move card2 to 1
        services.delete_card(self.card1, self.editor)
        self.assertFalse(Card.objects.filter(id=self.card1.id).exists())
        self.card0.refresh_from_db()
        self.card2.refresh_from_db()
        self.assertEqual(self.card0.position, 0)
        self.assertEqual(self.card2.position, 1)

    def test_move_card_within_same_list(self):
        # Move card0 (pos 0) to pos 2 -> order should be [card1, card2, card0]
        services.move_card(self.card0, self.col0, 2, self.editor)
        self.card0.refresh_from_db()
        self.card1.refresh_from_db()
        self.card2.refresh_from_db()
        self.assertEqual(self.card1.position, 0)
        self.assertEqual(self.card2.position, 1)
        self.assertEqual(self.card0.position, 2)

    def test_move_card_across_lists(self):
        # Move card1 from col0 to col1 at pos 0
        services.move_card(self.card1, self.col1, 0, self.editor)
        self.card1.refresh_from_db()
        self.card0.refresh_from_db()
        self.card2.refresh_from_db()

        # Check source list (col0) gap closed
        self.assertEqual(self.card0.position, 0)
        self.assertEqual(self.card2.position, 1)

        # Check target list (col1)
        self.assertEqual(self.card1.board_list, self.col1)
        self.assertEqual(self.card1.position, 0)

    def test_service_permission_denied_for_viewer(self):
        with self.assertRaises(PermissionDenied):
            services.create_list(self.board, "Unauthorized", self.viewer)
        with self.assertRaises(PermissionDenied):
            services.create_card(self.col0, "Unauthorized", "desc", self.viewer)
        with self.assertRaises(PermissionDenied):
            services.move_card(self.card0, self.col1, 0, self.viewer)

    def test_move_card_rejects_cross_board(self):
        # Moving card0 to other_list (belongs to other_board) must raise ValueError
        with self.assertRaises(ValueError):
            services.move_card(self.card0, self.other_list, 0, self.owner)


class BoardsPhase2ApiTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="alice", password="password123")
        self.editor = User.objects.create_user(username="bob", password="password123")
        self.viewer = User.objects.create_user(username="charlie", password="password123")
        self.outsider = User.objects.create_user(username="stranger", password="password123")

        self.board = Board.objects.create(title="Dev Board", owner=self.owner)
        BoardMembership.objects.create(board=self.board, user=self.owner, role=BoardMembership.Role.OWNER)
        BoardMembership.objects.create(board=self.board, user=self.editor, role=BoardMembership.Role.EDITOR)
        BoardMembership.objects.create(board=self.board, user=self.viewer, role=BoardMembership.Role.VIEWER)

        self.other_board = Board.objects.create(title="Foreign Board", owner=self.owner)
        BoardMembership.objects.create(board=self.other_board, user=self.owner, role=BoardMembership.Role.OWNER)
        self.foreign_list = BoardList.objects.create(board=self.other_board, title="Foreign List", position=0)

        self.col = BoardList.objects.create(board=self.board, title="Backlog", position=0)
        self.card = Card.objects.create(board_list=self.col, title="Task 1", position=0, created_by=self.owner)

    def test_api_list_create_by_editor(self):
        self.client.login(username="bob", password="password123")
        url = reverse("boards:api_list_create", kwargs={"board_id": self.board.id})
        res = self.client.post(url, {"title": "In Review"}, content_type="application/json")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["list"]["title"], "In Review")
        self.assertEqual(data["list"]["position"], 1)

    def test_api_list_rename_by_editor(self):
        self.client.login(username="bob", password="password123")
        url = reverse("boards:api_list_rename", kwargs={"board_id": self.board.id, "list_id": self.col.id})
        res = self.client.post(url, {"title": "Sprint Backlog"}, content_type="application/json")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["ok"])
        self.col.refresh_from_db()
        self.assertEqual(self.col.title, "Sprint Backlog")

    def test_api_list_delete_by_editor(self):
        self.client.login(username="bob", password="password123")
        url = reverse("boards:api_list_delete", kwargs={"board_id": self.board.id, "list_id": self.col.id})
        res = self.client.post(url, {}, content_type="application/json")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["ok"])
        self.assertFalse(BoardList.objects.filter(id=self.col.id).exists())

    def test_api_list_move_by_editor(self):
        col2 = BoardList.objects.create(board=self.board, title="Col2", position=1)
        self.client.login(username="bob", password="password123")
        url = reverse("boards:api_list_move", kwargs={"board_id": self.board.id, "list_id": self.col.id})
        res = self.client.post(url, {"new_position": 1}, content_type="application/json")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["ok"])
        self.col.refresh_from_db()
        self.assertEqual(self.col.position, 1)

    def test_api_card_create_by_editor(self):
        self.client.login(username="bob", password="password123")
        url = reverse("boards:api_card_create", kwargs={"board_id": self.board.id, "list_id": self.col.id})
        res = self.client.post(url, {"title": "New Task", "description": "details"}, content_type="application/json")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["card"]["title"], "New Task")
        self.assertEqual(data["card"]["author"], "bob")

    def test_api_card_update_by_editor(self):
        self.client.login(username="bob", password="password123")
        url = reverse("boards:api_card_update", kwargs={"board_id": self.board.id, "card_id": self.card.id})
        res = self.client.post(url, {"title": "Updated Task", "description": "updated"}, content_type="application/json")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["ok"])
        self.card.refresh_from_db()
        self.assertEqual(self.card.title, "Updated Task")

    def test_api_card_delete_by_editor(self):
        self.client.login(username="bob", password="password123")
        url = reverse("boards:api_card_delete", kwargs={"board_id": self.board.id, "card_id": self.card.id})
        res = self.client.post(url, {}, content_type="application/json")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["ok"])
        self.assertFalse(Card.objects.filter(id=self.card.id).exists())

    def test_api_card_move_by_editor(self):
        col2 = BoardList.objects.create(board=self.board, title="Col2", position=1)
        self.client.login(username="bob", password="password123")
        url = reverse("boards:api_card_move", kwargs={"board_id": self.board.id, "card_id": self.card.id})
        res = self.client.post(url, {"target_list_id": col2.id, "new_position": 0}, content_type="application/json")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["ok"])
        self.card.refresh_from_db()
        self.assertEqual(self.card.board_list, col2)
        self.assertEqual(self.card.position, 0)

    def test_api_mutations_rejected_for_viewer_403(self):
        """Viewer role must receive 403 Forbidden on all mutation endpoints."""
        self.client.login(username="charlie", password="password123")
        create_url = reverse("boards:api_list_create", kwargs={"board_id": self.board.id})
        res = self.client.post(create_url, {"title": "Hacked List"}, content_type="application/json")
        self.assertEqual(res.status_code, 403)
        self.assertFalse(res.json()["ok"])

        card_url = reverse("boards:api_card_create", kwargs={"board_id": self.board.id, "list_id": self.col.id})
        card_res = self.client.post(card_url, {"title": "Hacked Card"}, content_type="application/json")
        self.assertEqual(card_res.status_code, 403)

    def test_api_mutations_rejected_for_non_member_404(self):
        """Anti-enumeration security invariant: Non-members must receive 404, not 403."""
        self.client.login(username="stranger", password="password123")
        create_url = reverse("boards:api_list_create", kwargs={"board_id": self.board.id})
        res = self.client.post(create_url, {"title": "Probe"}, content_type="application/json")
        self.assertEqual(res.status_code, 404)

    def test_api_cross_board_card_move_rejected_404(self):
        """Moving a card into a target list from another board returns 404."""
        self.client.login(username="alice", password="password123")
        url = reverse("boards:api_card_move", kwargs={"board_id": self.board.id, "card_id": self.card.id})
        res = self.client.post(url, {
            "target_list_id": self.foreign_list.id,
            "new_position": 0,
        }, content_type="application/json")
        self.assertEqual(res.status_code, 404)
