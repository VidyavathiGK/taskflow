from django.urls import path
from .views import (
    BoardCreateView,
    BoardDeleteView,
    BoardDetailView,
    BoardMembersView,
    BoardUpdateView,
    CardCreateApiView,
    CardDeleteApiView,
    CardMoveApiView,
    CardUpdateApiView,
    ListCreateApiView,
    ListDeleteApiView,
    ListMoveApiView,
    ListRenameApiView,
    MyBoardsView,
)

app_name = "boards"

urlpatterns = [
    # HTML Views
    path("", MyBoardsView.as_view(), name="my_boards"),
    path("create/", BoardCreateView.as_view(), name="board_create"),
    path("<int:board_id>/", BoardDetailView.as_view(), name="board_detail"),
    path("<int:board_id>/edit/", BoardUpdateView.as_view(), name="board_edit"),
    path("<int:board_id>/delete/", BoardDeleteView.as_view(), name="board_delete"),
    path("<int:board_id>/members/", BoardMembersView.as_view(), name="manage_members"),

    # Phase 2 JSON API Endpoints (POST only)
    # List Endpoints
    path("<int:board_id>/lists/create/", ListCreateApiView.as_view(), name="api_list_create"),
    path("<int:board_id>/lists/<int:list_id>/rename/", ListRenameApiView.as_view(), name="api_list_rename"),
    path("<int:board_id>/lists/<int:list_id>/delete/", ListDeleteApiView.as_view(), name="api_list_delete"),
    path("<int:board_id>/lists/<int:list_id>/move/", ListMoveApiView.as_view(), name="api_list_move"),

    # Card Endpoints
    path("<int:board_id>/lists/<int:list_id>/cards/create/", CardCreateApiView.as_view(), name="api_card_create"),
    path("<int:board_id>/cards/<int:card_id>/update/", CardUpdateApiView.as_view(), name="api_card_update"),
    path("<int:board_id>/cards/<int:card_id>/delete/", CardDeleteApiView.as_view(), name="api_card_delete"),
    path("<int:board_id>/cards/<int:card_id>/move/", CardMoveApiView.as_view(), name="api_card_move"),
]
