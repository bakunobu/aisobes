"""Flask application entry point — Organizer app with decompose flow."""

import json
import os
import time
from datetime import datetime, timezone

from flask import (
    Flask,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from flask_migrate import Migrate

from extensions import db
import utils
from werkzeug.security import generate_password_hash, check_password_hash

# Password hashing helpers
def hash_password(password: str) -> str:
    return generate_password_hash(password)

def verify_password(password: str, password_hash: str) -> bool:
    return check_password_hash(password_hash, password)

# ---------------------------------------------------------------------------
# Flask application factory
# ---------------------------------------------------------------------------

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-key-change-me")

# Database configuration
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///organizer.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["STATIC_FOLDER"] = "static"

# Bind db to this app
db.init_app(app)
migrate = Migrate(app, db)

# Import models so Alembic can detect them for autogenerate
import models  # noqa: E402,F401


# ===========================================================================
# Helpers
# ===========================================================================


def _format_elapsed(seconds: int) -> str:
    """Format seconds as H:MM:SS string (e.g. '0:42:15')."""
    if not seconds:
        return "0:00:00"
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h}:{m:02d}:{s:02d}"


def _get_or_create_tag(name: str) -> "models.Tag":
    """Return existing tag or create a new one."""
    name = name.strip().lower()
    if not name:
        return None
    tag = models.Tag.query.filter_by(tag=name).first()
    if tag is None:
        tag = models.Tag(tag=name)
        db.session.add(tag)
        db.session.flush()
    return tag


def _save_plan_to_db(plan: dict):
    """Persist a decomposed plan into the database.

    Creates Tag, Project, Task, Subtask rows with relationships.
    Also creates EntityDependency rows for project and task dependencies.
    """
    # 1. Shared tags
    for tag_name in plan.get("shared_tags", []):
        _get_or_create_tag(tag_name)

    # Store created projects and tasks for dependency mapping
    projects_db = []
    tasks_db = []

    # 2. Projects → Tasks → Subtasks
    for pidx, pdata in enumerate(plan.get("problems", [])):
        # Project-level tags
        proj_tags = []
        for tname in pdata.get("tags", []):
            tag = _get_or_create_tag(tname)
            if tag:
                proj_tags.append(tag)

        project = models.Project(
            description=pdata.get("description", "Untitled project"),
            estimated_time=(pdata.get("estimated_time", 0) or 0) * 60,
            priority=pdata.get("priority", 3),
        )
        project.tags = proj_tags
        db.session.add(project)
        db.session.flush()
        projects_db.append(project)

        project_tasks = []
        for tidx, tdata in enumerate(pdata.get("tasks", [])):
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
            project_tasks.append(task)
            tasks_db.append(task)

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

        # Create project dependencies
        for dep_idx in pdata.get("depends_on", []):
            if dep_idx < len(projects_db):
                dep = models.EntityDependency(
                    entity_type="project",
                    entity_id=project.id,
                    prerequisite_type="project",
                    prerequisite_id=projects_db[dep_idx].id
                )
                db.session.add(dep)

        # Create task dependencies
        for tidx, tdata in enumerate(pdata.get("tasks", [])):
            task = project_tasks[tidx]
            for dep_idx in tdata.get("depends_on", []):
                if dep_idx < len(project_tasks):
                    dep = models.EntityDependency(
                        entity_type="task",
                        entity_id=task.id,
                        prerequisite_type="task",
                        prerequisite_id=project_tasks[dep_idx].id
                    )
                    db.session.add(dep)

    db.session.commit()


# ===========================================================================
# Health-check
# ===========================================================================


@app.route("/")
def home():
    """Render the dashboard home page."""
    projects = models.Project.query.filter_by(
        is_deleted=False, is_completed=False, is_blocked=False
    ).order_by(
        models.Project.priority, models.Project.created.desc()
    ).all()
    workflow_tasks = (
        models.Task.query
        .filter_by(is_deleted=False, is_completed=False, is_archived=False, is_blocked=False)
        .filter(models.Task.first_run.isnot(None))
        .order_by(models.Task.first_run.desc())
        .limit(10)
        .all()
    )
    return render_template(
        "home.html",
        projects=projects,
        workflow_tasks=workflow_tasks,
        format_elapsed=_format_elapsed,
    )


# ===========================================================================
# Decompose flow — input → LLM → edit → approve / download
# ===========================================================================


@app.route("/decompose", methods=["GET", "POST"])
def decompose():
    """Page: enter an idea, see LLM-structured plan, edit inline.

    GET  — show the form.
    POST — call decompose_idea(), store result in session, render for editing.
    """
    plan = session.pop("plan", None)
    error = None
    plan_json = None
    idea = ""

    if request.method == "POST":
        project_id = request.form.get("project_id")
        if project_id:
            project = models.Project.query.get(project_id)
            if project:
                idea = project.description
            else:
                error = "Project not found."
        else:
            idea = request.form.get("problem") or request.form.get("idea", "")
            idea = idea.strip()

        if not idea:
            error = "Please enter an idea."
        else:
            try:
                plan = utils.decompose_idea(idea)
                session["plan"] = plan
                plan_json = json.dumps(plan, indent=2, ensure_ascii=False)
            except Exception as exc:
                error = str(exc)

    return render_template(
        "decompose.html",
        idea=idea,
        plan=plan,
        plan_json=plan_json,
        error=error,
    )


@app.route("/decompose/approve", methods=["POST"])
def decompose_approve():
    """Accept the edited JSON plan, persist to database."""
    raw_json = request.form.get("plan_json", "").strip()
    if not raw_json:
        flash("No plan data submitted.", "error")
        return redirect(url_for("decompose"))

    try:
        plan = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        flash(f"Invalid JSON: {exc}", "error")
        return redirect(url_for("decompose"))

    try:
        _save_plan_to_db(plan)
        flash("Plan saved to database.", "success")
        return redirect(url_for("home"))
    except Exception as exc:
        flash(f"Database error: {exc}", "error")
        return redirect(url_for("decompose"))


@app.route("/decompose/download")
def decompose_download():
    """Download the current plan as a JSON file."""
    plan = session.get("plan")
    if plan is None:
        flash("No plan to download. Generate one first.", "error")
        return redirect(url_for("decompose"))

    from io import BytesIO

    data = json.dumps(plan, indent=2, ensure_ascii=False)
    buf = BytesIO(data.encode("utf-8"))
    buf.seek(0)

    timestamp = time.strftime("%Y%m%d-%H%M%S")
    return send_file(
        buf,
        mimetype="application/json",
        as_attachment=True,
        download_name=f"plan_{timestamp}.json",
    )


# ===========================================================================
# Project detail page — view / edit / manage tasks
# ===========================================================================


@app.route("/project/<int:project_id>")
def project_detail(project_id):
    """Render the project detail page with task hierarchy."""
    project = models.Project.query.get_or_404(project_id)
    if project.is_routine:
        return redirect(url_for("project_routine", project_id=project_id))
    tasks = (
        models.Task.query
        .filter_by(project_id=project_id, is_deleted=False)
        .order_by(models.Task.priority, models.Task.created)
        .all()
    )
    return render_template("project.html", project=project, tasks=tasks)


# ===========================================================================
# Routine Project — manage routine tasks
# ===========================================================================


@app.route("/project/<int:project_id>/routine")
def project_routine(project_id):
    """Render the routine project page with create/edit form and task list."""
    project = models.Project.query.get_or_404(project_id)
    if not project.is_routine:
        return redirect(url_for("project_detail", project_id=project_id))
    routine_tasks = (
        models.RoutineTask.query
        .filter_by(project_id=project_id)
        .order_by(models.RoutineTask.created.desc())
        .all()
    )
    day_names = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    return render_template(
        "routine.html",
        project=project,
        routine_tasks=routine_tasks,
        day_names=day_names,
    )


@app.route("/project/<int:project_id>/routine/create", methods=["POST"])
def project_routine_create(project_id):
    """Create a new routine task."""
    project = models.Project.query.get_or_404(project_id)


# ===========================================================================
# Reminder Project — manage reminder tasks
# ===========================================================================


@app.route("/project/<int:project_id>/reminders")
def project_reminders(project_id):
    """Render the reminder project page with create/edit form and task list."""
    project = models.Project.query.get_or_404(project_id)
    if not project.is_routine:
        return redirect(url_for("project_detail", project_id=project_id))
    
    reminder_tasks = (
        models.ReminderTask.query
        .filter_by(project_id=project_id)
        .order_by(models.ReminderTask.due_datetime.desc())
        .all()
    )
    return render_template(
        "reminders.html",
        project=project,
        reminder_tasks=reminder_tasks,
    )
    if not project.is_routine:
        return redirect(url_for("project_detail", project_id=project_id))

    description = request.form.get("description", "").strip()
    if not description:
        flash("Description is required.", "error")
        return redirect(url_for("project_routine", project_id=project_id))

    selected_days = request.form.getlist("days_of_week")
    days_of_week = ",".join(selected_days) if selected_days else ""

    time_of_day = request.form.get("time_of_day", "09:00").strip()
    duration = int(request.form.get("duration", 30) or 30)
    start_date_str = request.form.get("start_date", "").strip()
    end_date_str = request.form.get("end_date", "").strip()

    start_date = (
        datetime.strptime(start_date_str, "%Y-%m-%d").date()
        if start_date_str else None
    )
    end_date = (
        datetime.strptime(end_date_str, "%Y-%m-%d").date()
        if end_date_str else None
    )

    routine_task = models.RoutineTask(
        project_id=project.id,
        description=description,
        days_of_week=days_of_week,
        time_of_day=time_of_day,
        duration=duration,
        start_date=start_date,
        end_date=end_date,
    )
    db.session.add(routine_task)
    db.session.commit()
    flash("Routine task created.", "success")
    return redirect(url_for("project_routine", project_id=project_id))


@app.route(
    "/project/<int:project_id>/routine/<int:task_id>/edit",
    methods=["POST"],
)
def project_routine_edit(project_id, task_id):
    """Edit an existing routine task."""
    project = models.Project.query.get_or_404(project_id)
    if not project.is_routine:
        return redirect(url_for("project_detail", project_id=project_id))

    routine_task = models.RoutineTask.query.filter_by(
        id=task_id, project_id=project_id
    ).first_or_404()

    description = request.form.get("description", "").strip()
    if not description:
        flash("Description is required.", "error")
        return redirect(url_for("project_routine", project_id=project_id))

    selected_days = request.form.getlist("days_of_week")
    days_of_week = ",".join(selected_days) if selected_days else ""

    routine_task.description = description
    routine_task.days_of_week = days_of_week
    routine_task.time_of_day = request.form.get("time_of_day", "09:00").strip()
    routine_task.duration = int(request.form.get("duration", 30) or 30)

    start_date_str = request.form.get("start_date", "").strip()
    end_date_str = request.form.get("end_date", "").strip()
    routine_task.start_date = (
        datetime.strptime(start_date_str, "%Y-%m-%d").date()
        if start_date_str else None
    )
    routine_task.end_date = (
        datetime.strptime(end_date_str, "%Y-%m-%d").date()
        if end_date_str else None
    )

    is_active = request.form.get("is_active")
    routine_task.is_active = is_active == "1"

    db.session.commit()
    flash("Routine task updated.", "success")
    return redirect(url_for("project_routine", project_id=project_id))


@app.route(
    "/project/<int:project_id>/routine/<int:task_id>/delete",
    methods=["POST"],
)
def project_routine_delete(project_id, task_id):
    """Delete a routine task."""
    project = models.Project.query.get_or_404(project_id)
    if not project.is_routine:
        return redirect(url_for("project_detail", project_id=project_id))

    routine_task = models.RoutineTask.query.filter_by(
        id=task_id, project_id=project_id
    ).first_or_404()
    db.session.delete(routine_task)
    db.session.commit()
    flash("Routine task deleted.", "success")
    return redirect(url_for("project_routine", project_id=project_id))


# ===========================================================================
# Reminder Project — manage reminder tasks
# ===========================================================================


@app.route("/project/<int:project_id>/reminders")
def project_reminders_list(project_id):
    """Render the reminder project page with create/edit form and task list."""
    project = models.Project.query.get_or_404(project_id)
    if not project.is_routine:
        return redirect(url_for("project_detail", project_id=project_id))
    
    reminder_tasks = (
        models.ReminderTask.query
        .filter_by(project_id=project_id)
        .order_by(models.ReminderTask.due_datetime.desc())
        .all()
    )
    return render_template(
        "reminders.html",
        project=project,
        reminder_tasks=reminder_tasks,
    )


@app.route("/project/<int:project_id>/reminders/create", methods=["POST"])
def project_reminder_create(project_id):
    """Create a new reminder task."""
    project = models.Project.query.get_or_404(project_id)
    if not project.is_routine:
        return redirect(url_for("project_detail", project_id=project_id))
    
    description = request.form.get("description", "").strip()
    due_datetime_str = request.form.get("due_datetime")
    duration = request.form.get("duration", 30)
    
    if not description or not due_datetime_str:
        flash("Description and due date/time are required", "error")
        return redirect(url_for("project_reminders", project_id=project_id))
    
    try:
        due_datetime = datetime.strptime(due_datetime_str, "%Y-%m-%dT%H:%M")
    except ValueError:
        flash("Invalid date/time format", "error")
        return redirect(url_for("project_reminders", project_id=project_id))
    
    reminder = models.ReminderTask(
        project_id=project_id,
        description=description,
        due_datetime=due_datetime,
        duration=int(duration),
        is_completed=False,
        is_active=True
    )
    
    db.session.add(reminder)
    db.session.commit()
    
    flash("Reminder created successfully", "success")
    return redirect(url_for("project_reminders", project_id=project_id))


@app.route("/project/<int:project_id>/reminders/<int:task_id>/delete", methods=["POST"])
def project_reminder_delete(project_id, task_id):
    """Delete a reminder task."""
    reminder = models.ReminderTask.query.get_or_404(task_id)
    if reminder.project_id != project_id:
        abort(404)
    
    db.session.delete(reminder)
    db.session.commit()
    
    flash("Reminder deleted", "success")
    return redirect(url_for("project_reminders", project_id=project_id))


@app.route("/project/<int:project_id>/reminders/<int:task_id>/toggle", methods=["POST"])
def project_reminder_toggle(project_id, task_id):
    """Toggle completion status of a reminder task."""
    reminder = models.ReminderTask.query.get_or_404(task_id)
    if reminder.project_id != project_id:
        abort(404)
    
    reminder.is_completed = not reminder.is_completed
    db.session.commit()
    
    flash(f"Reminder marked as {'completed' if reminder.is_completed else 'pending'}", "success")
    return redirect(url_for("project_reminders", project_id=project_id))


# ===========================================================================
# Project detail — edit
# ===========================================================================


@app.route("/project/<int:project_id>/edit", methods=["POST"])
def project_edit(project_id):
    """Update project metadata."""
    project = models.Project.query.get_or_404(project_id)
    project.description = request.form.get("description", project.description)
    project.priority = int(request.form.get("priority", project.priority))
    estimated_time = request.form.get("estimated_time", "")
    if estimated_time:
        project.estimated_time = int(estimated_time) * 60  # minutes → seconds
    # Tags — replace
    tag_names = request.form.get("tags", "").strip()
    if tag_names:
        project.tags = []
        for name in tag_names.split(","):
            tag = _get_or_create_tag(name)
            if tag and tag not in project.tags:
                project.tags.append(tag)
    db.session.commit()
    flash("Project updated.", "success")
    return redirect(url_for("project_detail", project_id=project_id))


@app.route("/project/<int:project_id>/task/add", methods=["POST"])
def project_task_add(project_id):
    """Add a new task to the project."""
    project = models.Project.query.get_or_404(project_id)
    desc = request.form.get("description", "").strip()
    if not desc:
        flash("Task description required.", "error")
        return redirect(url_for("project_detail", project_id=project_id))

    task = models.Task(
        project_id=project.id,
        estimated_time=int(request.form.get("estimated_time", 0) or 0) * 60,
        priority=int(request.form.get("priority", 3)),
    )
    db.session.add(task)
    db.session.flush()

    # Create a ChangeLog entry
    log = models.ChangeLog(
        entity_type="task",
        entity_id=task.id,
        change_type="split",
        to_parent_id=project.id,
        description=f"Task created: {desc}",
    )
    db.session.add(log)
    db.session.commit()

    flash("Task added.", "success")
    return redirect(url_for("project_detail", project_id=project_id))


@app.route("/project/<int:project_id>/task/<int:task_id>/remove", methods=["POST"])
def project_task_remove(project_id, task_id):
    """Soft-delete a task."""
    task = models.Task.query.filter_by(id=task_id, project_id=project_id).first_or_404()
    task.is_deleted = True
    # Also soft-delete subtasks
    for sub in task.subtasks:
        sub.is_deleted = True
    log = models.ChangeLog(
        entity_type="task",
        entity_id=task.id,
        change_type="delete",
        description="Task removed",
    )
    db.session.add(log)
    db.session.commit()
    flash("Task removed.", "success")
    return redirect(url_for("project_detail", project_id=project_id))


@app.route("/project/<int:project_id>/task/<int:task_id>/split", methods=["POST"])
def project_task_split(project_id, task_id):
    """AI-powered split: replace a task with multiple finer tasks."""
    task = models.Task.query.filter_by(id=task_id, project_id=project_id).first_or_404()
    hint = request.form.get("hint", "").strip()

    try:
        split_tasks = utils.split_task(
            task_id=task_id,
            description=f"Task in project #{project_id}",
            hint=hint,
        )
    except Exception as exc:
        flash(f"Split failed: {exc}", "error")
        return redirect(url_for("project_detail", project_id=project_id))

    # Soft-delete original task
    task.is_deleted = True
    for sub in task.subtasks:
        sub.is_deleted = True

    # Create new tasks from split result
    for tdata in split_tasks:
        new_task = models.Task(
            project_id=project_id,
            estimated_time=(tdata.get("estimated_time", 0) or 0) * 60,
            priority=tdata.get("priority", 3),
        )
        db.session.add(new_task)
        db.session.flush()

        # Tags for new task
        for tname in tdata.get("tags", []):
            tag = _get_or_create_tag(tname)
            if tag:
                new_task.tags.append(tag)

        # Subtasks for new task
        for sdata in tdata.get("subtasks", []):
            subtask = models.Subtask(
                task_id=new_task.id,
                estimated_time=(sdata.get("estimated_time", 0) or 0) * 60,
                priority=sdata.get("priority", 3),
            )
            db.session.add(subtask)
            db.session.flush()
            for tname in sdata.get("tags", []):
                tag = _get_or_create_tag(tname)
                if tag:
                    subtask.tags.append(tag)

    log = models.ChangeLog(
        entity_type="task",
        entity_id=task.id,
        change_type="split",
        to_parent_id=project_id,
        description=f"Task #{task.id} split into {len(split_tasks)} tasks",
    )
    db.session.add(log)
    db.session.commit()

    flash(f"Task split into {len(split_tasks)} new tasks.", "success")
    return redirect(url_for("project_detail", project_id=project_id))


@app.route("/project/<int:project_id>/merge", methods=["POST"])
def project_merge(project_id):
    """AI-powered merge: find similar tasks in the project and suggest merges."""
    project = models.Project.query.get_or_404(project_id)
    tasks = (
        models.Task.query
        .filter_by(project_id=project_id, is_deleted=False)
        .all()
    )
    if len(tasks) < 2:
        flash("Need at least 2 tasks to merge.", "error")
        return redirect(url_for("project_detail", project_id=project_id))

    try:
        suggestions = utils.suggest_merge_tasks(tasks)
    except Exception as exc:
        flash(f"Merge analysis failed: {exc}", "error")
        return redirect(url_for("project_detail", project_id=project_id))

    # If user confirmed a specific merge pair
    merge_a = request.form.get("merge_a")
    merge_b = request.form.get("merge_b")
    if merge_a and merge_b:
        task_a = models.Task.query.get(int(merge_a))
        task_b = models.Task.query.get(int(merge_b))
        if task_a and task_b and task_a.project_id == project_id and task_b.project_id == project_id:
            # Move subtasks from B to A
            for sub in task_b.subtasks:
                if not sub.is_deleted:
                    sub.task_id = task_a.id
            # Combine estimated times
            task_a.estimated_time = (task_a.estimated_time or 0) + (task_b.estimated_time or 0)
            # Soft-delete B
            task_b.is_deleted = True
            log = models.ChangeLog(
                entity_type="task",
                entity_id=task_a.id,
                change_type="merge",
                description=f"Merged task #{task_b.id} into #{task_a.id}",
            )
            db.session.add(log)
            db.session.commit()
            flash("Tasks merged successfully.", "success")
            return redirect(url_for("project_detail", project_id=project_id))

    return render_template(
        "project.html",
        project=project,
        tasks=tasks,
        merge_suggestions=suggestions,
    )


# ===========================================================================
# Timer API endpoints
# ===========================================================================


@app.route("/api/tasks")
def api_tasks():
    """Return all non-deleted, non-blocked tasks with their subtasks."""
    tasks = (
        models.Task.query
        .filter_by(is_deleted=False, is_completed=False, is_blocked=False)
        .order_by(models.Task.priority, models.Task.created)
        .all()
    )
    result = []
    for task in tasks:
        # Since we are not fixing the description issue, we use the project description
        project = models.Project.query.get(task.project_id)
        task_description = project.description if project else "Untitled Task"

        subs = (
            models.Subtask.query
            .filter_by(task_id=task.id, is_deleted=False, is_completed=False)
            .order_by(models.Subtask.priority, models.Subtask.created)
            .all()
        )
        result.append({
            "id": task.id,
            "description": task_description,
            "project_id": task.project_id,
            "project_name": project.description if project else "Untitled Project",
            "estimated_time": task.estimated_time or 0,
            "is_blocked": task.is_blocked,
            "subtasks": [
                {
                    "id": s.id,
                    "description": task_description, # Using task description for subtask
                    "estimated_time": s.estimated_time or 0,
                    "is_blocked": task.is_blocked,  # Subtask is blocked if parent is
                }
                for s in subs
            ],
        })
    return {"success": True, "tasks": result}


@app.route("/api/timer/start", methods=["POST"])
def api_timer_start():
    """Start a timer session for a task or subtask."""
    data = request.get_json()
    task_id = data.get("task_id")
    subtask_id = data.get("subtask_id")
    planned_duration = data.get("planned_duration", 0)

    # Pause any currently active sessions
    active_sessions = models.TimerSession.query.filter_by(status="active").all()
    for s in active_sessions:
        s.status = "paused"

    session = models.TimerSession(
        task_id=task_id,
        subtask_id=subtask_id,
        planned_duration=planned_duration,
        status="active",
        start_time=datetime.now(timezone.utc),
    )
    db.session.add(session)

    if task_id:
        task = models.Task.query.get(task_id)
        if task and task.first_run is None:
            task.first_run = datetime.now(timezone.utc)
    if subtask_id:
        subtask = models.Subtask.query.get(subtask_id)
        if subtask and subtask.first_run is None:
            subtask.first_run = datetime.now(timezone.utc)

    db.session.commit()
    return {"success": True, "session_id": session.id}


@app.route("/api/timer/pause", methods=["PUT"])
def api_timer_pause():
    """Pause a running timer session."""
    data = request.get_json()
    session_id = data.get("session_id")
    session = models.TimerSession.query.get(session_id)
    if not session:
        return {"success": False, "error": "Session not found"}, 404
    elapsed = int((datetime.now(timezone.utc) - session.start_time).total_seconds())
    session.actual_duration = elapsed
    session.status = "paused"
    db.session.commit()
    return {"success": True, "actual_duration": session.actual_duration}


@app.route("/api/timer/stop", methods=["PUT"])
def api_timer_stop():
    """Stop a timer session and accumulate time on the Task/Subtask."""
    data = request.get_json()
    session_id = data.get("session_id")
    session = models.TimerSession.query.get(session_id)
    if not session:
        return {"success": False, "error": "Session not found"}, 404
    elapsed = int((datetime.now(timezone.utc) - session.start_time).total_seconds())
    session.actual_duration = elapsed
    session.end_time = datetime.now(timezone.utc)
    session.status = "stopped"

    if session.task_id:
        task = models.Task.query.get(session.task_id)
        if task:
            task.total_time_spent = (task.total_time_spent or 0) + elapsed
    if session.subtask_id:
        subtask = models.Subtask.query.get(session.subtask_id)
        if subtask:
            subtask.total_time_spent = (subtask.total_time_spent or 0) + elapsed
    db.session.commit()
    return {"success": True, "actual_duration": session.actual_duration}


@app.route("/api/timer/complete", methods=["PUT"])
def api_timer_complete():
    """Mark a timer session as completed (timer reached zero)."""
    data = request.get_json()
    session_id = data.get("session_id")
    session = models.TimerSession.query.get(session_id)
    if not session:
        return {"success": False, "error": "Session not found"}, 404
    elapsed = int((datetime.now(timezone.utc) - session.start_time).total_seconds())
    session.actual_duration = elapsed
    session.end_time = datetime.now(timezone.utc)
    session.status = "completed"

    if session.task_id:
        task = models.Task.query.get(session.task_id)
        if task:
            task.total_time_spent = (task.total_time_spent or 0) + elapsed
    if session.subtask_id:
        subtask = models.Subtask.query.get(session.subtask_id)
        if subtask:
            subtask.total_time_spent = (subtask.total_time_spent or 0) + elapsed
    db.session.commit()
    return {"success": True, "actual_duration": session.actual_duration}


@app.route("/api/daily-stats")
def api_daily_stats():
    """Return today's timer statistics."""
    today = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    sessions_today = models.TimerSession.query.filter(
        models.TimerSession.created >= today,
        models.TimerSession.status.in_(["completed", "stopped"]),
    ).all()

    finished = sum(1 for s in sessions_today if s.status == "completed")
    total_time = sum(s.actual_duration or 0 for s in sessions_today)
    total_sessions = len(sessions_today)

    if total_sessions == 0:
        mood = "😴"
    elif finished >= total_sessions:
        mood = "🔥"
    elif finished / total_sessions >= 0.5:
        mood = "😊"
    else:
        mood = "😤"

    hours = total_time // 3600
    minutes = (total_time % 3600) // 60
    formatted = f"{hours}h {minutes}m" if hours > 0 else f"{minutes}m" if minutes > 0 else "0m"

    completion_pct = (
        round(finished / total_sessions * 100) if total_sessions > 0 else 0
    )

    return {
        "success": True,
        "stats": {
            "finished_tasks": finished,
            "total_sessions": total_sessions,
            "total_time_seconds": total_time,
            "total_time_formatted": formatted,
            "completion_percentage": completion_pct,
            "mood_emoji": mood,
        },
    }


@app.route("/api/timer/stats")
def api_timer_stats():
    """Return per-task timer statistics."""
    task_name = request.args.get("task")
    sessions = models.TimerSession.query.all()
    stats_map = {}
    for s in sessions:
        key = s.task_id or s.subtask_id
        if key not in stats_map:
            stats_map[key] = {
                "task_id": s.task_id,
                "subtask_id": s.subtask_id,
                "task_name": f"{'subtask' if s.subtask_id else 'task'} #{key}",
                "total_seconds_spent": 0,
                "total_attempts": 0,
                "successful_runs": 0,
                "success_rate": 0,
            }
        stats_map[key]["total_seconds_spent"] += s.actual_duration or 0
        stats_map[key]["total_attempts"] += 1
        if s.status == "completed":
            stats_map[key]["successful_runs"] += 1
    for stat in stats_map.values():
        if stat["total_attempts"] > 0:
            stat["success_rate"] = round(
                stat["successful_runs"] / stat["total_attempts"] * 100
            )
    return {"success": True, "stats": list(stats_map.values())}


# ===========================================================================
# Quest endpoints
# ===========================================================================


def _compute_quest_progress(quest: models.Quest) -> tuple[int, int, str]:
    """Return (current_value, goal_value, status_str)."""
    if quest.quest_type == 'daily_tasks':
        # Count tasks completed today matching target
        today = datetime.now(timezone.utc).date()
        tasks_done = models.Task.query.filter_by(
            is_completed=True,
            is_deleted=False
        ).filter(
            models.Task.created >= today
        ).count()
        return (tasks_done, quest.goal_value, f"{tasks_done}/{quest.goal_value} tasks today")

    elif quest.quest_type == 'daily_time':
        today = datetime.now(timezone.utc).date()
        sessions = models.TimerSession.query.filter(
            models.TimerSession.created >= today,
            models.TimerSession.status.in_(["completed", "stopped"])
        ).all()
        seconds = sum(s.actual_duration or 0 for s in sessions)
        return (seconds, quest.goal_value, f"{_format_elapsed(seconds)} / {_format_elapsed(quest.goal_value)} today")

    elif quest.quest_type == 'total_time':
        total = 0  # TODO: Implement cumulative time tracking
        return (total, quest.goal_value, f"{_format_elapsed(total)} / {_format_elapsed(quest.goal_value)} total")

    elif quest.quest_type == 'complete_task':
        task = models.Task.query.get(quest.target_id)
        done = 1 if task and task.is_completed else 0
        return (done, 1, "✓ Completed" if done else "○ Pending")


@app.route("/quests", methods=["GET"])
def quests_list():
    """Main quests page: list all + create form."""
    quests = models.Quest.query.order_by(models.Quest.created.desc()).all()
    return render_template("quests.html", quests=quests)


@app.route("/quests/create", methods=["POST"])
def quest_create():
    """Create a new quest."""
    name = request.form.get("name")
    quest_type = request.form.get("quest_type")
    target_type = request.form.get("target_type")
    goal_value = int(request.form.get("goal_value", 0))
    award_type = request.form.get("award_type")
    award_description = request.form.get("award_description", "")
    
    if not name or not quest_type:
        flash("Name and quest type are required", "error")
        return redirect(url_for("quests_list"))
    
    quest = models.Quest(
        name=name,
        quest_type=quest_type,
        target_type=target_type,
        goal_value=goal_value,
        award_type=award_type,
        award_description=award_description,
        is_active=True
    )
    
    # Handle target references
    if target_type == "project":
        quest.target_id = int(request.form.get("target_project_id"))
    elif target_type == "task":
        quest.target_id = int(request.form.get("target_task_id"))
    elif target_type == "subtask":
        quest.target_id = int(request.form.get("target_subtask_id"))
    elif target_type == "tag":
        quest.target_tag = request.form.get("target_tag")
    
    db.session.add(quest)
    db.session.commit()
    flash("Quest created successfully", "success")
    return redirect(url_for("quests_list"))


@app.route("/quests/<int:quest_id>/edit", methods=["POST"])
def quest_edit(quest_id):
    """Edit an existing quest."""
    quest = models.Quest.query.get_or_404(quest_id)
    quest.name = request.form.get("name", quest.name)
    quest.quest_type = request.form.get("quest_type", quest.quest_type)
    quest.target_type = request.form.get("target_type", quest.target_type)
    quest.goal_value = int(request.form.get("goal_value", quest.goal_value))
    quest.award_type = request.form.get("award_type", quest.award_type)
    quest.award_description = request.form.get("award_description", quest.award_description)
    quest.is_active = bool(request.form.get("is_active"))
    
    # Handle target references
    if quest.target_type == "project":
        quest.target_id = int(request.form.get("target_project_id"))
    elif quest.target_type == "task":
        quest.target_id = int(request.form.get("target_task_id"))
    elif quest.target_type == "subtask":
        quest.target_id = int(request.form.get("target_subtask_id"))
    elif quest.target_type == "tag":
        quest.target_tag = request.form.get("target_tag")
    
    db.session.commit()
    flash("Quest updated successfully", "success")
    return redirect(url_for("quests_list"))


@app.route("/quests/<int:quest_id>/delete", methods=["POST"])
def quest_delete(quest_id):
    """Delete a quest and its logs."""
    quest = models.Quest.query.get_or_404(quest_id)
    
    # Delete associated logs
    models.QuestLog.query.filter_by(quest_id=quest_id).delete()
    
    db.session.delete(quest)
    db.session.commit()
    flash("Quest deleted", "success")
    return redirect(url_for("quests_list"))


@app.route("/quests/<int:quest_id>/toggle", methods=["POST"])
def quest_toggle(quest_id):
    """Toggle quest active status."""
    quest = models.Quest.query.get_or_404(quest_id)
    quest.is_active = not quest.is_active
    db.session.commit()
    status = "active" if quest.is_active else "inactive"
    flash(f"Quest marked as {status}", "success")
    return redirect(url_for("quests_list"))


@app.route("/api/session/create", methods=["POST"])
def api_session_create():
    """Create a new session plan."""
    data = request.get_json()
    
    # Validate input
    try:
        duration = int(data.get("duration", 60))
        intensity = data.get("intensity", "medium")
        focus_project_id = data.get("focus_project_id")
        diversity = data.get("diversity", "same")
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid input"}), 400
    
    # Get focus project name if exists
    focus_project = None
    if focus_project_id:
        project = models.Project.query.get(focus_project_id)
        focus_project = project.description if project else None
    
    # Generate plan
    try:
        plan = utils.generate_session_plan(
            duration=duration,
            intensity=intensity,
            focus_project=focus_project,
            diversity=diversity
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    
    # Save to DB
    session_plan = models.SessionPlan(
        duration=duration,
        intensity=intensity,
        focus_project_id=focus_project_id,
        diversity=diversity,
        plan_json=json.dumps(plan)
    )
    db.session.add(session_plan)
    db.session.commit()
    
    return jsonify({
        "session_id": session_plan.id,
        "tasks": plan
    })


@app.route("/api/session/<int:session_id>")
def api_session_get(session_id):
    """Get a session plan."""
    session_plan = models.SessionPlan.query.get_or_404(session_id)
    return jsonify({
        "id": session_plan.id,
        "duration": session_plan.duration,
        "intensity": session_plan.intensity,
        "focus_project_id": session_plan.focus_project_id,
        "diversity": session_plan.diversity,
        "tasks": json.loads(session_plan.plan_json)
    })


@app.route("/quests/<int:quest_id>/check", methods=["POST"])
def quest_check(quest_id):
    """Check progress and log results."""
    quest = models.Quest.query.get_or_404(quest_id)
    current, goal, status_str = _compute_quest_progress(quest)
    
    # Create log entry
    log = models.QuestLog(
        quest_id=quest.id,
        event_type="progress",
        value=current,
        description=f"Progress: {status_str}"
    )
    db.session.add(log)
    
    # Check if quest completed
    if current >= goal:
        completed_log = models.QuestLog(
            quest_id=quest.id,
            event_type="completed",
            value=current,
            description=f"Quest completed! {status_str}"
        )
        db.session.add(completed_log)
        quest.is_active = False
    
    db.session.commit()
    flash(f"Progress checked: {status_str}", "success")
    return redirect(url_for("quests_list"))


# ===========================================================================
# Project list page (redirects home)
# ===========================================================================


@app.route("/projects")
def project_list():
    """Alias — redirect to home (dashboard)."""
    return redirect(url_for("home"))

# ---------------------------------------------------------------------------
# User Authentication
# ---------------------------------------------------------------------------

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
@app.route("/user", methods=["GET"])
def user_page():
    """Render the combined login/register page."""
    user_id = session.get("user_id")
    user = None
    if user_id:
        user = models.User.query.get(user_id)
    return render_template("user.html", user=user)

@app.route("/register", methods=["POST"])
def register():
    """Handle new user registration."""
    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip()
    password = request.form.get("password", "")
    confirm_password = request.form.get("confirm_password", "")

    # Validate inputs
    if not name or not email or not password:
        flash("All fields are required", "error")
        return redirect(url_for("user_page"))
    if password != confirm_password:
        flash("Passwords do not match", "error")
        return redirect(url_for("user_page"))
    if len(password) < 6:
        flash("Password must be at least 6 characters", "error")
        return redirect(url_for("user_page"))

    # Check if email exists
    if models.User.query.filter_by(email=email).first():
        flash("Email already registered", "error")
        return redirect(url_for("user_page"))

    # Create user
    user = models.User(
        name=name,
        email=email,
        password_hash=hash_password(password),
        role="user",
        is_active=True
    )
    db.session.add(user)
    db.session.commit()

    # Log in user
    session["user_id"] = user.id
    flash(f"Welcome {name}! Account created successfully", "success")
    return redirect(url_for("home"))

@app.route("/login", methods=["POST"])
def login():
    """Handle user login."""
    email = request.form.get("email", "").strip()
    password = request.form.get("password", "")

    user = models.User.query.filter_by(email=email).first()

    if not user or not verify_password(password, user.password_hash):
        flash("Invalid email or password", "error")
        return redirect(url_for("user_page"))

    # Log in user
    session["user_id"] = user.id
    user.last_login = datetime.now(timezone.utc)
    db.session.commit()
    
    flash(f"Welcome back {user.name}!", "success")
    return redirect(url_for("home"))

@app.route("/logout")
def logout():
    """Handle user logout."""
    session.pop("user_id", None)
    flash("You have been logged out", "success")
    return redirect(url_for("home"))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

@app.route("/dependencies")
def dependencies():
    """Page to manage dependencies between projects and tasks."""
    projects = models.Project.query.filter_by(is_deleted=False).order_by(models.Project.created.desc()).all()
    tasks = models.Task.query.filter_by(is_deleted=False).order_by(models.Task.created.desc()).all()
    dependencies = models.EntityDependency.query.all()
    return render_template(
        "dependencies.html",
        projects=projects,
        tasks=tasks,
        dependencies=dependencies
    )



if __name__ == "__main__":
    app.run(debug=True, port=5050)
