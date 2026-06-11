"""SQLAlchemy ORM models for the Organizer application.

Hierarchy:
    Project (epic)
      └── Task
            └── Subtask

Each level supports many-to-many tags.
All mutations (promote, split, merge) are recorded in ChangeLog.

Users can be assigned roles (owner, creator, participant, assignee) on any
entity level via the polymorphic UserEntityRole table.
"""

from datetime import datetime, timezone
from sqlalchemy import select, exists, and_, or_
from sqlalchemy.ext.hybrid import hybrid_property

from extensions import db


# ===========================================================================
# User
# ===========================================================================

class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(255), nullable=False, unique=True)
    password_hash = db.Column(db.String(255), nullable=False)
    avatar = db.Column(db.String(500), nullable=True)
    role = db.Column(db.String(20), default="member")  # 'admin' | 'member'
    is_active = db.Column(db.Boolean, default=True)
    last_login = db.Column(db.DateTime, nullable=True)
    created = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    # Relationships
    entity_roles = db.relationship("UserEntityRole", back_populates="user", lazy="dynamic")

    def __repr__(self):
        return f"<User {self.id}: {self.name!r}>"


# ===========================================================================
# UserEntityRole — polymorphic join: user ↔ (Project | Task | Subtask)
# ===========================================================================

class UserEntityRole(db.Model):
    __tablename__ = "user_entity_roles"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    entity_type = db.Column(
        db.String(20), nullable=False
    )  # 'project' | 'task' | 'subtask'
    entity_id = db.Column(db.Integer, nullable=False)
    role = db.Column(
        db.String(20), nullable=False
    )  # 'owner' | 'creator' | 'participant' | 'assignee'
    created = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    # Relationships
    user = db.relationship("User", back_populates="entity_roles")

    # A user can hold multiple roles on the same entity (e.g. creator + owner).
    __table_args__ = (
        db.UniqueConstraint(
            "user_id", "entity_type", "entity_id", "role",
            name="uq_user_entity_role",
        ),
    )

    def __repr__(self):
        return (
            f"<UserEntityRole user={self.user_id} "
            f"{self.entity_type}#{self.entity_id} as {self.role}>"
        )


# ===========================================================================
# Tag
# ===========================================================================

class Tag(db.Model):
    __tablename__ = "tags"

    id = db.Column(db.Integer, primary_key=True)
    tag = db.Column(db.String(120), nullable=False, unique=True)

    # Relationships
    projects = db.relationship("Project", secondary="project_tags", back_populates="tags")
    tasks = db.relationship("Task", secondary="task_tags", back_populates="tags")
    subtasks = db.relationship("Subtask", secondary="subtask_tags", back_populates="tags")

    def __repr__(self):
        return f"<Tag {self.tag!r}>"


# ===========================================================================
# Project
# ===========================================================================

class Project(db.Model):
    __tablename__ = "projects"

    id = db.Column(db.Integer, primary_key=True)
    description = db.Column(db.Text, nullable=False)
    created = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    first_run = db.Column(db.DateTime, nullable=True)
    last_run = db.Column(db.DateTime, nullable=True)
    number_of_runs = db.Column(db.Integer, default=0)
    completed_runs = db.Column(db.Integer, default=0)
    interrupted_runs = db.Column(db.Integer, default=0)
    total_time_spent = db.Column(db.Integer, default=0)  # actual tracked seconds
    estimated_time = db.Column(db.Integer, default=0)  # estimated seconds
    is_completed = db.Column(db.Boolean, default=False)
    is_archived = db.Column(db.Boolean, default=False)
    is_deleted = db.Column(db.Boolean, default=False)  # soft delete
    priority = db.Column(db.Integer, default=3)  # 1=highest, 5=lowest
    is_routine = db.Column(db.Boolean, default=False)  # routine project flag

    # Relationships
    tags = db.relationship("Tag", secondary="project_tags", back_populates="projects")
    tasks = db.relationship("Task", back_populates="project", lazy="dynamic")
    user_roles = db.relationship(
        "UserEntityRole",
        primaryjoin="and_(Project.id == foreign(UserEntityRole.entity_id), "
                    "UserEntityRole.entity_type == 'project')",
        viewonly=True,
        lazy="dynamic",
    )

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

    def __repr__(self):
        return f"<Project {self.id}: {self.description[:40]!r}>"


# ===========================================================================
# Project ↔ Tag mapping
# ===========================================================================

class ProjectTag(db.Model):
    __tablename__ = "project_tags"

    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), primary_key=True)
    tag_id = db.Column(db.Integer, db.ForeignKey("tags.id"), primary_key=True)


# ===========================================================================
# Task
# ===========================================================================

class Task(db.Model):
    __tablename__ = "tasks"

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=False)
    created = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    first_run = db.Column(db.DateTime, nullable=True)
    total_time_spent = db.Column(db.Integer, default=0)  # actual tracked seconds
    estimated_time = db.Column(db.Integer, default=0)  # estimated seconds
    is_completed = db.Column(db.Boolean, default=False)
    is_archived = db.Column(db.Boolean, default=False)
    is_deleted = db.Column(db.Boolean, default=False)
    priority = db.Column(db.Integer, default=3)  # 1=highest, 5=lowest

    # Relationships
    project = db.relationship("Project", back_populates="tasks")
    tags = db.relationship("Tag", secondary="task_tags", back_populates="tasks")
    subtasks = db.relationship("Subtask", back_populates="task", lazy="dynamic")
    timer_sessions = db.relationship(
        "TimerSession", back_populates="task", lazy="dynamic",
        foreign_keys="TimerSession.task_id",
    )
    user_roles = db.relationship(
        "UserEntityRole",
        primaryjoin="and_(Task.id == foreign(UserEntityRole.entity_id), "
                    "UserEntityRole.entity_type == 'task')",
        viewonly=True,
        lazy="dynamic",
    )

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

    def __repr__(self):
        return f"<Task {self.id} (project={self.project_id})>"


# ===========================================================================
# Task ↔ Tag mapping
# ===========================================================================

class TaskTag(db.Model):
    __tablename__ = "task_tags"
    
    task_id = db.Column(db.Integer, db.ForeignKey("tasks.id"), primary_key=True)
    tag_id = db.Column(db.Integer, db.ForeignKey("tags.id"), primary_key=True)


# ===========================================================================
# EntityDependency — polymorphic blocking dependencies
# ===========================================================================

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


# ===========================================================================
# Subtask
# ===========================================================================

class Subtask(db.Model):
    __tablename__ = "subtasks"

    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey("tasks.id"), nullable=False)
    created = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    first_run = db.Column(db.DateTime, nullable=True)
    total_time_spent = db.Column(db.Integer, default=0)  # actual tracked seconds
    estimated_time = db.Column(db.Integer, default=0)  # estimated seconds
    is_completed = db.Column(db.Boolean, default=False)
    is_archived = db.Column(db.Boolean, default=False)
    is_deleted = db.Column(db.Boolean, default=False)
    priority = db.Column(db.Integer, default=3)  # 1=highest, 5=lowest

    # Relationships
    task = db.relationship("Task", back_populates="subtasks")
    tags = db.relationship("Tag", secondary="subtask_tags", back_populates="subtasks")
    timer_sessions = db.relationship(
        "TimerSession", back_populates="subtask", lazy="dynamic",
        foreign_keys="TimerSession.subtask_id",
    )
    user_roles = db.relationship(
        "UserEntityRole",
        primaryjoin="and_(Subtask.id == foreign(UserEntityRole.entity_id), "
                    "UserEntityRole.entity_type == 'subtask')",
        viewonly=True,
        lazy="dynamic",
    )

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
    )  # 'project' | 'task' | 'subtask'
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


# ===========================================================================
# TimerSession — records individual countdown/pomodoro timer sessions
# ===========================================================================


class TimerSession(db.Model):
    __tablename__ = "timer_sessions"

    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey("tasks.id"), nullable=True)
    subtask_id = db.Column(db.Integer, db.ForeignKey("subtasks.id"), nullable=True)
    planned_duration = db.Column(db.Integer, default=0)  # seconds
    actual_duration = db.Column(db.Integer, default=0)   # seconds
    status = db.Column(
        db.String(20), default="inactive"
    )  # 'inactive' | 'active' | 'paused' | 'completed' | 'stopped'
    start_time = db.Column(db.DateTime, nullable=True)
    end_time = db.Column(db.DateTime, nullable=True)
    created = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    # Relationships
    task = db.relationship(
        "Task", back_populates="timer_sessions",
        foreign_keys=[task_id],
    )
    subtask = db.relationship(
        "Subtask", back_populates="timer_sessions",
        foreign_keys=[subtask_id],
    )

    def __repr__(self):
        target = f"task={self.task_id}" if self.task_id else f"subtask={self.subtask_id}"
        return f"<TimerSession {self.id} ({target}) status={self.status}>"


# ===========================================================================
# RoutineTask — recurring tasks for routine projects (no subtasks, fixed schedule)
# ===========================================================================


class RoutineTask(db.Model):
    __tablename__ = "routine_tasks"

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=False)
    description = db.Column(db.Text, nullable=False)
    days_of_week = db.Column(
        db.String(50), default=""
    )  # comma-separated: "mon,tue,wed"
    time_of_day = db.Column(db.String(5), default="09:00")  # "HH:MM"
    duration = db.Column(db.Integer, default=30)  # minutes
    start_date = db.Column(db.Date, nullable=True)
    end_date = db.Column(db.Date, nullable=True)
    is_active = db.Column(db.Boolean, default=True)
    created = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    # Relationships
    project = db.relationship(
        "Project", backref=db.backref("routine_tasks", lazy="dynamic")
    )


# ===========================================================================
# ReminderTask — one-off timed reminders for routine projects
# ===========================================================================


class ReminderTask(db.Model):
    __tablename__ = "reminder_tasks"
    
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=False)
    description = db.Column(db.Text, nullable=False)
    due_datetime = db.Column(db.DateTime, nullable=False)
    duration = db.Column(db.Integer, default=30)  # minutes
    is_completed = db.Column(db.Boolean, default=False)
    is_active = db.Column(db.Boolean, default=True)
    created = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    
    # Relationships
    project = db.relationship(
        "Project", backref=db.backref("reminder_tasks", lazy="dynamic")
    )
    

# ===========================================================================
# Quest — challenges with rewards
# ===========================================================================


class Quest(db.Model):
    __tablename__ = "quests"
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.Text, nullable=False)
    quest_type = db.Column(db.String(20), nullable=False)  # daily_tasks/daily_time/total_time/complete_task
    target_type = db.Column(db.String(20), nullable=False)  # all/project/tag/task/subtask
    target_id = db.Column(db.Integer, nullable=True)  # FK to project/task/subtask
    target_tag = db.Column(db.String(120), nullable=True)  # tag name
    goal_value = db.Column(db.Integer, nullable=False)  # tasks count or seconds
    award_type = db.Column(db.String(20), nullable=False)  # goods/budget/free_time
    award_description = db.Column(db.Text, nullable=False)
    is_active = db.Column(db.Boolean, default=True)
    start_date = db.Column(db.Date, nullable=True)
    end_date = db.Column(db.Date, nullable=True)
    created = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    
    def __repr__(self):
        return f"<Quest {self.id}: {self.name[:20]!r}>"


# ===========================================================================
# QuestLog — progress tracking
# ===========================================================================


class QuestLog(db.Model):
    __tablename__ = "quest_logs"
    
    id = db.Column(db.Integer, primary_key=True)
    quest_id = db.Column(db.Integer, db.ForeignKey("quests.id"), nullable=False)
    event_type = db.Column(db.String(20), nullable=False)  # progress/milestone/completed/failed
    value = db.Column(db.Integer, nullable=False)  # numeric progress at this point
    description = db.Column(db.Text, nullable=False)
    created = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    
    # Relationships
    quest = db.relationship("Quest", backref=db.backref("logs", lazy="dynamic"))
    
    def __repr__(self):
        return f"<QuestLog {self.id} for quest={self.quest_id} ({self.event_type})>"

