import json
import os
import time

import requests
from dotenv import dotenv_values
from flask import (
    Flask,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)

from utils import generate_todo

app = Flask(__name__)

# Load config
config = dotenv_values(".env")

# Validate required config
required_keys = ["OPENROUTER_API_URL", "OPENROUTER_API_KEY", "OPENROUTER_API_MODEL"]
for key in required_keys:
    if key not in config or not config[key]:
        raise ValueError(f"Missing required config: {key}")

# Global status tracker
status_log = []

# Holds the currently active todo (generated or uploaded)
current_todo: dict | None = None
current_todo_path: str | None = None  # file path if loaded from disk


def log_status(message):
    """Add status message with timestamp"""
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    status_log.append(f"[{timestamp}] {message}")
    if len(status_log) > 10:  # Keep last 10 messages
        status_log.pop(0)


def _navigate(data: dict, path: list[int]):
    """Return the dict at *path* inside the todo tree.  ``[]`` returns root."""
    node = data
    for idx in path[:-1]:
        node = node["tasks"][idx]
    return node


def _can_mark_done(data: dict) -> bool:
    """Check whether *data* (root/task) can be marked 'done'.

    A parent cannot be done while any child is still 'in_progress'.
    """
    # Check root-level children (tasks)
    for task in data.get("tasks", []):
        if task.get("status") != "done":
            return False
    # Check task-level children (subtasks)
    for sub in data.get("subtasks", []):
        if sub.get("status") != "done":
            return False
    return True


@app.route("/", methods=["GET"])
def home():
    return render_template("index.html", status_log=status_log)


@app.route("/ask", methods=["GET"])
def ask():
    return render_template("ask.html", status_log=status_log)


@app.route("/chat", methods=["POST"])
def chat():
    user_message = request.form["message"]
    log_status(f"Received message: {user_message[:20]}...")

    try:
        log_status("Sending to API...")
        response = requests.post(
            url=config["OPENROUTER_API_URL"],
            headers={
                "Authorization": f"Bearer {config['OPENROUTER_API_KEY']}",
            },
            json={
                "model": config["OPENROUTER_API_MODEL"],
                "messages": [{"role": "user", "content": user_message}],
            },
            timeout=30,
        )
        response.raise_for_status()

        data = response.json()
        assistant_message = data["choices"][0]["message"]["content"]
        log_status(f"Received response: {assistant_message[:20]}...")

        return render_template(
            "ask.html",
            status_log=status_log,
            response=assistant_message,
            user_message=user_message,
        )

    except Exception as e:
        log_status(f"Error: {str(e)}")
        return render_template("ask.html", status_log=status_log, error=str(e))


@app.route("/save", methods=["POST"])
def save():
    user_message = request.form["user_message"]
    assistant_message = request.form["assistant_message"]

    timestamp = time.strftime("%Y%m%d-%H%M%S")
    filename = f"./llm_responses/response_{timestamp}.json"

    data = {
        "user_message": user_message,
        "assistant_message": assistant_message,
        "timestamp": timestamp,
    }

    with open(filename, "w") as f:
        json.dump(data, f, indent=2)

    log_status(f"Saved response to {filename}")
    return redirect(url_for("ask"))


@app.route("/todo", methods=["GET", "POST"])
def todo():
    global current_todo, current_todo_path
    error = None

    if request.method == "POST":
        # --- Upload previously generated JSON ---
        if "upload" in request.form:
            file = request.files.get("file")
            if file and file.filename:
                try:
                    data = json.loads(file.read().decode("utf-8"))
                    current_todo = data
                    current_todo_path = None
                    log_status(f"Loaded todo from upload: {file.filename}")
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    error = f"Invalid JSON file: {exc}"
                    log_status(f"Upload error: {exc}")
            else:
                error = "Please select a JSON file to upload."

        # --- Generate from idea ---
        elif "idea" in request.form:
            idea = request.form.get("idea", "").strip()
            if not idea:
                error = "Please enter an idea or problem statement."
            else:
                log_status(f"Generating todo for: {idea[:40]}...")
                try:
                    current_todo = generate_todo(idea)
                    current_todo_path = current_todo.get("_saved_to")
                    log_status("Todo generated successfully")
                except Exception as exc:
                    log_status(f"Todo generation failed: {exc}")
                    error = str(exc)

    return render_template(
        "todo.html",
        status_log=status_log,
        todo_data=current_todo,
        error=error,
        saved_path=current_todo_path
        or (current_todo.get("_saved_to") if current_todo else None),
    )


@app.route("/todo/toggle", methods=["POST"])
def todo_toggle():
    """Toggle status of a task/subtask and persist the change."""
    global current_todo, current_todo_path
    if current_todo is None:
        return jsonify({"ok": False, "error": "No active todo"}), 400

    payload = request.get_json(silent=True) or {}
    path = payload.get("path", [])  # list of int indices, e.g. [0] or [0, 1]
    new_status = payload.get("status", "done")

    # Navigate to the target node
    node = current_todo
    for idx in path[:-1]:
        if idx >= len(node.get("tasks", [])):
            return jsonify({"ok": False, "error": "Invalid path"}), 400
        node = node["tasks"][idx]

    leaf_idx = path[-1] if path else -1
    if leaf_idx < 0 or leaf_idx >= len(node.get("tasks", [])):
        return jsonify({"ok": False, "error": "Invalid leaf index"}), 400

    target = node["tasks"][leaf_idx]

    if "subtasks" in target and target["subtasks"]:
        # This is a parent task — check its subtasks
        if new_status == "done" and not _can_mark_done(target):
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "Cannot mark done: unfinished subtasks remain.",
                    }
                ),
                400,
            )
        target["status"] = new_status
        # Cascade to subtasks when unchecking
        if new_status == "in_progress":
            for sub in target.get("subtasks", []):
                sub["status"] = "in_progress"
    else:
        # This is a subtask (or a task without subtasks)
        target["status"] = new_status
        # If a subtask is unchecked, cascade up to parent
        if new_status == "in_progress" and len(path) >= 2:
            parent = current_todo
            for idx in path[:-2]:
                parent = parent["tasks"][idx]
            parent_task = parent["tasks"][path[-2]]
            parent_task["status"] = "in_progress"
            # Also cascade to root
            current_todo["status"] = "in_progress"
        # If a subtask is checked, maybe parent can be marked done
        elif new_status == "done" and len(path) >= 2:
            parent = current_todo
            for idx in path[:-2]:
                parent = parent["tasks"][idx]
            parent_task = parent["tasks"][path[-2]]
            if _can_mark_done(parent_task):
                parent_task["status"] = "done"
                if _can_mark_done(current_todo):
                    current_todo["status"] = "done"

    # Update root-level parent status for top-level tasks
    if len(path) == 1 and new_status == "in_progress":
        current_todo["status"] = "in_progress"
    if len(path) == 1 and new_status == "done" and _can_mark_done(current_todo):
        current_todo["status"] = "done"

    # Persist to disk if we have a save path
    _save_current_todo()

    return jsonify({"ok": True, "status": target["status"]})


@app.route("/todo/download", methods=["GET"])
def todo_download():
    """Download the current todo as a JSON file."""
    global current_todo
    if current_todo is None:
        return "No active todo to download", 400

    from io import BytesIO

    data = json.dumps(current_todo, indent=2, ensure_ascii=False)
    buf = BytesIO(data.encode("utf-8"))
    buf.seek(0)

    timestamp = time.strftime("%Y%m%d-%H%M%S")
    return send_file(
        buf,
        mimetype="application/json",
        as_attachment=True,
        download_name=f"todo_{timestamp}.json",
    )


def _save_current_todo():
    """Persist current_todo to disk if path is known."""
    global current_todo, current_todo_path
    if current_todo is None or current_todo_path is None:
        return
    try:
        with open(current_todo_path, "w") as f:
            json.dump(current_todo, f, indent=2, ensure_ascii=False)
    except OSError:
        pass  # silently ignore persistence errors


if __name__ == "__main__":
    app.run(debug=True)
