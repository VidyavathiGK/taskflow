# TaskFlow — Real-time Collaborative Task Board (Trello-Lite)

> **Phase 1: Project Skeleton, Core Schema, Authentication & Board Management**

TaskFlow is a collaborative Kanban task board built with Python 3.12 and Django 5.x on top of PostgreSQL.

In **Phase 1**, the project establishes:
- Secure authentication with a custom User model (equipped with `avatar_color` for live presence in later phases).
- Relational schema with role-based access control (RBAC): `Board`, `BoardMembership`, `BoardList`, and `Card`.
- Centralized access control returning `404 Not Found` for non-members to prevent workspace enumeration.
- Board workspace CRUD, member invitations, role promotions, and member removals restricted to the workspace `OWNER`.
- Django Admin with nested inlines for convenient entity inspection.
- A `seed_demo` management command for instant local testing.

---

## 🚀 Quickstart Setup

### 1. Prerequisites
- **Python 3.12+**
- **PostgreSQL** running locally on port 5432 (or use SQLite fallback if desired)

### 2. Virtual Environment & Dependencies
```bash
# Navigate to project directory
cd taskflow

# Create and activate virtual environment
python -m venv venv
.\venv\Scripts\activate   # On Windows PowerShell / CMD
# or source venv/bin/activate on macOS/Linux

# Install pinned dependencies
pip install -r requirements.txt
```

### 3. Environment Variables
Copy `.env.example` to `.env` and verify your PostgreSQL credentials:
```bash
cp .env.example .env
```

Your `.env` file contains:
```env
SECRET_KEY=django-insecure-taskflow-dev-secret-key-phase1-super-secure
DEBUG=True
ALLOWED_HOSTS=127.0.0.1,localhost

# Database Configuration (PostgreSQL)
DB_ENGINE=django.db.backends.postgresql
DB_NAME=taskflow
DB_USER=postgres
DB_PASSWORD=root
DB_HOST=localhost
DB_PORT=5432

# Or toggle SQLite for instant zero-dependency testing:
USE_SQLITE=False
```

### 4. Database Setup & Migrations
Ensure the `taskflow` database exists in PostgreSQL (or create it: `CREATE DATABASE taskflow;`), then apply migrations:
```bash
python manage.py migrate
```

### 5. Seed Demo Data
Populate the database with 3 demo users, 1 board workspace, 3 columns, and 8 sample task cards:
```bash
python manage.py seed_demo
```

**Seeded Demo Credentials** (all use password: `password123`):
- `alice` &mdash; Role: **OWNER** (full workspace management, invites, deletes)
- `bob` &mdash; Role: **EDITOR** (member with edit privileges)
- `charlie` &mdash; Role: **VIEWER** (read-only member)

*(An admin superuser `admin` / `admin123` is also available for accessing `/admin/`)*.

### 6. Run the Development Server
```bash
python manage.py runserver
```
Visit: [http://127.0.0.1:8000/](http://127.0.0.1:8000/)

---

## 📁 Project Structure

```
taskflow/
├── manage.py
├── requirements.txt            # Pinned Django 5.x, psycopg 3, python-decouple
├── .env.example                # Example environment file
├── .env                        # Local environment variables
├── .gitignore                  # Python, Django, venv, and DB ignore rules
├── taskflow/                   # Core project configuration
│   ├── __init__.py
│   ├── settings.py             # Decouple-driven settings, custom AUTH_USER_MODEL
│   ├── urls.py                 # Top-level routing: accounts/, boards/, admin/
│   ├── wsgi.py
│   └── asgi.py
├── accounts/                   # Authentication & user profile app
│   ├── models.py               # Custom User extending AbstractUser (avatar_color)
│   ├── forms.py                # SignUpForm with color picker widget
│   ├── views.py                # SignUpView with automatic post-registration login
│   ├── urls.py                 # /accounts/login/, /accounts/signup/, /accounts/logout/
│   ├── admin.py                # UserAdmin with avatar_color in fieldsets
│   └── tests.py                # User creation and auth flow tests
├── boards/                     # Workspace, membership & Kanban models
│   ├── models.py               # Board, BoardMembership, BoardList, Card
│   ├── permissions.py          # Centralized get_board_for_member & BoardAccessMixin
│   ├── forms.py                # BoardForm, AddMemberForm, UpdateMemberRoleForm
│   ├── views.py                # MyBoards, Create, Detail, Edit, Delete, ManageMembers
│   ├── urls.py                 # Workspace routes
│   ├── admin.py                # Inlines: BoardMembershipInline, BoardListInline, CardInline
│   ├── tests.py                # Access control & CRUD unit tests
│   └── management/
│       └── commands/
│           └── seed_demo.py    # Seed demo users, boards, lists, and cards
├── static/
│   └── css/
│       └── style.css           # Vanilla CSS modern design system
└── templates/
    ├── base.html               # Base layout with navbar, avatar badges, alerts
    ├── accounts/
    │   ├── login.html          # Login card with demo credential quick-hints
    │   └── signup.html         # Signup card with avatar color picker
    └── boards/
        ├── my_boards.html      # User's boards dashboard showing roles
        ├── board_create.html   # Board creation form
        ├── board_detail.html   # Workspace columns & cards read-only view
        ├── board_edit.html     # Owner edit board form
        ├── board_confirm_delete.html # Owner delete confirmation
        └── manage_members.html # Owner member roster, invites, role changes & removal
```

---

## 🧠 Database Schema & Architectural Explanations

Each model was designed specifically to support interview discussions regarding scalability, clean relational design, and security:

### 1. `accounts.User` (`AbstractUser`)
- **Why extend `AbstractUser` on migration 0001?**
  Django's default auth model is notoriously difficult to substitute once foreign keys have been registered across a project. Defining a custom user model upfront avoids migration deadlocks and provides flexibility to add features like `avatar_color` and future profile or OAuth fields without database surgery.
- **`avatar_color` (`CharField(max_length=7)`):**
  Stores a hex color string (e.g. `#4F46E5`). In Phase 1, it provides a distinctive colored avatar bubble in the navbar and on cards. In Phase 3 (WebSockets), it will power real-time cursor/presence indicators.

### 2. `boards.Board`
- **Role:** High-level collaborative workspace entity.
- **Fields:**
  - `title`: Name of the board workspace.
  - `description`: Context and workspace objectives.
  - `owner`: Foreign key to `User`. The board creator who retains final authority (board deletion, owner cannot be demoted).
  - `created_at` & `updated_at`: Timestamps for auditability and sorting.
- **Optimization:** Indexed on `owner` and `created_at`. Ordered by `['-updated_at']` so recently active boards surface first.

### 3. `boards.BoardMembership` (RBAC Join Table)
- **Role:** Explicit join table modeling the Many-to-Many relationship between `Board` and `User`.
- **Why an explicit through model instead of `ManyToManyField`?**
  An explicit through model allows storing critical relational metadata on the edge itself: the member's `role` (`OWNER`, `EDITOR`, `VIEWER`) and `joined_at` timestamp.
- **Constraints & Indexes:**
  - `UniqueConstraint(fields=['board', 'user'])`: Prevents duplicate memberships at the database level.
  - Composite index on `(board, user)`: Makes permission lookups on every request $O(1)$ fast.
  - Index on `user`: Enables instant querying of all boards a user belongs to for the "My Boards" dashboard.

### 4. `boards.BoardList`
- **Role:** Workflow columns within a board (e.g. "To Do", "In Progress", "Done").
- **Why named `BoardList`?**
  Prevents shadowing Python's built-in `list` primitive.
- **`position` (`PositiveIntegerField`):**
  Enables explicit arbitrary sequencing from left to right. This decouples visual column order from insertion primary keys, making drag-and-drop column reordering trivial in later phases.
- **Optimization:** Indexed on `(board, position)` and ordered by `['position', 'created_at']`.

### 5. `boards.Card`
- **Role:** Discrete task items nested inside a `BoardList`.
- **`position` (`PositiveIntegerField`):**
  Enables vertical ordering within a column.
- **`created_by` (`ForeignKey(User)`):**
  Preserves authorship and task provenance regardless of who edits or drags the card across columns.
- **Optimization:** Indexed on `(board_list, position)` and ordered by `['position', 'created_at']`.

---

## 🛡️ Security & Access Control

### Centralized Permission Layer (`boards/permissions.py`)
All authorization checks are consolidated in `get_board_for_member` and `BoardAccessMixin`:
1. **Preventing Workspace Enumeration (404 vs 403):**
   When a user requests a board they are not a member of (e.g., `/boards/999/`), TaskFlow raises **`Http404`** rather than `Http403`. Returning `403 Forbidden` would confirm to unauthorized users that a board ID exists, exposing private organizational boards to ID enumeration attacks.
2. **Owner-Only Enforcement:**
   Views modifying workspace metadata (`BoardUpdateView`, `BoardDeleteView`, `BoardMembersView`) inherit from `BoardOwnerRequiredMixin`. Non-members receive `404`, and non-owner members receive `403 PermissionDenied`.
3. **Owner Invariant Protection:**
   The board owner cannot be removed or demoted from the workspace, preventing orphaned boards.
