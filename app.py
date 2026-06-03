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
    """
    # 1. Shared tags
    for tag_name in plan.get("shared_tags", []):
        _get_or_create_tag(tag_name)

    # 2. Projects → Tasks → Subtasks
    for pdata in plan.get("problems", []):
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

        for tdata in pdata.get("tasks", []):
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

    db.session.commit()


# ===========================================================================
# Health-check
# ===========================================================================


@app.route("/")
def home():
    """Render the dashboard home page."""
    projects = models.Project.query.filter_by(is_deleted=False).order_by(
        models.Project.created.desc()
    ).all()
    in_progress_tasks = (
        models.Task.query
        .filter_by(is_deleted=False, is_completed=False, is_archived=False)
        .filter(models.Task.first_run.isnot(None))
        .order_by(models.Task.first_run.desc())
        .limit(10)
        .all()
    )
    return render_template(
        "home.html",
        projects=projects,
        in_progress_tasks=in_progress_tasks,
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
    tasks = (
        models.Task.query
        .filter_by(project_id=project_id, is_deleted=False)
        .order_by(models.Task.priority, models.Task.created)
        .all()
    )
    return render_template("project.html", project=project, tasks=tasks)


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
    """Return all non-deleted tasks with their subtasks for the timer dropdown."""
    tasks = (
        models.Task.query
        .filter_by(is_deleted=False)
        .order_by(models.Task.priority, models.Task.created)
        .all()
    )
    result = []
    for task in tasks:
        project = models.Project.query.get(task.project_id)
        subs = (
            models.Subtask.query
            .filter_by(task_id=task.id, is_deleted=False)
            .order_by(models.Subtask.priority, models.Subtask.created)
            .all()
        )
        result.append({
            "id": task.id,
            "description": project.description if project else "Untitled",
            "project_id": task.project_id,
            "project_name": project.description if project else "Untitled",
            "estimated_time": task.estimated_time or 0,
            "subtasks": [
                {
                    "id": s.id,
                    "description": project.description if project else "Untitled",
                    "estimated_time": s.estimated_time or 0,
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
# Project list page (redirects home)
# ===========================================================================


@app.route("/projects")
def project_list():
    """Alias — redirect to home (dashboard)."""
    return redirect(url_for("home"))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app.run(debug=True)
