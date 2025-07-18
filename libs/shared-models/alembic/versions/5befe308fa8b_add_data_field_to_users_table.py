"""Add data field to users table

Revision ID: 5befe308fa8b
Revises: dc59a1c03d1f
Create Date: 2025-06-08 15:13:33.763066

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = '5befe308fa8b'
down_revision = 'dc59a1c03d1f'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.add_column('users', sa.Column('data', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False))
    # ### end Alembic commands ###


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_column('users', 'data')
    # ### end Alembic commands ### 