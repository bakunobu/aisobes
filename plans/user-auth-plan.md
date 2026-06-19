# User Authentication — Implementation Plan

## Overview

Add email + password authentication to the Organizer app with a combined Login/Register user page. The `User` model already exists in [`models.py`](models.py:26) with `email`, `password_hash`, `name`, `role`, `is_active`, and `last_login` fields — we just need to wire up the auth flow.

## Architecture

```mermaid
flowchart TD
    A[Navigation Bar] --> B{Logged In?}
    B -->|No| C[/user page - Login Tab]
    B -->|No| D[/user page - Register Tab]
    B -->|Yes| E[Username + Logout Link]
    
    C --> F[POST /login]
    D --> G[POST /register]
    E --> H[GET /logout]
    
    F --> I{Valid Credentials?}
    I -->|Yes| J[Set session user_id]
    I -->|No| K[Flash error, redirect /user]
    
    G --> L{Valid Input?}
    L -->|Yes| M[hash_password, create User, set session]
    L -->|No| N[Flash error, redirect /user]
    
    J --> O[Redirect to /]
    M --> O
    H --> P[Clear session, redirect /]
```

## Files to Modify / Create

| File | Action | Description |
|------|--------|-------------|
| [`app.py`](app.py:1) | **Modify** | Add auth helpers, routes, decorator |
| [`templates/user.html`](templates/user.html) | **Create** | Combined Login/Register page with tabs |
| [`templates/home.html`](templates/home.html:1) | **Modify** | Update nav to show auth links |

## Step-by-Step Breakdown

### Step 1 — Password Hashing Helpers (`app.py`)

Add two utility functions using `werkzeug.security` (already available via Flask):

```python
from werkzeug.security import generate_password_hash, check_password_hash

def hash_password(password: str) -> str:
    return generate_password_hash(password)

def verify_password(password: str, password_hash: str) -> bool:
    return check_password_hash(password_hash, password)
```

### Step 2 — `login_required` Decorator (`app.py`)

A simple decorator that checks `session.get("user_id")` and redirects to `/user` if missing. Also injects the current `User` object into the route via `g.user`:

```python
from functools import wraps
from flask import g

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        user_id = session.get("user_id")
        if user_id is None:
            flash("Please log in first.", "error")
            return redirect(url_for("user_page"))
        g.user = models.User.query.get(user_id)
        return f(*args, **kwargs)
    return decorated
```

### Step 3 — Auth Routes (`app.py`)

| Route | Method | Purpose |
|-------|--------|---------|
| `/user` | GET | Render the combined login/register page |
| `/register` | POST | Create new user account |
| `/login` | POST | Authenticate existing user |
| `/logout` | GET | Clear session |

**`/register` (POST)** logic:
1. Read `email`, `password`, `confirm_password`, `name` from form
2. Validate: all fields required, passwords match, email not already taken, password ≥ 6 chars
3. `hash_password()` → create `User` → `db.session.add()` → `db.session.commit()`
4. Set `session["user_id"] = user.id`
5. Flash success, redirect to `/`

**`/login` (POST)** logic:
1. Read `email`, `password` from form
2. Query `User` by email
3. `verify_password()` against stored hash
4. Set `session["user_id"]`, update `last_login`
5. Flash success, redirect to `/`

### Step 4 — User Page Template (`templates/user.html`)

A single page with two tabs: **Login** and **Register**. Matches the existing UI style (Arial, tab-based nav, clean design).

**Layout:**
- Tab bar at top: "Login" | "Register"
- Active tab shows the corresponding form
- Login form: email, password, submit button
- Register form: name, email, password, confirm password, submit button
- Flash messages displayed above the forms
- If already logged in, show "You are logged in as {name}" with a logout button

**Form validation:** Client-side HTML5 validation (`type="email"`, `required`, `minlength`) plus server-side error flashes.

### Step 5 — Update Navigation (`templates/home.html`)

Modify the `.tabs` navigation bar to include auth-aware links:

- **When NOT logged in:** Show "Login / Register" tab linking to `/user`
- **When logged in:** Show username and "Logout" link

This requires passing `session` (or a `current_user` context) to the template. We'll use Flask's `session` object directly in Jinja2 templates (it's available by default).

### Step 6 — Database Migration

The `User` model already exists in [`models.py`](models.py:26). Run:

```bash
flask db migrate -m "ensure users table"
flask db upgrade
```

### Step 7 — Testing

Manual test flow:
1. Visit `/user` → see Login/Register tabs
2. Register with name, email, password → redirected to `/`, see username in nav
3. Logout → nav shows "Login / Register" again
4. Login with same credentials → redirected to `/`
5. Try duplicate email registration → see error flash
6. Try wrong password login → see error flash

## Edge Cases Covered

- **Duplicate email**: Checked before insert, flash error
- **Password mismatch**: Checked on registration, flash error
- **Short password**: Minimum 6 characters, validated server-side
- **Already logged in**: `/user` page shows current user info instead of forms
- **Invalid email format**: HTML5 `type="email"` + server-side validation
- **Non-existent user login**: Flash "Invalid email or password" (no user enumeration)