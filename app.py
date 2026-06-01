"""Flask application entry point — Organizer app with decompose flow."""

import json
import os
import time

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
from utils import decompose_idea

# ---------------------------------------------------------------------------
# Flask application factory
# ---------------------------------------------------------------------------

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-key-change-me")

# Database configuration
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///organizer.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# Bind db to this app
db.init_app(app)
migrate = Migrate(app, db)

# Import models so Alembic can detect them for autogenerate
import models  # noqa: E402,F401


# ===========================================================================
# Helpers
# ===========================================================================


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
    return render_template("home.html", projects=projects)


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
                plan = decompose_idea(idea)
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
