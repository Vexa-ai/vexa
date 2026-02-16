"""Add recordings, media_files, and transcription_jobs tables

Revision ID: a1b2c3d4e5f6
Revises: 5befe308fa8b
Create Date: 2026-02-11 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'a1b2c3d4e5f6'
down_revision = '5befe308fa8b'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- recordings table ---
    op.create_table(
        'recordings',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('meeting_id', sa.Integer(), nullable=True),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('session_uid', sa.String(), nullable=True),
        sa.Column('source', sa.String(50), nullable=False, server_default='bot'),
        sa.Column('status', sa.String(50), nullable=False, server_default='in_progress'),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['meeting_id'], ['meetings.id']),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_recordings_id', 'recordings', ['id'])
    op.create_index('ix_recordings_meeting_id', 'recordings', ['meeting_id'])
    op.create_index('ix_recordings_user_id', 'recordings', ['user_id'])
    op.create_index('ix_recordings_session_uid', 'recordings', ['session_uid'])
    op.create_index('ix_recordings_status', 'recordings', ['status'])
    op.create_index('ix_recordings_created_at', 'recordings', ['created_at'])
    op.create_index('ix_recording_meeting_session', 'recordings', ['meeting_id', 'session_uid'])
    op.create_index('ix_recording_user_created', 'recordings', ['user_id', 'created_at'])

    # --- media_files table ---
    op.create_table(
        'media_files',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('recording_id', sa.Integer(), nullable=False),
        sa.Column('type', sa.String(50), nullable=False),
        sa.Column('format', sa.String(20), nullable=False),
        sa.Column('storage_path', sa.String(1024), nullable=False),
        sa.Column('storage_backend', sa.String(50), nullable=False, server_default='minio'),
        sa.Column('file_size_bytes', sa.Integer(), nullable=True),
        sa.Column('duration_seconds', sa.Float(), nullable=True),
        sa.Column('metadata', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(['recording_id'], ['recordings.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_media_files_id', 'media_files', ['id'])
    op.create_index('ix_media_files_recording_id', 'media_files', ['recording_id'])

    # --- transcription_jobs table ---
    op.create_table(
        'transcription_jobs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('recording_id', sa.Integer(), nullable=False),
        sa.Column('meeting_id', sa.Integer(), nullable=True),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('language', sa.String(10), nullable=True),
        sa.Column('task', sa.String(50), nullable=False, server_default='transcribe'),
        sa.Column('status', sa.String(50), nullable=False, server_default='pending'),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('progress', sa.Float(), nullable=True),
        sa.Column('segments_count', sa.Integer(), nullable=True),
        sa.Column('session_uid', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['recording_id'], ['recordings.id']),
        sa.ForeignKeyConstraint(['meeting_id'], ['meetings.id']),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_transcription_jobs_id', 'transcription_jobs', ['id'])
    op.create_index('ix_transcription_jobs_recording_id', 'transcription_jobs', ['recording_id'])
    op.create_index('ix_transcription_jobs_meeting_id', 'transcription_jobs', ['meeting_id'])
    op.create_index('ix_transcription_jobs_user_id', 'transcription_jobs', ['user_id'])
    op.create_index('ix_transcription_jobs_session_uid', 'transcription_jobs', ['session_uid'])
    op.create_index('ix_transcription_jobs_created_at', 'transcription_jobs', ['created_at'])
    op.create_index('ix_transcription_job_status_created', 'transcription_jobs', ['status', 'created_at'])
    op.create_index('ix_transcription_job_user_created', 'transcription_jobs', ['user_id', 'created_at'])


def downgrade() -> None:
    op.drop_table('transcription_jobs')
    op.drop_table('media_files')
    op.drop_table('recordings')
