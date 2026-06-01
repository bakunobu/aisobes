"""rename_problem_to_project

Revision ID: 63a8c874fa8c
Revises: 6b2c22ec421c
Create Date: 2026-06-01 20:49:20.206956

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '63a8c874fa8c'
down_revision = '6b2c22ec421c'
branch_labels = None
depends_on = None


def upgrade():
    # Step 1: Create the new "projects" table with same schema as "problems"
    op.create_table(
        'projects',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('created', sa.DateTime(), nullable=True),
        sa.Column('first_run', sa.DateTime(), nullable=True),
        sa.Column('last_run', sa.DateTime(), nullable=True),
        sa.Column('number_of_runs', sa.Integer(), nullable=True),
        sa.Column('completed_runs', sa.Integer(), nullable=True),
        sa.Column('interrupted_runs', sa.Integer(), nullable=True),
        sa.Column('total_time_spent', sa.Integer(), nullable=True),
        sa.Column('estimated_time', sa.Integer(), nullable=True),
        sa.Column('is_completed', sa.Boolean(), nullable=True),
        sa.Column('is_archived', sa.Boolean(), nullable=True),
        sa.Column('is_deleted', sa.Boolean(), nullable=True),
        sa.Column('priority', sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )

    # Step 2: Copy data from problems to projects
    op.execute("""
        INSERT INTO projects (id, description, created, first_run, last_run,
            number_of_runs, completed_runs, interrupted_runs, total_time_spent,
            estimated_time, is_completed, is_archived, is_deleted, priority)
        SELECT id, description, created, first_run, last_run,
            number_of_runs, completed_runs, interrupted_runs, total_time_spent,
            estimated_time, is_completed, is_archived, is_deleted, priority
        FROM problems
    """)

    # Step 3: Create project_tags table with FKs referencing projects + tags
    op.create_table(
        'project_tags',
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('tag_id', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ['project_id'], ['projects.id'],
            name='fk_project_tags_project_id'
        ),
        sa.ForeignKeyConstraint(
            ['tag_id'], ['tags.id'],
            name='fk_project_tags_tag_id'
        ),
        sa.PrimaryKeyConstraint('project_id', 'tag_id')
    )

    # Step 4: Copy data from problem_tags to project_tags
    op.execute("""
        INSERT INTO project_tags (project_id, tag_id)
        SELECT problem_id, tag_id FROM problem_tags
    """)

    # Step 5: Rename problem_id → project_id on tasks (batch mode for SQLite)
    with op.batch_alter_table('tasks') as batch_op:
        batch_op.alter_column('problem_id', new_column_name='project_id')

    # Step 6: Drop old tables
    op.drop_table('problem_tags')
    op.drop_table('problems')


def downgrade():
    # Reverse Step 6: recreate problems
    op.create_table(
        'problems',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('created', sa.DateTime(), nullable=True),
        sa.Column('first_run', sa.DateTime(), nullable=True),
        sa.Column('last_run', sa.DateTime(), nullable=True),
        sa.Column('number_of_runs', sa.Integer(), nullable=True),
        sa.Column('completed_runs', sa.Integer(), nullable=True),
        sa.Column('interrupted_runs', sa.Integer(), nullable=True),
        sa.Column('total_time_spent', sa.Integer(), nullable=True),
        sa.Column('estimated_time', sa.Integer(), nullable=True),
        sa.Column('is_completed', sa.Boolean(), nullable=True),
        sa.Column('is_archived', sa.Boolean(), nullable=True),
        sa.Column('is_deleted', sa.Boolean(), nullable=True),
        sa.Column('priority', sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )

    op.execute("""
        INSERT INTO problems (id, description, created, first_run, last_run,
            number_of_runs, completed_runs, interrupted_runs, total_time_spent,
            estimated_time, is_completed, is_archived, is_deleted, priority)
        SELECT id, description, created, first_run, last_run,
            number_of_runs, completed_runs, interrupted_runs, total_time_spent,
            estimated_time, is_completed, is_archived, is_deleted, priority
        FROM projects
    """)

    # Recreate problem_tags
    op.create_table(
        'problem_tags',
        sa.Column('problem_id', sa.Integer(), nullable=False),
        sa.Column('tag_id', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ['problem_id'], ['problems.id'],
            name='fk_problem_tags_problem_id'
        ),
        sa.ForeignKeyConstraint(
            ['tag_id'], ['tags.id'],
            name='fk_problem_tags_tag_id'
        ),
        sa.PrimaryKeyConstraint('problem_id', 'tag_id')
    )

    op.execute("""
        INSERT INTO problem_tags (problem_id, tag_id)
        SELECT project_id, tag_id FROM project_tags
    """)

    # Rename project_id back to problem_id
    with op.batch_alter_table('tasks') as batch_op:
        batch_op.alter_column('project_id', new_column_name='problem_id')

    # Drop new tables
    op.drop_table('project_tags')
    op.drop_table('projects')
