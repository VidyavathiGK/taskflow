"""
WebSocket consumers for real-time collaborative board synchronization.

Provides BoardConsumer:
- Rejects unauthenticated connections (code 4401)
- Rejects non-members (code 4404 to preserve anti-enumeration security)
- Subscribes client to group "board_<id>" and personal channel "user_<id>"
- Dispatches server-pushed events to connected browsers as JSON
- Rejects client mutation attempts (one validated write path via HTTP)
"""

import logging
from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from .models import BoardMembership

logger = logging.getLogger("taskflow.realtime")


class BoardConsumer(AsyncJsonWebsocketConsumer):
    """
    Async consumer handling WebSocket connections for a single board workspace.
    """

    board_id: int
    board_group_name: str
    user_group_name: str
    is_joined: bool = False

    @database_sync_to_async
    def is_board_member(self, user, board_id: int) -> bool:
        """Check if user has an active membership record on the board."""
        return BoardMembership.objects.filter(board_id=board_id, user=user).exists()

    async def connect(self):
        self.user = self.scope.get("user")
        self.board_id = self.scope["url_route"]["kwargs"].get("board_id")

        # 1. Reject anonymous / unauthenticated connections
        if not self.user or not self.user.is_authenticated:
            logger.warning(
                f"WebSocket connection rejected: Unauthenticated access attempt on board={self.board_id}"
            )
            await self.close(code=4401)
            return

        # 2. Reject users who are not active members of this board (anti-enumeration 4404)
        has_access = await self.is_board_member(self.user, self.board_id)
        if not has_access:
            logger.warning(
                f"WebSocket connection rejected: User '{self.user.username}' is not a member of board={self.board_id}"
            )
            await self.close(code=4404)
            return

        self.board_group_name = f"board_{self.board_id}"
        self.user_group_name = f"user_{self.user.id}"

        # 3. Join board broadcast group
        await self.channel_layer.group_add(
            self.board_group_name,
            self.channel_name
        )

        # 4. Join user-specific targeted group (for role changes / kick notifications)
        await self.channel_layer.group_add(
            self.user_group_name,
            self.channel_name
        )

        self.is_joined = True
        await self.accept()

        logger.info(
            f"WebSocket connected: user='{self.user.username}' board={self.board_id} channel={self.channel_name}"
        )

    async def disconnect(self, close_code):
        if self.is_joined:
            await self.channel_layer.group_discard(
                self.board_group_name,
                self.channel_name
            )
            await self.channel_layer.group_discard(
                self.user_group_name,
                self.channel_name
            )
            logger.info(
                f"WebSocket disconnected: user='{getattr(self.user, 'username', 'anonymous')}' board={getattr(self, 'board_id', None)} code={close_code}"
            )

    async def receive_json(self, content, **kwargs):
        """
        Handle incoming messages from the browser.
        DESIGN DECISION:
        All mutations flow exclusively through HTTP endpoints.
        WebSockets ignore or reject mutation payloads, allowing only ping/pong keepalives.
        """
        msg_type = content.get("type")

        if msg_type == "ping":
            await self.send_json({"type": "pong"})
            return

        # Reject any attempted client mutation frames
        await self.send_json({
            "type": "error",
            "message": "Mutations over WebSocket are rejected. Use HTTP POST endpoints.",
        })

    # ==========================================================================
    # GROUP EVENT HANDLERS (Dispatched by boards.events.broadcast)
    # ==========================================================================

    async def board_event(self, event):
        """Forward board broadcast event to client as JSON."""
        await self.send_json(event["payload"])

    async def user_event(self, event):
        """Forward targeted user event (role change, member removal) to client."""
        payload = event["payload"]
        await self.send_json(payload)

        # If user was removed or board deleted, close connection
        if payload.get("event") in ["member.removed", "board.deleted"]:
            await self.close(code=4403)
