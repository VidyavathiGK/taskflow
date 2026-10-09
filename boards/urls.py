from django.urls import path
from .views import (
    BoardCreateView,
    BoardDeleteView,
    BoardDetailView,
    BoardMembersView,
    BoardUpdateView,
    MyBoardsView,
)

app_name = "boards"

urlpatterns = [
    path("", MyBoardsView.as_view(), name="my_boards"),
    path("create/", BoardCreateView.as_view(), name="board_create"),
    path("<int:board_id>/", BoardDetailView.as_view(), name="board_detail"),
    path("<int:board_id>/edit/", BoardUpdateView.as_view(), name="board_edit"),
    path("<int:board_id>/delete/", BoardDeleteView.as_view(), name="board_delete"),
    path("<int:board_id>/members/", BoardMembersView.as_view(), name="manage_members"),
]
