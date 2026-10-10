/**
 * TaskFlow — Interactive Kanban Board Client Script (Phase 2)
 *
 * Responsibilities:
 * - Initializes SortableJS on columns (horizontal) and cards (vertical + cross-column)
 * - Handles inline list and card creation via fetch()
 * - Handles inline list title renaming and deletion confirmation
 * - Handles card detail viewing, updating, and deletion modal
 * - Enforces optimistic updates with automated rollback on server rejection
 * - Disables all mutations and drag handles for VIEWER roles
 */

document.addEventListener('DOMContentLoaded', () => {
  const boardApp = document.getElementById('board-app');
  if (!boardApp) return;

  const boardId = boardApp.dataset.boardId;
  const canEdit = boardApp.dataset.canEdit === 'true';

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

  // Helper: Standardized AJAX JSON POST request
  async function apiPost(url, data) {
    const res = await fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': getCsrfToken(),
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
  // 1. DRAG AND DROP (SortableJS)
  // ===========================================================================

  const cardSortables = [];
  let columnSortable = null;

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

      // Track source position before drag begins to allow rollback
      onStart: (evt) => {
        evt.item._originalParent = evt.from;
        evt.item._originalIndex = evt.oldIndex;
      },

      onEnd: async (evt) => {
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
        evt.item._originalIndex = evt.oldIndex;
      },

      onEnd: async (evt) => {
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

  // Initialize existing card containers
  document.querySelectorAll('.column-cards-container').forEach(initCardsSortable);
  initColumnsSortable();

  // ===========================================================================
  // 2. INLINE ADD LIST
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

        // Render new column DOM
        const newCol = createColumnElement(newList);
        const addListCol = document.getElementById('add-list-column');
        addListCol.parentNode.insertBefore(newCol, addListCol);

        // Initialize Sortable on the new column's card container
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
          ${canEdit ? '<span class="list-drag-handle" title="Drag to reorder column">⋮⋮</span>' : ''}
          <span class="column-title-text" id="list-title-text-${list.id}" ${canEdit ? 'title="Click to rename list"' : ''}>${escapeHtml(list.title)}</span>
          ${canEdit ? `<input type="text" class="column-title-input" id="list-title-input-${list.id}" value="${escapeHtml(list.title)}" style="display: none;" />` : ''}
        </div>
        <div class="column-header-actions">
          <span class="column-count" id="list-count-${list.id}">0</span>
          ${canEdit ? `
            <button type="button" class="btn-icon btn-icon-danger delete-list-btn"
                    data-list-id="${list.id}"
                    data-list-title="${escapeHtml(list.title)}"
                    data-card-count="0"
                    title="Delete this list">
              ✕
            </button>
          ` : ''}
        </div>
      </div>
      <div class="column-cards-container" data-list-id="${list.id}" id="cards-container-${list.id}"></div>
      ${canEdit ? `
        <div class="add-card-section" id="add-card-section-${list.id}">
          <button type="button" class="btn-toggle-add-card" id="btn-show-add-card-${list.id}" data-list-id="${list.id}">
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
      ` : ''}
    `;

    return col;
  }

  // ===========================================================================
  // 3. INLINE ADD CARD (Delegated Event Listeners)
  // ===========================================================================

  document.addEventListener('click', async (e) => {
    // Show inline add card form
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

    // Cancel inline add card form
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

    // Submit inline add card form
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

        // Render and append card element
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

  // Support Enter key submission inside add card textarea
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
  // 4. INLINE LIST RENAMING
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
  // 5. DELETE LIST CONFIRMATION MODAL
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
  // 6. CARD EDIT / DELETE MODAL
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

  // Open modal on card click
  document.addEventListener('click', (e) => {
    // Ignore clicks on buttons or inside add forms
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

        // Update card in DOM
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
