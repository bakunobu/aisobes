# Органайзер и Тайм-Трекер — Архитектурный План v3

## 1. Иерархия сущностей (пересмотрено)

Пользователь уточнил модель данных — теперь используется четкая трехуровневая структура:

```
Problem (проблема/эпик)
  └── Task (задача)          — many Tasks per Problem
        └── Subtask (подзадача) — many Subtasks per Task
```

Каждый уровень поддерживает теги (many-to-many). Все изменения (promote/split) логируются в ChangeLog.

---

## 2. Модель данных (SQLAlchemy ORM + Flask-Migrate)

### 2.1 Tag
```
Tag
├── id            INTEGER PK
├── tag           TEXT NOT NULL UNIQUE
```

### 2.2 Problem
```
Problem
├── id                  INTEGER PK
├── description         TEXT NOT NULL
├── created             DATETIME
├── first_run           DATETIME (nullable — первый запуск трекинга)
├── last_run            DATETIME (nullable — последний запуск трекинга)
├── number_of_runs      INTEGER DEFAULT 0
├── completed_runs      INTEGER DEFAULT 0
├── interrupted_runs    INTEGER DEFAULT 0
├── total_time_spent    INTEGER DEFAULT 0 (секунды)
├── is_completed        BOOLEAN DEFAULT FALSE
├── is_archived         BOOLEAN DEFAULT FALSE
├── is_deleted          BOOLEAN DEFAULT FALSE (soft delete)
```

### 2.3 ProblemTag (mapping)
```
ProblemTag
├── problem_id   INTEGER FK → Problem.id
├── tag_id       INTEGER FK → Tag.id
└── PK (problem_id, tag_id)
```

### 2.4 Task
```
Task
├── id                  INTEGER PK
├── problem_id          INTEGER FK → Problem.id (NOT NULL)
├── created             DATETIME
├── first_run           DATETIME (nullable)
├── total_time_spent    INTEGER DEFAULT 0 (секунды)
├── is_completed        BOOLEAN DEFAULT FALSE
├── is_archived         BOOLEAN DEFAULT FALSE
├── is_deleted          BOOLEAN DEFAULT FALSE
```

### 2.5 TaskTag (mapping)
```
TaskTag
├── task_id      INTEGER FK → Task.id
├── tag_id       INTEGER FK → Tag.id
└── PK (task_id, tag_id)
```

### 2.6 Subtask
```
Subtask
├── id                  INTEGER PK
├── task_id             INTEGER FK → Task.id (NOT NULL)
├── created             DATETIME
├── first_run           DATETIME (nullable)
├── total_time_spent    INTEGER DEFAULT 0 (секунды)
├── is_completed        BOOLEAN DEFAULT FALSE
├── is_archived         BOOLEAN DEFAULT FALSE
├── is_deleted          BOOLEAN DEFAULT FALSE
```

### 2.7 SubtaskTag (mapping)
```
SubtaskTag
├── subtask_id   INTEGER FK → Subtask.id
├── tag_id       INTEGER FK → Tag.id
└── PK (subtask_id, tag_id)
```

### 2.8 ChangeLog
```
ChangeLog
├── id              INTEGER PK
├── entity_type     TEXT  ('problem' | 'task' | 'subtask')
├── entity_id       INTEGER
├── change_type     TEXT  ('promote' | 'split' | 'merge' | 'archive' | 'complete' | 'delete')
├── from_parent_id  INTEGER (nullable — ID родителя до изменения)
├── to_parent_id    INTEGER (nullable — ID родителя после изменения)
├── description     TEXT
├── created         DATETIME
```

### 2.9 User
```
User
├── id              INTEGER PK
├── name            TEXT NOT NULL
├── email           TEXT NOT NULL UNIQUE
├── password_hash   TEXT NOT NULL
├── avatar          TEXT (nullable — URL or path to avatar image)
├── role            TEXT DEFAULT 'member'  ('admin' | 'member')
├── is_active       BOOLEAN DEFAULT TRUE
├── last_login      DATETIME (nullable)
├── created         DATETIME
```

### 2.10 UserEntityRole (polymorphic join)
```
UserEntityRole
├── id              INTEGER PK
├── user_id         INTEGER FK → User.id (NOT NULL)
├── entity_type     TEXT  ('problem' | 'task' | 'subtask')
├── entity_id       INTEGER (NOT NULL)
├── role            TEXT  ('owner' | 'creator' | 'participant' | 'assignee')
├── created         DATETIME
└── UNIQUE (user_id, entity_type, entity_id, role)
```

A user can hold multiple roles on the same entity (e.g., both `creator` and `owner`).
The `UserEntityRole` table follows the same polymorphic pattern as `ChangeLog`
(`entity_type` + `entity_id`), keeping the design consistent.

---

## 3. Технологии

| Слой | Выбор |
|------|-------|
| ORM | **SQLAlchemy** (через Flask-SQLAlchemy) |
| Миграции | **Flask-Migrate** (Alembic) |
| База данных | SQLite |
| Backend | Flask (минималистичный) |

---

## 4. Порядок реализации (текущий этап)

1. Очистить [`app.py`](app.py) до минимального Flask-скелета
2. Добавить зависимости: `flask-sqlalchemy`, `flask-migrate`
3. Создать [`models.py`](models.py) со всеми 8 моделями
4. Настроить Flask-Migrate, создать начальную миграцию

---

## 5. Остальные разделы плана

Разделы про страницы, API, планировщик, геймификацию — см. v2 плана. Они будут адаптированы под новую модель Problem→Task→Subtask по мере реализации.
