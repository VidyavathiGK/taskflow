/**
 * TaskFlow In-App Notifications Client (Phase 4)
 *
 * Connects to ws/notifications/ to receive real-time alerts.
 * Manages the navbar bell badge, dropdown notifications list, and mark-read actions.
 */

(function () {
  'use strict';

  // Only run if notification widget is present in DOM (authenticated pages)
  const bellBtn = document.getElementById('notification-bell-btn');
  const badgeEl = document.getElementById('notification-badge');
  const dropdownEl = document.getElementById('notification-dropdown');
  const listEl = document.getElementById('notification-list');
  const markAllBtn = document.getElementById('mark-all-read-btn');

  if (!bellBtn || !dropdownEl) return;

  function getCsrfToken() {
    const match = document.cookie.match(/csrftoken=([^;]+)/);
    if (match) return match[1];
    const input = document.querySelector('input[name="csrfmiddlewaretoken"]');
    return input ? input.value : '';
  }

  function formatTimeAgo(isoString) {
    if (!isoString) return '';
    const date = new Date(isoString);
    const now = new Date();
    const diffSec = Math.floor((now - date) / 1000);
    if (diffSec < 45) return 'just now';
    if (diffSec < 90) return '1m ago';
    const diffMin = Math.floor(diffSec / 60);
    if (diffMin < 60) return `${diffMin}m ago`;
    const diffHours = Math.floor(diffMin / 60);
    if (diffHours < 24) return `${diffHours}h ago`;
    const diffDays = Math.floor(diffHours / 24);
    return `${diffDays}d ago`;
  }

  function updateBadge(count) {
    if (!badgeEl) return;
    if (count > 0) {
      badgeEl.textContent = count > 99 ? '99+' : count;
      badgeEl.classList.remove('hidden');
    } else {
      badgeEl.textContent = '0';
      badgeEl.classList.add('hidden');
    }
  }

  function renderNotificationItem(n) {
    const item = document.createElement('div');
    item.className = `notification-item ${n.is_read ? 'read' : 'unread'}`;
    item.dataset.id = n.id;
    item.dataset.boardId = n.board_id;
    if (n.card_id) item.dataset.cardId = n.card_id;

    // Avatar bubble
    const avatar = document.createElement('span');
    avatar.className = 'avatar-bubble avatar-bubble-xs';
    avatar.style.backgroundColor = (n.actor && n.actor.avatar_color) || '#64748B';
    avatar.textContent = (n.actor && n.actor.initials) || (n.actor && n.actor.username ? n.actor.username.slice(0, 2).toUpperCase() : 'TF');

    // Content container
    const content = document.createElement('div');
    content.className = 'notification-content';

    const msg = document.createElement('p');
    msg.className = 'notification-msg';
    msg.textContent = n.message; // Safe DOM textContent (XSS prevention)

    const meta = document.createElement('span');
    meta.className = 'notification-time';
    meta.textContent = formatTimeAgo(n.created_at);

    content.appendChild(msg);
    content.appendChild(meta);

    item.appendChild(avatar);
    item.appendChild(content);

    // Click handler to open board / card and mark read
    item.addEventListener('click', async (e) => {
      e.stopPropagation();
      if (!n.is_read) {
        try {
          await fetch(`/notifications/${n.id}/mark-read/`, {
            method: 'POST',
            headers: {
              'X-CSRFToken': getCsrfToken(),
              'Content-Type': 'application/json',
            },
          });
          item.classList.remove('unread');
          item.classList.add('read');
          const curCount = parseInt(badgeEl.textContent, 10) || 0;
          updateBadge(Math.max(0, curCount - 1));
        } catch (err) {
          console.warn('Failed to mark notification read:', err);
        }
      }

      // Navigate to board workspace
      const targetUrl = n.card_id
        ? `/boards/${n.board_id}/#card-${n.card_id}`
        : `/boards/${n.board_id}/`;
      window.location.href = targetUrl;
    });

    return item;
  }

  async function loadNotifications() {
    try {
      const res = await fetch('/notifications/');
      const data = await res.json();
      if (data.ok) {
        updateBadge(data.unread_count || 0);
        listEl.innerHTML = '';
        if (data.notifications && data.notifications.length > 0) {
          data.notifications.forEach((n) => {
            listEl.appendChild(renderNotificationItem(n));
          });
        } else {
          listEl.innerHTML = '<div class="notification-empty">No notifications yet.</div>';
        }
      }
    } catch (err) {
      console.warn('Failed to fetch notifications:', err);
    }
  }

  // Toggle Dropdown
  bellBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    dropdownEl.classList.toggle('hidden');
  });

  document.addEventListener('click', (e) => {
    if (!dropdownEl.contains(e.target) && e.target !== bellBtn) {
      dropdownEl.classList.add('hidden');
    }
  });

  // Mark all read
  if (markAllBtn) {
    markAllBtn.addEventListener('click', async (e) => {
      e.stopPropagation();
      try {
        const res = await fetch('/notifications/mark-all-read/', {
          method: 'POST',
          headers: {
            'X-CSRFToken': getCsrfToken(),
            'Content-Type': 'application/json',
          },
        });
        const data = await res.json();
        if (data.ok) {
          updateBadge(0);
          const unreadItems = listEl.querySelectorAll('.notification-item.unread');
          unreadItems.forEach((el) => {
            el.classList.remove('unread');
            el.classList.add('read');
          });
        }
      } catch (err) {
        console.warn('Failed to mark all notifications read:', err);
      }
    });
  }

  // =========================================================================
  // WEBSOCKET CLIENT (ws/notifications/)
  // =========================================================================
  let socket = null;
  let reconnectAttempts = 0;
  let pingInterval = null;

  function initWebSocket() {
    const protocol = window.location.protocol === 'https:' ? 'wss://' : 'ws://';
    const wsUrl = `${protocol}${window.location.host}/ws/notifications/`;

    try {
      socket = new WebSocket(wsUrl);
    } catch (e) {
      console.warn('Could not initialize notifications WebSocket:', e);
      return;
    }

    socket.onopen = () => {
      reconnectAttempts = 0;
      if (pingInterval) clearInterval(pingInterval);
      pingInterval = setInterval(() => {
        if (socket && socket.readyState === WebSocket.OPEN) {
          socket.send(JSON.stringify({ type: 'ping' }));
        }
      }, 20000);
    };

    socket.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.event === 'notification.created') {
          const payload = data.payload || data;
          const notif = payload.notification;
          const unreadCount = payload.unread_count;
          if (typeof unreadCount === 'number') {
            updateBadge(unreadCount);
          } else {
            const cur = parseInt(badgeEl.textContent, 10) || 0;
            updateBadge(cur + 1);
          }

          if (notif && listEl) {
            const emptyNotice = listEl.querySelector('.notification-empty');
            if (emptyNotice) emptyNotice.remove();
            listEl.prepend(renderNotificationItem(notif));
          }
        } else if (data.event === 'notification.count_updated') {
          const payload = data.payload || data;
          if (typeof payload.unread_count === 'number') {
            updateBadge(payload.unread_count);
          }
        }
      } catch (err) {
        console.warn('Failed to parse notification WebSocket message:', err);
      }
    };

    socket.onclose = (event) => {
      if (pingInterval) clearInterval(pingInterval);
      if (event.code !== 4401 && event.code !== 1000) {
        const delay = Math.min(1000 * Math.pow(2, reconnectAttempts), 30000);
        reconnectAttempts++;
        setTimeout(initWebSocket, delay);
      }
    };

    socket.onerror = () => {
      // Handled in onclose
    };
  }

  // Initial load
  loadNotifications();
  initWebSocket();
})();
