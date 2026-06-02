"""add_timer_sessions_table

Revision ID: 16fe869f81b6
Revises: 63a8c874fa8c
Create Date: 2026-06-02 22:04:56.914143

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '16fe869f81b6'
down_revision = '63a8c874fa8c'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('timer_sessions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('task_id', sa.Integer(), nullable=True),
    sa.Column('subtask_id', sa.Integer(), nullable=True),
    sa.Column('planned_duration', sa.Integer(), nullable=True),
    sa.Column('actual_duration', sa.Integer(), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=True),
    sa.Column('start_time', sa.DateTime(), nullable=True),
    sa.Column('end_time', sa.DateTime(), nullable=True),
    sa.Column('created', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['subtask_id'], ['subtasks.id'], ),
    sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], ),
    sa.PrimaryKeyConstraint('id')
    )


def downgrade():
    op.drop_table('timer_sessions')
