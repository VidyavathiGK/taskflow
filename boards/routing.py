"""
WebSocket URL routing for boards app.
"""

from django.urls import path
from . import consumers

websocket_urlpatterns = [
    path("ws/boards/<int:board_id>/", consumers.BoardConsumer.as_asgi()),
]
