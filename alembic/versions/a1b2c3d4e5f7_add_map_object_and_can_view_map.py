"""add map_object table and can_view_map permission

Revision ID: a1b2c3d4e5f7
Revises: f7e8d9c0b1a2
Create Date: 2026-03-18
"""
from alembic import op
import sqlalchemy as sa

revision = "a1b2c3d4e5f7"
down_revision = "f7e8d9c0b1a2"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "map_object",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("lat", sa.Float(), nullable=False),
        sa.Column("lon", sa.Float(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("icon_color", sa.String(20), nullable=False, server_default="#e74c3c"),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("NOW()")),
    )

    op.add_column(
        "dashboard_users",
        sa.Column("can_view_map", sa.Boolean(), nullable=False, server_default="0"),
    )


def downgrade():
    op.drop_column("dashboard_users", "can_view_map")
    op.drop_table("map_object")
