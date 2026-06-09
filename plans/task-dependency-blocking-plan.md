# Plan: Task & Project Dependency Blocking

## Goal

Prevent the system from offering tasks that cannot run concurrently because
they depend on the output of another task or project.  A task/project is
**blocked** (cannot be started) as long as any of its prerequisites are
incomplete.

## Scope

| Dependency type | Example |
|---|---|
| Task → Task | "Analyze scraped data" needs "Scrape raw data" finished first |
| Project → Project | "Build API layer" needs "Build database schema" completed first |
| Task → Project | A task explicitly needs an entire other project done |

Transitive blocking: if Project A blocks Project B, all of B's tasks are
blocked too.

---

## Implementation Order (16 steps)

### Step 1 — Add `EntityDependency` model to [`models.py`](models.py)

Insert after the `TaskTag` class (line ~190), before `Subtask`:

```python
class EntityDependency(db.Model):
    """Polymorphic dependency: entity → prerequisite (project or task level)."""
    __tablename__ = "entity_dependencies"

    id = db.Column(db.Integer, primary_key=True)
    entity_type = db.Column(
        db.String(20), nullable=False
    )  # 'project' | 'task'
    entity_id = db.Column(db.Integer, nullable=False)
    prerequisite_type = db.Column(
        db.String(20), nullable=False
    )  # 'project' | 'task'
    prerequisite_id = db.Column(db.Integer, nullable=False)
    created = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        db.UniqueConstraint(
            "entity_type", "entity_id",
            "prerequisite_type", "prerequisite_id",
            name="uq_entity_dependency",
        ),
    )

    def __repr__(self):
        return (
            f"<EntityDependency {self.entity_type}#{self.entity_id}"
            f" ← {self.prerequisite_type}#{self.prerequisite_id}>"
        )
```

### Step 2 — Generate Alembic migration

```bash
cd /home/bakunobu/projects/live_interview && flask db migrate -m "add_entity_dependencies"
```

### Step 3 — Add `is_blocked` hybrid property to `Project` model

Insert inside the `Project` class (after `is_routine`, around line 120):

```python
from sqlalchemy import exists, and_
from sqlalchemy.ext.hybrid import hybrid_property

class Project(db.Model):
    # ... existing fields ...

    @hybrid_property
    def is_blocked(self):
        """True if any prerequisite project is incomplete."""
        return db.session.query(EntityDependency).filter(
            EntityDependency.entity_type == 'project',
            EntityDependency.entity_id == self.id,
            EntityDependency.prerequisite_type == 'project',
        ).join(
            Project,
            EntityDependency.prerequisite_id == Project.id,
        ).filter(
            Project.is_completed == False,
            Project.is_deleted == False,
        ).count() > 0

    @is_blocked.expression
    def is_blocked(cls):
        return exists(
            select([1])
            .select_from(EntityDependency.__table__.join(
                Project.__table__,
                EntityDependency.prerequisite_id == Project.__table__.c.id,
            ))
            .where(
                and_(
                    EntityDependency.entity_type == 'project',
                    EntityDependency.entity_id == cls.id,
                    EntityDependency.prerequisite_type == 'project',
                    Project.__table__.c.is_completed == False,
                    Project.__table__.c.is_deleted == False,
                )
            )
        )
```

### Step 4 — Add `is_blocked` hybrid property to `Task` model

Insert inside the `Task` class (after `priority`, around line 164):

```python
class Task(db.Model):
    # ... existing fields and relationships ...

    @hybrid_property
    def is_blocked(self):
        """True if blocked by incomplete prerequisite task OR incomplete prerequisite project."""
        # A) Blocked by another task
        task_blocked = db.session.query(EntityDependency).filter(
            EntityDependency.entity_type == 'task',
            EntityDependency.entity_id == self.id,
            EntityDependency.prerequisite_type == 'task',
        ).join(
            Task,
            EntityDependency.prerequisite_id == Task.id,
        ).filter(
            Task.is_completed == False,
            Task.is_deleted == False,
        ).count() > 0

        # B) Blocked by a prerequisite project
        project_blocked = db.session.query(EntityDependency).filter(
            EntityDependency.entity_type == 'task',
            EntityDependency.entity_id == self.id,
            EntityDependency.prerequisite_type == 'project',
        ).join(
            Project,
            EntityDependency.prerequisite_id == Project.id,
        ).filter(
            Project.is_completed == False,
            Project.is_deleted == False,
        ).count() > 0

        # C) Transitive: owning project is itself blocked
        owner_blocked = False
        if self.project:
            owner_blocked = self.project.is_blocked

        return task_blocked or project_blocked or owner_blocked

    @is_blocked.expression
    def is_blocked(cls):
        task_block = exists(
            select([1])
            .select_from(EntityDependency.__table__.join(
                Task.__table__,
                EntityDependency.prerequisite_id == Task.__table__.c.id,
            ))
            .where(
                and_(
                    EntityDependency.entity_type == 'task',
                    EntityDependency.entity_id == cls.id,
                    EntityDependency.prerequisite_type == 'task',
                    Task.__table__.c.is_completed == False,
                    Task.__table__.c.is_deleted == False,
                )
            )
        )

        project_block = exists(
            select([1])
            .select_from(EntityDependency.__table__.join(
                Project.__table__,
                EntityDependency.prerequisite_id == Project.__table__.c.id,
            ))
            .where(
                and_(
                    EntityDependency.entity_type == 'task',
                    EntityDependency.entity_id == cls.id,
                    EntityDependency.prerequisite_type == 'project',
                    Project.__table__.c.is_completed == False,
                    Project.__table__.c.is_deleted == False,
                )
            )
        )

        # Transitive: owner project blocked
        owner_block = exists(
            select([1])
            .select_from(EntityDependency.__table__.join(
                Project.__table__,
                EntityDependency.prerequisite_id == Project.__table__.c.id,
            ))
            .where(
                and_(
                    EntityDependency.entity_type == 'project',
                    EntityDependency.entity_id == cls.project_id,
                    EntityDependency.prerequisite_type == 'project',
                    Project.__table__.c.is_completed == False,
                    Project.__table__.c.is_deleted == False,
                )
            )
        )

        from sqlalchemy import or_
        return or_(task_block, project_block, owner_block)
```

### Step 5 — Update `DECOMPOSE_IDEA_PROMPT` in [`utils.py`](utils.py)

**Replace the description bullet (lines 48-60) with:**

```python
- ``description`` — a clear, one-sentence description that answers
  "How do I know this is done?"  Make the expected result measurable:
  * For coding work: end with a deliverable reference such as
    "— deliverable: a commit with <what the code does> and passing tests".
  * For non-coding work: end with a verifiable artifact such as
    "— deliverable: a screenshot of <artifact>" or
    "— deliverable: a photo of <diagram/document>".
- ``depends_on`` — OPTIONAL array of 0-based indices.  OMIT or set [] when
  the item can start immediately.
  * On a PROJECT: indices into the parent ``problems`` array — use when this
    project cannot start until another project in this plan is completed.
  * On a TASK: indices into the enclosing ``tasks`` array — use when this
    task needs another task in the same project to finish first (because it
    consumes that task's output).
  * Subtasks do NOT have ``depends_on``.
```

**Replace the JSON schema block (lines 69-95) with:**

```python
{
    "shared_tags": ["tag1", "tag2"],
    "problems": [
        {
            "description": "<project description>",
            "depends_on": [],
            "estimated_time": 480,
            "priority": 2,
            "tags": ["tag1"],
            "tasks": [
                {
                    "description": "<task description>",
                    "depends_on": [],
                    "estimated_time": 240,
                    "priority": 2,
                    "tags": ["tag1", "tag2"],
                    "subtasks": [
                        {
                            "description": "<subtask description>",
                            "estimated_time": 120,
                            "priority": 3,
                            "tags": ["tag1"]
                        }
                    ]
                }
            ]
        }
    ]
}
```

### Step 6 — Update `SPLIT_TASK_PROMPT` in [`utils.py`](utils.py)

After the SMART description line (~483), insert before the Output line:

```python
- ``depends_on`` — OPTIONAL array of 0-based indices of prerequisite tasks
  in the output array.  Use when a new task cannot start until another new
  task is completed (sequential dependency).  Omit or set [] otherwise.
```

Add `"depends_on": []` to the example task object in the JSON schema (~490).

### Step 7 — Update `REFINE_TASK_PROMPT` in [`utils.py`](utils.py)

Same change as Step 6 — add the `depends_on` bullet and field to the schema
(~611 and ~619).

### Step 8 — Add `depends_on` to `_normalise_decomposed()` in [`utils.py`](utils.py)

In the task loop (inside `for task in prob["tasks"]:`, ~line 187), add:

```python
task.setdefault("depends_on", [])
```

In the project loop (inside `for prob in data["problems"]:`, ~line 175), add:

```python
prob.setdefault("depends_on", [])
```

**Add a new validation function** before `_normalise_decomposed()`:

```python
def _validate_depends_on(data: dict) -> None:
    """Check depends_on indices and detect dependency cycles."""
    problems = data.get("problems", [])
    n_proj = len(problems)

    for p_idx, prob in enumerate(problems):
        # Validate project-level depends_on
        deps = prob.get("depends_on", [])
        for d in deps:
            if not isinstance(d, int) or d < 0 or d >= n_proj:
                raise ValueError(
                    f"Invalid project depends_on index {d} in project {p_idx}"
                )
            if d == p_idx:
                raise ValueError(f"Project {p_idx} depends on itself")

        # Validate task-level depends_on
        tasks = prob.get("tasks", [])
        n_task = len(tasks)
        for t_idx, task in enumerate(tasks):
            t_deps = task.get("depends_on", [])
            for d in t_deps:
                if not isinstance(d, int) or d < 0 or d >= n_task:
                    raise ValueError(
                        f"Invalid task depends_on index {d} in task {t_idx}"
                        f" of project {p_idx}"
                    )
                if d == t_idx:
                    raise ValueError(
                        f"Task {t_idx} in project {p_idx} depends on itself"
                    )

    # Detect cycles via DFS
    for p_idx in range(n_proj):
        deps = problems[p_idx].get("depends_on", [])
        visited = set()
        stack = list(deps)
        while stack:
            cur = stack.pop()
            if cur == p_idx:
                raise ValueError(f"Circular dependency detected on project {p_idx}")
            if cur in visited:
                continue
            visited.add(cur)
            stack.extend(problems[cur].get("depends_on", []))
```

Call `_validate_depends_on(data)` inside `_normalise_decomposed()` right after
the existing validation (~line 170).

### Step 9 — Update `_save_plan_to_db()` in [`app.py`](app.py)

After creating all projects and tasks. **Find** the commit at line 131 and
insert dependency persistence BEFORE it. Replace the task loop (lines 98-129)
with:

```python
        # Track task index → db_task for dependency resolution
        task_index_map = {}

        for t_idx, tdata in enumerate(pdata.get("tasks", [])):
            # Task-level tags
            task_tags = []
            for tname in tdata.get("tags", []):
                tag = _get_or_create_tag(tname)
                if tag:
                    task_tags.append(tag)

            task = models.Task(
                project_id=project.id,
                estimated_time=(tdata.get("estimated_time", 0) or 0) * 60,
                priority=tdata.get("priority", 3),
            )
            task.tags = task_tags
            db.session.add(task)
            db.session.flush()

            task_index_map[t_idx] = task

            for sdata in tdata.get("subtasks", []):
                # Subtask-level tags
                sub_tags = []
                for tname in sdata.get("tags", []):
                    tag = _get_or_create_tag(tname)
                    if tag:
                        sub_tags.append(tag)

                subtask = models.Subtask(
                    task_id=task.id,
                    estimated_time=(sdata.get("estimated_time", 0) or 0) * 60,
                    priority=sdata.get("priority", 3),
                )
                subtask.tags = sub_tags
                db.session.add(subtask)

        # --- Persist task-level dependencies ---
        for t_idx, tdata in enumerate(pdata.get("tasks", [])):
            for dep_idx in tdata.get("depends_on", []):
                dep = models.EntityDependency(
                    entity_type="task",
                    entity_id=task_index_map[t_idx].id,
                    prerequisite_type="task",
                    prerequisite_id=task_index_map[dep_idx].id,
                )
                db.session.add(dep)

        project_index_map[p_idx] = project

# --- Persist project-level dependencies (after ALL projects created) ---
for p_idx, pdata in enumerate(plan.get("problems", [])):
    for dep_idx in pdata.get("depends_on", []):
        dep = models.EntityDependency(
            entity_type="project",
            entity_id=project_index_map[p_idx].id,
            prerequisite_type="project",
            prerequisite_id=project_index_map[dep_idx].id,
        )
        db.session.add(dep)
```

Also add `project_index_map = {}` before the project loop (~line 81) and
`project_index_map[p_idx] = project` after `db.session.flush()` in the
project creation (~line 96).

### Step 10 — Update `/api/tasks` endpoint in [`app.py`](app.py)

Line 730: add filter clause:

```python
tasks = (
    models.Task.query
    .filter_by(is_deleted=False)
    .filter(models.Task.is_blocked == False)
    .order_by(models.Task.priority, models.Task.created)
    .all()
)
```

### Step 11 — Update `home()` route in [`app.py`](app.py)

Line 145: add filter to workflow_tasks query:

```python
workflow_tasks = (
    models.Task.query
    .filter_by(is_deleted=False, is_completed=False, is_archived=False)
    .filter(models.Task.first_run.isnot(None))
    .filter(models.Task.is_blocked == False)
    .order_by(models.Task.first_run.desc())
    .limit(10)
    .all()
)
```

### Step 12 — Update [`templates/project.html`](templates/project.html)

**A) Add blocked-task CSS** (in the `<style>` block, line ~268 before `</style>`):

```css
.task-card.blocked {
    background: #f0f0f0;
    opacity: 0.65;
    border-color: #ccc;
}
.task-card.blocked .task-header:hover {
    background: #f0f0f0;
}
.blocked-badge {
    display: inline-block;
    padding: 2px 10px;
    border-radius: 10px;
    font-size: 10px;
    font-weight: bold;
    color: #fff;
    background: #adb5bd;
}
```

**B) Modify the task card rendering** (lines 390-451) — replace the task-header
block (lines 391-425) with:

```jinja2
<div class="task-card {% if task.is_blocked %}blocked{% endif %}">
    <div class="task-header">
        {% if task.is_blocked %}
            <span style="font-size:18px;flex-shrink:0;" title="Blocked — prerequisites incomplete">🔒</span>
        {% else %}
            <input type="checkbox" class="cb"
                   {% if task.is_completed %}checked{% endif %}
                   disabled title="Complete via timer tracking">
        {% endif %}
        <div class="info">
            <span class="task-id">#{{ task.id }}</span>
            <span class="badge badge-p{{ task.priority }}">P{{ task.priority }}</span>
            ⏱ {{ (task.estimated_time or 0) // 60 }} min
            {% if task.is_blocked %}<span class="blocked-badge">BLOCKED</span>{% endif %}
            {% if task.tags %}
                {% for t in task.tags %}
                    <span class="badge" style="background:#6c757d;font-weight:normal;font-size:10px;">{{ t.tag }}</span>
                {% endfor %}
            {% endif %}
            {% if task.total_time_spent %}
                <span style="font-size:11px;color:#888;">⌛ {{ (task.total_time_spent or 0) // 60 }}m tracked</span>
            {% endif %}
        </div>
        <div class="task-actions">
            {% if not task.is_blocked %}
                <button class="btn-sm btn-success" style="margin-right:4px;"
                        onclick="Timer.selectTask({{ task.id }}, null, 'Task #{{ task.id }}', {{ task.estimated_time or 0 }}); return false;"
                        title="Start timer for this task">▶</button>
            {% endif %}
            <!-- split and remove buttons unchanged -->
            <form action="/project/{{ project.id }}/task/{{ task.id }}/split"
                  method="post" style="display:inline;">
                <input type="text" name="hint" placeholder="Hint (optional)"
                       style="width:100px;padding:3px 6px;font-size:11px;border:1px solid #ccc;border-radius:3px;margin-right:4px;">
                <button type="submit" class="btn-sm btn-purple"
                        title="AI-powered split">⚡ Split</button>
            </form>
            <form action="/project/{{ project.id }}/task/{{ task.id }}/remove"
                  method="post" style="display:inline;"
                  onsubmit="return confirm('Remove task #{{ task.id }} and all its subtasks?');">
                <button type="submit" class="btn-sm btn-danger">✕ Remove</button>
            </form>
        </div>
    </div>
    <!-- subtasks section unchanged -->
```

### Step 13 — Update [`templates/home.html`](templates/home.html)

Read the file first, then make these changes:

**A)** Project cards: if `project.is_blocked`, render with 🔒 and `blocked` CSS class.

**B)** Workflow section: no template change needed — filtering is done in `home()` route
(Step 11). But you may want to add a `blocked` class to blocked project cards.

### Step 14 — Add `from sqlalchemy import select, exists, and_, or_` to imports

At the top of [`models.py`](models.py) (after line 15, alongside existing
`from datetime` import):

```python
from sqlalchemy import select, exists, and_, or_
```

### Step 15 — Manual dependency management (future proofing)

Add a dependency management section to [`project.html`](templates/project.html)
below the tasks section. This allows users to manually link task prerequisites:

```html
<div class="add-task-form" style="margin-top:20px;">
    <h4>🔗 Manage Task Dependencies</h4>
    <form action="/project/{{ project.id }}/deps/add" method="post">
        <label>Task:</label>
        <select name="task_id" required>
            {% for t in tasks %}
                <option value="{{ t.id }}">#{{ t.id }}</option>
            {% endfor %}
        </select>
        <label>Depends on:</label>
        <select name="prerequisite_id" required>
            {% for t in tasks %}
                <option value="{{ t.id }}">#{{ t.id }}</option>
            {% endfor %}
        </select>
        <button type="submit" class="btn-sm btn-primary">Add Dependency</button>
    </form>
    <form action="/project/{{ project.id }}/deps/remove" method="post" style="margin-top:8px;">
        <label>Remove dependency:</label>
        <select name="dep_id" required>
            {% for t in tasks %}
                {% for dep in t.blocked_by.all() %}
                    <option value="{{ dep.id }}">#{{ t.id }} depends on #{{ dep.prerequisite_id }}</option>
                {% endfor %}
            {% endfor %}
        </select>
        <button type="submit" class="btn-sm btn-danger">Remove</button>
    </form>
</div>
```

### Step 16 — Add dependency routes to [`app.py`](app.py)

```python
@app.route("/project/<int:project_id>/deps/add", methods=["POST"])
def project_deps_add(project_id):
    task_id = int(request.form.get("task_id", 0))
    prerequisite_id = int(request.form.get("prerequisite_id", 0))
    if task_id == prerequisite_id:
        flash("A task cannot depend on itself.", "error")
        return redirect(url_for("project_detail", project_id=project_id))

    dep = models.EntityDependency(
        entity_type="task",
        entity_id=task_id,
        prerequisite_type="task",
        prerequisite_id=prerequisite_id,
    )
    db.session.add(dep)
    db.session.commit()
    flash(f"Task #{task_id} now depends on #{prerequisite_id}.", "success")
    return redirect(url_for("project_detail", project_id=project_id))


@app.route("/project/<int:project_id>/deps/remove", methods=["POST"])
def project_deps_remove(project_id):
    dep_id = int(request.form.get("dep_id", 0))
    dep = models.EntityDependency.query.get(dep_id)
    if dep:
        db.session.delete(dep)
        db.session.commit()
        flash("Dependency removed.", "success")
    return redirect(url_for("project_detail", project_id=project_id))
```

---

## Files Changed — Summary

| File | Lines affected | Type of change |
|------|---------------|----------------|
| [`models.py`](models.py) | ~15, ~120, ~164 | Add import, EntityDependency class, Project.is_blocked, Task.is_blocked |
| `migrations/versions/XXXX.py` | new file | Auto-generated Alembic migration |
| [`utils.py`](utils.py) | 48-60, 69-95, ~170, ~187, 477-505, 605-633 | Prompt updates + `_validate_depends_on` + default setters |
| [`app.py`](app.py) | 71-131, 727-760, 139-158, ~1120 | `_save_plan_to_db`, `/api/tasks`, `home()`, new dep routes |
| [`templates/project.html`](templates/project.html) | ~268, 391-425, ~490 | CSS, task card rendering, dep management form |
| [`templates/home.html`](templates/home.html) | project card section | Blocked project indicators |

## Key Design Decisions

1. **Index-based references in LLM JSON** — avoids requiring the LLM to generate stable IDs.
   Indices are resolved to real DB IDs after persistence.
2. **Polymorphic dependency table** — one table handles all entity types, mirroring
   the existing `UserEntityRole` pattern.
3. **Hybrid properties** — `is_blocked` works both in Python (`task.is_blocked`) and
   in SQL filters (`.filter(Task.is_blocked == False)`).
4. **No subtask dependencies** — subtasks are atomic enough that sequential
   dependencies don't apply. The LLM should restructure into tasks if needed.
5. **Transitive blocking** — Task.is_blocked checks three layers: direct task
   prerequisites, project prerequisites on the task, and owner-project blocking.
