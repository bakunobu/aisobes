"""Flask application entry point — minimal shell for the Organizer app."""

from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate

# ---------------------------------------------------------------------------
# Flask application factory
# ---------------------------------------------------------------------------

app = Flask(__name__)

# Database configuration
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///organizer.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# Extensions
db = SQLAlchemy(app)
migrate = Migrate(app, db)

# Import models so Alembic can detect them for autogenerate
import models  # noqa: E402,F401


# ---------------------------------------------------------------------------
# Minimal health-check route
# ---------------------------------------------------------------------------

@app.route("/")
def home():
    return {"status": "ok", "app": "organizer"}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app.run(debug=True)
