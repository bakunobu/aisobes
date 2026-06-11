import logging
import sys
import os
from logging.config import fileConfig
from flask import current_app
from alembic import context

# Add project root to sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app  # Import monolithic app instance

with app.app_context():
    config = context.config
    fileConfig(config.config_file_name)
    
    # Set sqlalchemy.url from app's database configuration
    config.set_main_option('sqlalchemy.url', 
        current_app.extensions['migrate'].db.engine.url.render_as_string(hide_password=False))
    
    # Get target database metadata
    target_db = current_app.extensions['migrate'].db

# Other migration functions remain unchanged

def run_migrations_offline():
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_db.metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    """Run migrations in 'online' mode."""
    connectable = target_db.engine

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_db.metadata
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
