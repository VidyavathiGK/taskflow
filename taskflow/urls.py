"""
URL configuration for taskflow project.
"""

from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView

from boards.views import (
    NotificationListApiView,
    NotificationMarkAllReadApiView,
    NotificationMarkReadApiView,
)

urlpatterns = [
    path('admin/', admin.site.urls),
    path('accounts/', include('accounts.urls')),
    path('boards/', include('boards.urls')),

    # Phase 4 In-App Notifications
    path('notifications/', NotificationListApiView.as_view(), name='notification_list'),
    path('notifications/<int:notification_id>/mark-read/', NotificationMarkReadApiView.as_view(), name='notification_mark_read'),
    path('notifications/mark-all-read/', NotificationMarkAllReadApiView.as_view(), name='notification_mark_all_read'),

    # Root redirects straight to My Boards (which redirects to login if unauthenticated)
    path('', RedirectView.as_view(pattern_name='boards:my_boards', permanent=False), name='root'),
]
