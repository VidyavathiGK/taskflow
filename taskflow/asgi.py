"""
ASGI config for taskflow project.

Exposes the ASGI callable as a module-level variable named ``application``.
Multiplexes standard HTTP traffic through Django and WebSocket connections
through Django Channels with AuthMiddlewareStack and AllowedHostsOriginValidator.
"""

import os
from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'taskflow.settings')

# Initialize Django ASGI application early to ensure the AppRegistry
# is populated before importing code that may import ORM models.
django_asgi_app = get_asgi_application()

from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.security.websocket import AllowedHostsOriginValidator
import boards.routing

application = ProtocolTypeRouter({
    "http": django_asgi_app,
    "websocket": AllowedHostsOriginValidator(
        AuthMiddlewareStack(
            URLRouter(boards.routing.websocket_urlpatterns)
        )
    ),
})
