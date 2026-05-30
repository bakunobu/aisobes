"""SQLAlchemy ORM models for the Organizer application.

Hierarchy:
    Problem (epic)
      └── Task
            └── Subtask

Each level supports many-to-many tags.
All mutations (promote, split, merge) are recorded in ChangeLog.
"""

from datetime import datetime, timezone

from app import db


# ===========================================================================
# Tag
# ===========================================================================

class Tag(db.Model):
    __tablename__ = "tags"

    id = db.Column(db.Integer, primary_key=True)
    tag = db.Column(db.String(120), nullable=False, unique=True)

    # Relationships
    problems = db.relationship("Problem", secondary="problem_tags", back_populates="tags")
    tasks = db.relationship("Task", secondary="task_tags", back_populates="tags")
    subtasks = db.relationship("Subtask", secondary="subtask_tags", back_populates="tags")

    def __repr__(self):
        return f"<Tag {self.tag!r}>"


# ===========================================================================
# Problem
# ===========================================================================

class Problem(db.Model):
    __tablename__ = "problems"

    id = db.Column(db.Integer, primary_key=True)
    description = db.Column(db.Text, nullable=False)
    created = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    first_run = db.Column(db.DateTime, nullable=True)
    last_run = db.Column(db.DateTime, nullable=True)
    number_of_runs = db.Column(db.Integer, default=0)
    completed_runs = db.Column(db.Integer, default=0)
    interrupted_runs = db.Column(db.Integer, default=0)
    total_time_spent = db.Column(db.Integer, default=0)  # seconds
    is_completed = db.Column(db.Boolean, default=False)
    is_archived = db.Column(db.Boolean, default=False)
    is_deleted = db.Column(db.Boolean, default=False)  # soft delete
    priority = db.Column(db.Integer, default=3)  # 1=highest, 5=lowest

    # Relationships
    tags = db.relationship("Tag", secondary="problem_tags", back_populates="problems")
    tasks = db.relationship("Task", back_populates="problem", lazy="dynamic")

    def __repr__(self):
        return f"<Problem {self.id}: {self.description[:40]!r}>"


# ===========================================================================
# Problem ↔ Tag mapping
# ===========================================================================

class ProblemTag(db.Model):
    __tablename__ = "problem_tags"

    problem_id = db.Column(db.Integer, db.ForeignKey("problems.id"), primary_key=True)
    tag_id = db.Column(db.Integer, db.ForeignKey("tags.id"), primary_key=True)


# ===========================================================================
# Task
# ===========================================================================

class Task(db.Model):
    __tablename__ = "tasks"

    id = db.Column(db.Integer, primary_key=True)
    problem_id = db.Column(db.Integer, db.ForeignKey("problems.id"), nullable=False)
    created = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    first_run = db.Column(db.DateTime, nullable=True)
    total_time_spent = db.Column(db.Integer, default=0)  # seconds
    is_completed = db.Column(db.Boolean, default=False)
    is_archived = db.Column(db.Boolean, default=False)
    is_deleted = db.Column(db.Boolean, default=False)
    priority = db.Column(db.Integer, default=3)  # 1=highest, 5=lowest

    # Relationships
    problem = db.relationship("Problem", back_populates="tasks")
    tags = db.relationship("Tag", secondary="task_tags", back_populates="tasks")
    subtasks = db.relationship("Subtask", back_populates="task", lazy="dynamic")

    def __repr__(self):
        return f"<Task {self.id} (problem={self.problem_id})>"


# ===========================================================================
# Task ↔ Tag mapping
# ===========================================================================

class TaskTag(db.Model):
    __tablename__ = "task_tags"

    task_id = db.Column(db.Integer, db.ForeignKey("tasks.id"), primary_key=True)
    tag_id = db.Column(db.Integer, db.ForeignKey("tags.id"), primary_key=True)


# ===========================================================================
# Subtask
# ===========================================================================

class Subtask(db.Model):
    __tablename__ = "subtasks"

    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey("tasks.id"), nullable=False)
    created = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    first_run = db.Column(db.DateTime, nullable=True)
    total_time_spent = db.Column(db.Integer, default=0)  # seconds
    is_completed = db.Column(db.Boolean, default=False)
    is_archived = db.Column(db.Boolean, default=False)
    is_deleted = db.Column(db.Boolean, default=False)
    priority = db.Column(db.Integer, default=3)  # 1=highest, 5=lowest

    # Relationships
    task = db.relationship("Task", back_populates="subtasks")
    tags = db.relationship("Tag", secondary="subtask_tags", back_populates="subtasks")

    def __repr__(self):
        return f"<Subtask {self.id} (task={self.task_id})>"


# ===========================================================================
# Subtask ↔ Tag mapping
# ===========================================================================

class SubtaskTag(db.Model):
    __tablename__ = "subtask_tags"

    subtask_id = db.Column(db.Integer, db.ForeignKey("subtasks.id"), primary_key=True)
    tag_id = db.Column(db.Integer, db.ForeignKey("tags.id"), primary_key=True)


# ===========================================================================
# ChangeLog — records promote / split / merge / archive events
# ===========================================================================

class ChangeLog(db.Model):
    __tablename__ = "change_log"

    id = db.Column(db.Integer, primary_key=True)
    entity_type = db.Column(
        db.String(20), nullable=False
    )  # 'problem' | 'task' | 'subtask'
    entity_id = db.Column(db.Integer, nullable=False)
    change_type = db.Column(
        db.String(20), nullable=False
    )  # 'promote' | 'split' | 'merge' | 'archive' | 'complete' | 'delete'
    from_parent_id = db.Column(db.Integer, nullable=True)  # parent before change
    to_parent_id = db.Column(db.Integer, nullable=True)  # parent after change
    description = db.Column(db.Text, nullable=True)
    created = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return f"<ChangeLog {self.change_type} on {self.entity_type}#{self.entity_id}>"
