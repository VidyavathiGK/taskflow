/**
 * TaskFlow — Interactive Kanban Board & Real-Time Sync Client Script (Phase 2 & 3)
 *
 * Responsibilities:
 * - Initializes SortableJS on columns (horizontal) and cards (vertical + cross-column)
 * - Handles inline list and card creation via fetch()
 * - Handles inline list title renaming and deletion confirmation
 * - Handles card detail viewing, updating, and deletion modal
 * - Enforces optimistic updates with automated rollback on server rejection
 * - Manages WebSocket connection to ws/boards/<board_id>/ with auto-reconnect & exponential backoff
 * - Implements echo cancellation using ephemeral tab client_id
 * - Dispatches incoming real-time events to the DOM without full page reload
 * - Reconciles board state via snapshot API on reconnection
 * - Enforces role-based dynamic permissions (OWNER/EDITOR vs VIEWER)
 */

document.addEventListener('DOMContentLoaded', () => {
  const boardApp = document.getElementById('board-app');
  if (!boardApp) return;

  const boardId = boardApp.dataset.boardId;
  const currentUserId = parseInt(boardApp.dataset.userId, 10);
  let canEdit = boardApp.dataset.canEdit === 'true';

  // Ephemeral client ID generated per tab to prevent echoing own optimistic updates
  const tabClientId = 'tab_' + Math.random().toString(36).substring(2, 10) + '_' + Date.now();

  // Helper: Retrieve CSRF token from page
  function getCsrfToken() {
    const input = document.querySelector('input[name="csrfmiddlewaretoken"]');
    if (input) return input.value;
    const cookie = document.cookie
      .split('; ')
      .find((row) => row.startsWith('csrftoken='));
    return cookie ? cookie.split('=')[1] : '';
  }

  // Helper: Show floating toast notification
  function showToast(message, type = 'info') {
    const container = document.getElementById('toast-container');
    if (!container) return;

    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.innerHTML = `
      <span class="toast-icon">${type === 'error' ? '⚠️' : type === 'success' ? '✓' : 'ℹ️'}</span>
      <span class="toast-message">${escapeHtml(message)}</span>
    `;

    container.appendChild(toast);

    setTimeout(() => {
      toast.classList.add('toast-fade-out');
      setTimeout(() => toast.remove(), 300);
    }, 4000);
  }

  // Helper: Escape HTML strings to prevent XSS
  function escapeHtml(str) {
    if (!str) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  // Helper: Standardized AJAX JSON POST request with X-Client-ID header
  async function apiPost(url, data) {
    const res = await fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': getCsrfToken(),
        'X-Client-ID': tabClientId,
      },
      body: JSON.stringify(data),
    });

    const json = await res.json().catch(() => null);
    if (!res.ok || !json || !json.ok) {
      const errMsg = (json && json.error) || `Request failed (${res.status} ${res.statusText})`;
      const err = new Error(errMsg);
      err.status = res.status;
      throw err;
    }
    return json;
  }

  // Helper: Update column count badges and #position labels
  function updateColumnMetadata(columnEl) {
    if (!columnEl) return;
    const listId = columnEl.dataset.listId;
    const cards = columnEl.querySelectorAll('.task-card');
    const countBadge = document.getElementById(`list-count-${listId}`);
    if (countBadge) {
      countBadge.textContent = cards.length;
    }
    cards.forEach((card, idx) => {
      card.dataset.position = idx;
      const posBadge = card.querySelector('.card-position-badge');
      if (posBadge) posBadge.textContent = `#${idx}`;
    });
  }

  // ===========================================================================
  // 1. DRAG AND DROP (SortableJS) & COLLISION AVOIDANCE
  // ===========================================================================

  const cardSortables = [];
  let columnSortable = null;
  let isLocallyDragging = false;
  const queuedEvents = [];

  function initCardsSortable(containerEl) {
    if (!containerEl || typeof Sortable === 'undefined') return;

    const sortable = new Sortable(containerEl, {
      group: 'cards',
      animation: 150,
      disabled: !canEdit,
      ghostClass: 'sortable-ghost',
      dragClass: 'sortable-drag',
      chosenClass: 'sortable-chosen',
      fallbackTolerance: 3,

      onStart: (evt) => {
        isLocallyDragging = true;
        evt.item._originalParent = evt.from;
        evt.item._originalIndex = evt.oldIndex;
      },

      onEnd: async (evt) => {
        isLocallyDragging = false;
        flushQueuedEvents();

        const cardEl = evt.item;
        const fromListId = evt.from.dataset.listId;
        const toListId = evt.to.dataset.listId;
        const cardId = cardEl.dataset.cardId;
        const newIndex = evt.newIndex;
        const oldIndex = evt.item._originalIndex;

        // No position change
        if (fromListId === toListId && oldIndex === newIndex) {
          return;
        }

        // Optimistically update counts and position badges
        updateColumnMetadata(evt.from);
        if (fromListId !== toListId) {
          updateColumnMetadata(evt.to);
        }

        try {
          await apiPost(`/boards/${boardId}/cards/${cardId}/move/`, {
            target_list_id: parseInt(toListId, 10),
            new_position: newIndex,
          });

          cardEl.dataset.listId = toListId;
          cardEl.dataset.position = newIndex;
        } catch (err) {
          // Rollback DOM elements to original state
          if (evt.item._originalParent) {
            const originalChildren = Array.from(evt.item._originalParent.children).filter(
              (c) => c !== cardEl
            );
            if (oldIndex >= originalChildren.length) {
              evt.item._originalParent.appendChild(cardEl);
            } else {
              evt.item._originalParent.insertBefore(cardEl, originalChildren[oldIndex]);
            }
          }

          updateColumnMetadata(evt.from);
          if (fromListId !== toListId) {
            updateColumnMetadata(evt.to);
          }

          showToast(`Could not move card: ${err.message}`, 'error');
        }
      },
    });

    cardSortables.push(sortable);
  }

  function initColumnsSortable() {
    const track = document.getElementById('kanban-track');
    if (!track || typeof Sortable === 'undefined') return;

    columnSortable = new Sortable(track, {
      animation: 150,
      disabled: !canEdit,
      handle: '.list-drag-handle',
      draggable: '.kanban-column',
      filter: '#add-list-column',
      ghostClass: 'sortable-column-ghost',

      onStart: (evt) => {
        isLocallyDragging = true;
        evt.item._originalIndex = evt.oldIndex;
      },

      onEnd: async (evt) => {
        isLocallyDragging = false;
        flushQueuedEvents();

        const listEl = evt.item;
        const listId = listEl.dataset.listId;
        const newIndex = evt.newIndex;
        const oldIndex = evt.item._originalIndex;

        if (oldIndex === newIndex) return;

        try {
          await apiPost(`/boards/${boardId}/lists/${listId}/move/`, {
            new_position: newIndex,
          });
          listEl.dataset.position = newIndex;
        } catch (err) {
          // Revert column position in track
          const columns = Array.from(track.querySelectorAll('.kanban-column')).filter(
            (c) => c !== listEl
          );
          if (oldIndex >= columns.length) {
            track.insertBefore(listEl, document.getElementById('add-list-column'));
          } else {
            track.insertBefore(listEl, columns[oldIndex]);
          }

          showToast(`Could not move column: ${err.message}`, 'error');
        }
      },
    });
  }

  function flushQueuedEvents() {
    while (queuedEvents.length > 0) {
      const eventData = queuedEvents.shift();
      applyRemoteEvent(eventData);
    }
  }

  // Initialize existing card containers
  document.querySelectorAll('.column-cards-container').forEach(initCardsSortable);
  initColumnsSortable();

  // ===========================================================================
  // 2. REAL-TIME WEBSOCKET ENGINE (Phase 3)
  // ===========================================================================

  const wsStatusIndicator = document.getElementById('ws-status-indicator');
  let socket = null;
  let pingInterval = null;
  let reconnectAttempts = 0;
  let hasEverConnected = false;

  function updateWsStatus(state, label) {
    if (!wsStatusIndicator) return;
    wsStatusIndicator.className = `ws-status-badge ws-status-${state}`;
    const textEl = wsStatusIndicator.querySelector('.ws-status-text');
    if (textEl) textEl.textContent = label;
  }

  function initWebSocket() {
    const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${wsProtocol}//${window.location.host}/ws/boards/${boardId}/`;

    updateWsStatus('connecting', 'Connecting...');

    try {
      socket = new WebSocket(wsUrl);
    } catch (e) {
      scheduleReconnect();
      return;
    }

    socket.onopen = async () => {
      updateWsStatus('connected', 'Live Sync Active');
      const wasReconnecting = hasEverConnected && reconnectAttempts > 0;
      hasEverConnected = true;
      reconnectAttempts = 0;

      // Start periodic 25s ping keepalive
      if (pingInterval) clearInterval(pingInterval);
      pingInterval = setInterval(() => {
        if (socket && socket.readyState === WebSocket.OPEN) {
          socket.send(JSON.stringify({ type: 'ping' }));
        }
      }, 25000);

      // Reconnection reconciliation: fetch full board snapshot to restore state missed during outage
      if (wasReconnecting) {
        await refetchBoardSnapshot();
      }
    };

    socket.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.type === 'pong') return;

        // Echo cancellation: ignore events originating from this specific tab
        if (data.client_id && data.client_id === tabClientId) {
          return;
        }

        // If local user is currently dragging an element, queue the event to avoid layout collisions
        if (isLocallyDragging) {
          queuedEvents.push(data);
        } else {
          applyRemoteEvent(data);
        }
      } catch (err) {
        console.error('Failed to parse WebSocket message:', err);
      }
    };

    socket.onerror = () => {
      updateWsStatus('reconnecting', 'Reconnecting...');
    };

    socket.onclose = (event) => {
      if (pingInterval) clearInterval(pingInterval);

      // Handle server-directed close codes
      if (event.code === 4401) {
        window.location.href = `/accounts/login/?next=${encodeURIComponent(window.location.pathname)}`;
        return;
      }
      if (event.code === 4403 || event.code === 4404) {
        alert('Your access to this board has ended or the board was deleted.');
        window.location.href = '/boards/';
        return;
      }

      scheduleReconnect();
    };
  }

  function scheduleReconnect() {
    updateWsStatus('reconnecting', 'Reconnecting...');
    reconnectAttempts++;
    const delay = Math.min(1000 * Math.pow(1.6, reconnectAttempts), 15000);
    setTimeout(() => {
      initWebSocket();
    }, delay);
  }

  // Refetch full board snapshot to reconcile state upon reconnecting
  async function refetchBoardSnapshot() {
    try {
      const res = await fetch(`/boards/${boardId}/snapshot/`, {
        headers: { 'X-Requested-With': 'XMLHttpRequest' },
      });
      if (!res.ok) return;
      const data = await res.json();
      if (!data.ok || !data.board) return;

      reconcileDomWithSnapshot(data.board);
      showToast('Reconnected. Board synchronized.', 'success');
    } catch (err) {
      console.warn('Snapshot reconciliation failed:', err);
    }
  }

  // Reconcile DOM with snapshot data
  function reconcileDomWithSnapshot(boardData) {
    // 1. Update role if changed
    if (boardData.current_role) {
      applyRoleUpdate(boardData.current_role);
    }

    // 2. Re-render columns and cards
    const track = document.getElementById('kanban-track');
    if (!track) return;

    const addListCol = document.getElementById('add-list-column');

    boardData.lists.forEach((colData) => {
      let colEl = document.getElementById(`list-col-${colData.id}`);
      if (!colEl) {
        colEl = createColumnElement(colData);
        if (addListCol) {
          track.insertBefore(colEl, addListCol);
        } else {
          track.appendChild(colEl);
        }
        const container = colEl.querySelector('.column-cards-container');
        initCardsSortable(container);
      } else {
        const titleText = document.getElementById(`list-title-text-${colData.id}`);
        if (titleText) titleText.textContent = colData.title;
      }

      // Reconcile cards in this column
      const cardsContainer = document.getElementById(`cards-container-${colData.id}`);
      if (!cardsContainer) return;

      cardsContainer.innerHTML = '';
      colData.cards.forEach((cardData) => {
        const cardEl = createCardElement(cardData);
        cardsContainer.appendChild(cardEl);
      });

      updateColumnMetadata(colEl);
    });
  }

  // ===========================================================================
  // 3. APPLY REMOTE SERVER EVENTS (Live Real-Time DOM Updates)
  // ===========================================================================

  function applyRemoteEvent(data) {
    const eventType = data.event;
    const payload = data.payload || {};

    switch (eventType) {
      case 'list.created': {
        const listData = payload.list;
        if (!listData) return;
        if (document.getElementById(`list-col-${listData.id}`)) return;

        const newCol = createColumnElement(listData);
        const addListCol = document.getElementById('add-list-column');
        if (addListCol) {
          addListCol.parentNode.insertBefore(newCol, addListCol);
        } else {
          document.getElementById('kanban-track').appendChild(newCol);
        }

        const newCardsContainer = newCol.querySelector('.column-cards-container');
        initCardsSortable(newCardsContainer);
        showToast(`Column '${listData.title}' added.`, 'info');
        break;
      }

      case 'list.renamed': {
        const listData = payload.list;
        if (!listData) return;
        const titleText = document.getElementById(`list-title-text-${listData.id}`);
        const titleInput = document.getElementById(`list-title-input-${listData.id}`);
        if (titleText) titleText.textContent = listData.title;
        if (titleInput) titleInput.value = listData.title;
        break;
      }

      case 'list.deleted': {
        const listId = payload.list_id;
        const colEl = document.getElementById(`list-col-${listId}`);
        if (colEl) {
          colEl.remove();
          showToast('A column was deleted.', 'info');
        }
        break;
      }

      case 'list.moved': {
        const listId = payload.list_id;
        const newPosition = payload.new_position;
        const track = document.getElementById('kanban-track');
        const listEl = document.getElementById(`list-col-${listId}`);
        if (!track || !listEl) return;

        const columns = Array.from(track.querySelectorAll('.kanban-column')).filter(
          (c) => c !== listEl
        );

        const addCol = document.getElementById('add-list-column');
        if (newPosition >= columns.length) {
          if (addCol) track.insertBefore(listEl, addCol);
          else track.appendChild(listEl);
        } else {
          track.insertBefore(listEl, columns[newPosition]);
        }
        listEl.dataset.position = newPosition;
        break;
      }

      case 'card.created': {
        const cardData = payload.card;
        if (!cardData) return;
        if (document.getElementById(`card-${cardData.id}`)) return;

        const container = document.getElementById(`cards-container-${cardData.list_id}`);
        if (container) {
          const cardEl = createCardElement(cardData);
          container.appendChild(cardEl);
          const colEl = document.getElementById(`list-col-${cardData.list_id}`);
          updateColumnMetadata(colEl);
        }
        break;
      }

      case 'card.updated': {
        const cardData = payload.card;
        if (!cardData) return;
        const cardEl = document.getElementById(`card-${cardData.id}`);
        if (cardEl) {
          cardEl.dataset.title = cardData.title;
          cardEl.dataset.description = cardData.description || '';

          const titleEl = cardEl.querySelector('.task-card-title');
          if (titleEl) titleEl.textContent = cardData.title;

          let descEl = cardEl.querySelector('.task-card-desc');
          if (cardData.description) {
            if (!descEl) {
              descEl = document.createElement('div');
              descEl.className = 'task-card-desc';
              cardEl.querySelector('.task-card-footer').before(descEl);
            }
            descEl.textContent = cardData.description;
          } else if (descEl) {
            descEl.remove();
          }
        }

        // Modal collision: If this card is currently open in modal, update live
        const currentModalCardId = document.getElementById('modal-card-id');
        if (currentModalCardId && currentModalCardId.value == cardData.id) {
          const modalTitle = document.getElementById('modal-card-title');
          const modalDesc = document.getElementById('modal-card-desc');
          if (modalTitle) modalTitle.value = cardData.title;
          if (modalDesc) modalDesc.value = cardData.description || '';
          showToast('Card updated by a collaborator.', 'info');
        }
        break;
      }

      case 'card.deleted': {
        const cardId = payload.card_id;
        const listId = payload.list_id;
        const cardEl = document.getElementById(`card-${cardId}`);
        if (cardEl) {
          const colEl = cardEl.closest('.kanban-column') || document.getElementById(`list-col-${listId}`);
          cardEl.remove();
          if (colEl) updateColumnMetadata(colEl);
        }

        // Modal collision: If this card is currently open in modal, close modal and warn
        const currentModalCardId = document.getElementById('modal-card-id');
        if (currentModalCardId && currentModalCardId.value == cardId) {
          closeCardModal();
          showToast('The card you were viewing was deleted by another user.', 'error');
        }
        break;
      }

      case 'card.moved': {
        const { card_id, source_list_id, target_list_id, new_position } = payload;
        const cardEl = document.getElementById(`card-${card_id}`);
        const targetContainer = document.getElementById(`cards-container-${target_list_id}`);
        if (!cardEl || !targetContainer) return;

        const siblings = Array.from(targetContainer.querySelectorAll('.task-card')).filter(
          (c) => c !== cardEl
        );

        if (new_position >= siblings.length) {
          targetContainer.appendChild(cardEl);
        } else {
          targetContainer.insertBefore(cardEl, siblings[new_position]);
        }

        cardEl.dataset.listId = target_list_id;
        cardEl.dataset.position = new_position;

        const srcCol = document.getElementById(`list-col-${source_list_id}`);
        const tgtCol = document.getElementById(`list-col-${target_list_id}`);
        updateColumnMetadata(srcCol);
        if (source_list_id !== target_list_id) {
          updateColumnMetadata(tgtCol);
        }
        break;
      }

      case 'member.role_changed': {
        const { user_id, new_role } = payload;
        if (user_id === currentUserId) {
          applyRoleUpdate(new_role);
          showToast(`Your workspace role was updated to ${new_role}.`, 'info');
        }
        break;
      }

      case 'member.removed': {
        const { user_id } = payload;
        if (user_id === currentUserId) {
          alert('You have been removed from this board workspace.');
          window.location.href = '/boards/';
        }
        break;
      }

      case 'board.deleted': {
        alert('This board has been deleted by its owner.');
        window.location.href = '/boards/';
        break;
      }

      default:
        break;
    }
  }

  // Update permissions & UI dynamically when current user role changes live
  function applyRoleUpdate(newRole) {
    canEdit = (newRole === 'OWNER' || newRole === 'EDITOR');
    boardApp.dataset.canEdit = canEdit ? 'true' : 'false';

    const roleBadge = document.getElementById('user-role-badge');
    if (roleBadge) {
      roleBadge.className = `role-badge role-badge-${newRole.toLowerCase()}`;
      roleBadge.textContent = `Your Role: ${newRole}`;
    }

    const readonlyIndicator = document.getElementById('readonly-indicator');
    if (readonlyIndicator) {
      readonlyIndicator.style.display = canEdit ? 'none' : 'inline-block';
    }

    // Toggle SortableJS drag permissions
    cardSortables.forEach((s) => s.option('disabled', !canEdit));
    if (columnSortable) columnSortable.option('disabled', !canEdit);

    // Toggle add card buttons & forms
    document.querySelectorAll('.btn-toggle-add-card, .list-drag-handle, .delete-list-btn').forEach((el) => {
      el.style.display = canEdit ? '' : 'none';
    });

    const addListCol = document.getElementById('add-list-column');
    if (addListCol) {
      addListCol.style.display = canEdit ? 'block' : 'none';
    }
  }

  // Start WebSocket client
  initWebSocket();

  // ===========================================================================
  // 4. INLINE ADD LIST
  // ===========================================================================

  const btnShowAddList = document.getElementById('btn-show-add-list');
  const btnCancelAddList = document.getElementById('btn-cancel-add-list');
  const addListForm = document.getElementById('add-list-form');
  const addListTitleInput = document.getElementById('add-list-title');
  const btnSubmitAddList = document.getElementById('btn-submit-add-list');

  if (btnShowAddList && addListForm && addListTitleInput) {
    btnShowAddList.addEventListener('click', () => {
      btnShowAddList.style.display = 'none';
      addListForm.style.display = 'block';
      addListTitleInput.focus();
    });

    function hideAddListForm() {
      addListForm.style.display = 'none';
      btnShowAddList.style.display = 'block';
      addListTitleInput.value = '';
    }

    if (btnCancelAddList) {
      btnCancelAddList.addEventListener('click', hideAddListForm);
    }

    async function handleAddList() {
      const title = addListTitleInput.value.trim();
      if (!title) {
        showToast('Please enter a list title.', 'error');
        return;
      }

      btnSubmitAddList.disabled = true;
      try {
        const data = await apiPost(`/boards/${boardId}/lists/create/`, { title });
        const newList = data.list;

        const newCol = createColumnElement(newList);
        const addListCol = document.getElementById('add-list-column');
        addListCol.parentNode.insertBefore(newCol, addListCol);

        const newCardsContainer = newCol.querySelector('.column-cards-container');
        initCardsSortable(newCardsContainer);

        hideAddListForm();
        showToast(`List '${newList.title}' created!`, 'success');
      } catch (err) {
        showToast(err.message, 'error');
      } finally {
        btnSubmitAddList.disabled = false;
      }
    }

    if (btnSubmitAddList) {
      btnSubmitAddList.addEventListener('click', handleAddList);
    }

    addListTitleInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        handleAddList();
      } else if (e.key === 'Escape') {
        hideAddListForm();
      }
    });
  }

  // Helper: Create HTML Element for a new Column
  function createColumnElement(list) {
    const col = document.createElement('div');
    col.className = 'kanban-column';
    col.dataset.listId = list.id;
    col.dataset.position = list.position;
    col.id = `list-col-${list.id}`;

    col.innerHTML = `
      <div class="column-header">
        <div class="column-title-wrap">
          <span class="list-drag-handle" title="Drag to reorder column" style="${canEdit ? '' : 'display: none;'}">⋮⋮</span>
          <span class="column-title-text" id="list-title-text-${list.id}" title="Click to rename list">${escapeHtml(list.title)}</span>
          <input type="text" class="column-title-input" id="list-title-input-${list.id}" value="${escapeHtml(list.title)}" style="display: none;" />
        </div>
        <div class="column-header-actions">
          <span class="column-count" id="list-count-${list.id}">0</span>
          <button type="button" class="btn-icon btn-icon-danger delete-list-btn"
                  data-list-id="${list.id}"
                  data-list-title="${escapeHtml(list.title)}"
                  data-card-count="0"
                  title="Delete this list"
                  style="${canEdit ? '' : 'display: none;'}">
            ✕
          </button>
        </div>
      </div>
      <div class="column-cards-container" data-list-id="${list.id}" id="cards-container-${list.id}"></div>
      <div class="add-card-section" id="add-card-section-${list.id}">
        <button type="button" class="btn-toggle-add-card" id="btn-show-add-card-${list.id}" data-list-id="${list.id}" style="${canEdit ? '' : 'display: none;'}">
          + Add a card
        </button>
        <div class="add-card-form" id="add-card-form-${list.id}" style="display: none;">
          <textarea class="add-card-input" id="add-card-title-${list.id}" placeholder="Enter a title for this card..." rows="2"></textarea>
          <div class="add-card-actions">
            <button type="button" class="btn btn-primary btn-sm btn-submit-add-card" data-list-id="${list.id}">
              Add Card
            </button>
            <button type="button" class="btn btn-ghost btn-sm btn-cancel-add-card" data-list-id="${list.id}">
              Cancel
            </button>
          </div>
        </div>
      </div>
    `;

    return col;
  }

  // ===========================================================================
  // 5. INLINE ADD CARD (Delegated Event Listeners)
  // ===========================================================================

  document.addEventListener('click', async (e) => {
    const toggleBtn = e.target.closest('.btn-toggle-add-card');
    if (toggleBtn) {
      const listId = toggleBtn.dataset.listId;
      const form = document.getElementById(`add-card-form-${listId}`);
      const input = document.getElementById(`add-card-title-${listId}`);
      if (form) {
        toggleBtn.style.display = 'none';
        form.style.display = 'block';
        if (input) input.focus();
      }
      return;
    }

    const cancelBtn = e.target.closest('.btn-cancel-add-card');
    if (cancelBtn) {
      const listId = cancelBtn.dataset.listId;
      const form = document.getElementById(`add-card-form-${listId}`);
      const toggle = document.getElementById(`btn-show-add-card-${listId}`);
      const input = document.getElementById(`add-card-title-${listId}`);
      if (form && toggle) {
        form.style.display = 'none';
        toggle.style.display = 'block';
        if (input) input.value = '';
      }
      return;
    }

    const submitBtn = e.target.closest('.btn-submit-add-card');
    if (submitBtn) {
      const listId = submitBtn.dataset.listId;
      const input = document.getElementById(`add-card-title-${listId}`);
      if (!input) return;

      const title = input.value.trim();
      if (!title) {
        showToast('Please enter a card title.', 'error');
        return;
      }

      submitBtn.disabled = true;
      try {
        const data = await apiPost(`/boards/${boardId}/lists/${listId}/cards/create/`, { title });
        const newCard = data.card;

        const cardEl = createCardElement(newCard);
        const container = document.getElementById(`cards-container-${listId}`);
        if (container) {
          container.appendChild(cardEl);
          const colEl = document.getElementById(`list-col-${listId}`);
          updateColumnMetadata(colEl);
        }

        input.value = '';
        input.focus();
        showToast('Card created!', 'success');
      } catch (err) {
        showToast(err.message, 'error');
      } finally {
        submitBtn.disabled = false;
      }
    }
  });

  document.addEventListener('keydown', (e) => {
    if (e.target.matches('.add-card-input') && e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      const listId = e.target.id.replace('add-card-title-', '');
      const submitBtn = document.querySelector(`.btn-submit-add-card[data-list-id="${listId}"]`);
      if (submitBtn) submitBtn.click();
    } else if (e.target.matches('.add-card-input') && e.key === 'Escape') {
      const listId = e.target.id.replace('add-card-title-', '');
      const cancelBtn = document.querySelector(`.btn-cancel-add-card[data-list-id="${listId}"]`);
      if (cancelBtn) cancelBtn.click();
    }
  });

  // Helper: Create HTML Element for a new Card
  function createCardElement(card) {
    const cardEl = document.createElement('div');
    cardEl.className = 'task-card';
    cardEl.dataset.cardId = card.id;
    cardEl.dataset.listId = card.list_id;
    cardEl.dataset.position = card.position;
    cardEl.dataset.title = card.title;
    cardEl.dataset.description = card.description || '';
    cardEl.dataset.author = card.author;
    cardEl.dataset.authorInitials = card.author_initials;
    cardEl.dataset.authorColor = card.author_color || '#4F46E5';
    cardEl.id = `card-${card.id}`;

    cardEl.innerHTML = `
      <div class="task-card-title">${escapeHtml(card.title)}</div>
      ${card.description ? `<div class="task-card-desc">${escapeHtml(card.description)}</div>` : ''}
      <div class="task-card-footer">
        <div class="task-card-author">
          <span class="avatar-bubble avatar-bubble-sm" style="width: 20px; height: 20px; font-size: 0.65rem; background-color: ${card.author_color || '#4F46E5'};">
            ${escapeHtml(card.author_initials)}
          </span>
          <span>@${escapeHtml(card.author)}</span>
        </div>
        <span class="card-position-badge">#${card.position}</span>
      </div>
    `;

    return cardEl;
  }

  // ===========================================================================
  // 6. INLINE LIST RENAMING
  // ===========================================================================

  document.addEventListener('click', (e) => {
    if (!canEdit) return;
    const titleText = e.target.closest('.column-title-text');
    if (!titleText) return;

    const listId = titleText.id.replace('list-title-text-', '');
    const titleInput = document.getElementById(`list-title-input-${listId}`);
    if (!titleInput) return;

    titleText.style.display = 'none';
    titleInput.style.display = 'inline-block';
    titleInput.focus();
    titleInput.select();

    async function finishRename() {
      const newTitle = titleInput.value.trim();
      titleInput.style.display = 'none';
      titleText.style.display = 'inline-block';

      if (!newTitle || newTitle === titleText.textContent.trim()) {
        titleInput.value = titleText.textContent.trim();
        return;
      }

      const prevTitle = titleText.textContent;
      titleText.textContent = newTitle;

      try {
        await apiPost(`/boards/${boardId}/lists/${listId}/rename/`, { title: newTitle });
        showToast('List renamed.', 'success');
      } catch (err) {
        titleText.textContent = prevTitle;
        titleInput.value = prevTitle;
        showToast(`Could not rename list: ${err.message}`, 'error');
      }
    }

    titleInput.onblur = finishRename;
    titleInput.onkeydown = (keyEvent) => {
      if (keyEvent.key === 'Enter') {
        keyEvent.preventDefault();
        titleInput.blur();
      } else if (keyEvent.key === 'Escape') {
        titleInput.value = titleText.textContent.trim();
        titleInput.style.display = 'none';
        titleText.style.display = 'inline-block';
      }
    };
  });

  // ===========================================================================
  // 7. DELETE LIST CONFIRMATION MODAL
  // ===========================================================================

  const deleteListModal = document.getElementById('delete-list-modal');
  const deleteListTitleTarget = document.getElementById('delete-list-title-target');
  const deleteListCardCountTarget = document.getElementById('delete-list-card-count-target');
  const deleteListTargetId = document.getElementById('delete-list-target-id');
  const deleteListModalClose = document.getElementById('delete-list-modal-close');
  const deleteListCancelBtn = document.getElementById('delete-list-cancel-btn');
  const deleteListConfirmBtn = document.getElementById('delete-list-confirm-btn');

  function openDeleteListModal(listId, title, cardCount) {
    if (!deleteListModal) return;
    deleteListTargetId.value = listId;
    deleteListTitleTarget.textContent = `"${title}"`;
    deleteListCardCountTarget.textContent = cardCount;
    deleteListModal.style.display = 'flex';
  }

  function closeDeleteListModal() {
    if (!deleteListModal) return;
    deleteListModal.style.display = 'none';
  }

  if (deleteListModalClose) deleteListModalClose.addEventListener('click', closeDeleteListModal);
  if (deleteListCancelBtn) deleteListCancelBtn.addEventListener('click', closeDeleteListModal);
  if (deleteListModal) {
    deleteListModal.addEventListener('click', (e) => {
      if (e.target === deleteListModal) closeDeleteListModal();
    });
  }

  document.addEventListener('click', (e) => {
    const deleteBtn = e.target.closest('.delete-list-btn');
    if (deleteBtn) {
      const listId = deleteBtn.dataset.listId;
      const title = deleteBtn.dataset.listTitle;
      const count = deleteBtn.dataset.cardCount;
      openDeleteListModal(listId, title, count);
    }
  });

  if (deleteListConfirmBtn) {
    deleteListConfirmBtn.addEventListener('click', async () => {
      const listId = deleteListTargetId.value;
      if (!listId) return;

      deleteListConfirmBtn.disabled = true;
      try {
        await apiPost(`/boards/${boardId}/lists/${listId}/delete/`, {});
        const colEl = document.getElementById(`list-col-${listId}`);
        if (colEl) colEl.remove();

        closeDeleteListModal();
        showToast('List and associated cards deleted.', 'success');
      } catch (err) {
        showToast(`Could not delete list: ${err.message}`, 'error');
      } finally {
        deleteListConfirmBtn.disabled = false;
      }
    });
  }

  // ===========================================================================
  // 8. CARD EDIT / DELETE MODAL
  // ===========================================================================

  const cardModal = document.getElementById('card-modal');
  const cardModalClose = document.getElementById('card-modal-close');
  const cardModalCancel = document.getElementById('modal-card-cancel-btn');
  const cardEditForm = document.getElementById('card-edit-form');
  const modalCardId = document.getElementById('modal-card-id');
  const modalCardTitle = document.getElementById('modal-card-title');
  const modalCardDesc = document.getElementById('modal-card-desc');
  const modalCardMeta = document.getElementById('modal-card-meta');
  const modalCardDeleteBtn = document.getElementById('modal-card-delete-btn');
  const modalCardSaveBtn = document.getElementById('modal-card-save-btn');

  function openCardModal(cardEl) {
    if (!cardModal) return;

    modalCardId.value = cardEl.dataset.cardId;
    modalCardTitle.value = cardEl.dataset.title;
    modalCardDesc.value = cardEl.dataset.description || '';

    const author = cardEl.dataset.author || 'User';
    const initials = cardEl.dataset.authorInitials || author[0] || 'U';
    const color = cardEl.dataset.authorColor || '#4F46E5';

    if (modalCardMeta) {
      modalCardMeta.innerHTML = `
        <div style="display: flex; align-items: center; gap: 0.5rem; margin-top: 0.5rem;">
          <span>Created by:</span>
          <span class="avatar-bubble avatar-bubble-sm" style="width: 18px; height: 18px; font-size: 0.6rem; background-color: ${color};">
            ${escapeHtml(initials)}
          </span>
          <span style="font-weight: 500;">@${escapeHtml(author)}</span>
        </div>
      `;
    }

    cardModal.style.display = 'flex';
    modalCardTitle.focus();
  }

  function closeCardModal() {
    if (!cardModal) return;
    cardModal.style.display = 'none';
  }

  if (cardModalClose) cardModalClose.addEventListener('click', closeCardModal);
  if (cardModalCancel) cardModalCancel.addEventListener('click', closeCardModal);
  if (cardModal) {
    cardModal.addEventListener('click', (e) => {
      if (e.target === cardModal) closeCardModal();
    });
  }

  document.addEventListener('click', (e) => {
    if (e.target.closest('button') || e.target.closest('input') || e.target.closest('textarea')) {
      return;
    }

    const cardEl = e.target.closest('.task-card');
    if (cardEl) {
      openCardModal(cardEl);
    }
  });

  // Save Card Updates
  if (cardEditForm) {
    cardEditForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      if (!canEdit) return;

      const cardId = modalCardId.value;
      const title = modalCardTitle.value.trim();
      const description = modalCardDesc.value.trim();

      if (!title) {
        showToast('Card title cannot be empty.', 'error');
        return;
      }

      if (modalCardSaveBtn) modalCardSaveBtn.disabled = true;
      try {
        const data = await apiPost(`/boards/${boardId}/cards/${cardId}/update/`, {
          title,
          description,
        });

        const cardEl = document.getElementById(`card-${cardId}`);
        if (cardEl) {
          cardEl.dataset.title = data.card.title;
          cardEl.dataset.description = data.card.description || '';

          const titleEl = cardEl.querySelector('.task-card-title');
          if (titleEl) titleEl.textContent = data.card.title;

          let descEl = cardEl.querySelector('.task-card-desc');
          if (data.card.description) {
            if (!descEl) {
              descEl = document.createElement('div');
              descEl.className = 'task-card-desc';
              cardEl.querySelector('.task-card-footer').before(descEl);
            }
            descEl.textContent = data.card.description;
          } else if (descEl) {
            descEl.remove();
          }
        }

        closeCardModal();
        showToast('Card updated successfully.', 'success');
      } catch (err) {
        showToast(`Could not update card: ${err.message}`, 'error');
      } finally {
        if (modalCardSaveBtn) modalCardSaveBtn.disabled = false;
      }
    });
  }

  // Delete Card
  if (modalCardDeleteBtn) {
    modalCardDeleteBtn.addEventListener('click', async () => {
      if (!canEdit) return;
      const cardId = modalCardId.value;
      if (!cardId) return;

      if (!confirm('Are you sure you want to permanently delete this card?')) {
        return;
      }

      modalCardDeleteBtn.disabled = true;
      try {
        await apiPost(`/boards/${boardId}/cards/${cardId}/delete/`, {});
        const cardEl = document.getElementById(`card-${cardId}`);
        if (cardEl) {
          const colEl = cardEl.closest('.kanban-column');
          cardEl.remove();
          if (colEl) updateColumnMetadata(colEl);
        }

        closeCardModal();
        showToast('Card deleted.', 'success');
      } catch (err) {
        showToast(`Could not delete card: ${err.message}`, 'error');
      } finally {
        modalCardDeleteBtn.disabled = false;
      }
    });
  }
});
