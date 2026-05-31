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

    Creates Tag, Problem, Task, Subtask rows with relationships.
    """
    # 1. Shared tags
    for tag_name in plan.get("shared_tags", []):
        _get_or_create_tag(tag_name)

    # 2. Problems → Tasks → Subtasks
    for pdata in plan.get("problems", []):
        # Problem-level tags
        prob_tags = []
        for tname in pdata.get("tags", []):
            tag = _get_or_create_tag(tname)
            if tag:
                prob_tags.append(tag)

        problem = models.Problem(
            description=pdata.get("description", "Untitled problem"),
            estimated_time=(pdata.get("estimated_time", 0) or 0) * 60,
            priority=pdata.get("priority", 3),
        )
        problem.tags = prob_tags
        db.session.add(problem)
        db.session.flush()

        for tdata in pdata.get("tasks", []):
            # Task-level tags
            task_tags = []
            for tname in tdata.get("tags", []):
                tag = _get_or_create_tag(tname)
                if tag:
                    task_tags.append(tag)

            task = models.Task(
                problem_id=problem.id,
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
    return render_template("home.html")


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
        problem_id = request.form.get("problem_id")
        if problem_id:
            problem = models.Problem.query.get(problem_id)
            if problem:
                idea = problem.description
            else:
                error = "Problem not found."
        else:
            idea = request.form.get("problem") or request.form.get("idea", "")
            idea = idea.strip()

        if not idea:
            error = "Please enter a problem."
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

        # Keep plan in session so user can still download
        session["plan"] = plan
    except Exception as exc:
        flash(f"Database error: {exc}", "error")

    plan_json = json.dumps(plan, indent=2, ensure_ascii=False)
    return render_template(
        "decompose.html",
        idea="",
        plan=plan,
        plan_json=plan_json,
    )


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


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app.run(debug=True)
