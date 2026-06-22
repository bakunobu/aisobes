from __future__ import with_statement
import os
from flask import current_app
from alembic import context
from sqlalchemy import create_engine, engine_from_config, pool
import logging

# Import your models here
from models import *
from extensions import db

# This is the Alembic Config object
config = context.config

# Configure basic logging
logging.basicConfig()
logger = logging.getLogger('alembic')
logger.setLevel(logging.INFO)

target_metadata = db.Model.metadata

def run_migrations_offline():
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    """Run migrations in 'online' mode."""
    from app import app as flask_app
    url = flask_app.config["SQLALCHEMY_DATABASE_URI"]
    connectable = create_engine(url)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()