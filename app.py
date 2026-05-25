import json
import time

import requests
from dotenv import dotenv_values
from flask import Flask, redirect, render_template, request, url_for

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


def log_status(message):
    """Add status message with timestamp"""
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    status_log.append(f"[{timestamp}] {message}")
    if len(status_log) > 10:  # Keep last 10 messages
        status_log.pop(0)


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


if __name__ == "__main__":
    app.run(debug=True)
