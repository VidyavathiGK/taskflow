"""
URL configuration for taskflow project.
"""

from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView

urlpatterns = [
    path('admin/', admin.site.urls),
    path('accounts/', include('accounts.urls')),
    path('boards/', include('boards.urls')),
    # Root redirects straight to My Boards (which redirects to login if unauthenticated)
    path('', RedirectView.as_view(pattern_name='boards:my_boards', permanent=False), name='root'),
]
