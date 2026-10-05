"""Initial control-plane schema.

Revision ID: 0001
Revises:
Create Date: 2026-10-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False, unique=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("avatar_url", sa.String(2000)),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
    )
    op.create_table(
        "oauth_accounts",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "user_id", sa.String(64), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("provider_subject", sa.String(255), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.UniqueConstraint("provider", "provider_subject", name="uq_oauth_subject"),
    )
    op.create_table(
        "sessions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "user_id", sa.String(64), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("expires_at", TS, nullable=False),
        sa.Column("created_at", TS, nullable=False),
    )
    op.create_table(
        "models",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("display_name", sa.String(120), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("modality", sa.String(32), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("created_at", TS, nullable=False),
    )
    op.create_table(
        "model_versions",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column(
            "model_id",
            sa.String(64),
            sa.ForeignKey("models.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.String(32), nullable=False),
        sa.Column("container_image", sa.String(300), nullable=False),
        sa.Column("weights_version", sa.String(120), nullable=False),
        sa.Column("required_vram_gb", sa.Integer(), nullable=False),
        sa.Column("supported_gpus", postgresql.JSONB(), nullable=False),
        sa.Column("credit_cost", sa.Integer(), nullable=False),
        sa.Column("pricing_status", sa.String(32), nullable=False),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("capabilities", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.UniqueConstraint("model_id", "version", name="uq_model_version"),
    )
    op.create_table(
        "assets",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "user_id", sa.String(64), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("storage_key", sa.String(500), nullable=False),
        sa.Column("content_type", sa.String(120), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("width", sa.Integer()),
        sa.Column("height", sa.Integer()),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("deleted_at", TS),
    )
    op.create_index("ix_assets_user_created", "assets", ["user_id", "created_at"])
    op.create_table(
        "generations",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "user_id", sa.String(64), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("request_id", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("type", sa.String(16), nullable=False),
        sa.Column("mode", sa.String(32), nullable=False),
        sa.Column("model_id", sa.String(64), sa.ForeignKey("models.id"), nullable=False),
        sa.Column(
            "model_version_id", sa.String(80), sa.ForeignKey("model_versions.id"), nullable=False
        ),
        sa.Column("prompt_raw", sa.Text(), nullable=False),
        sa.Column("prompt_final", sa.Text()),
        sa.Column("prompt_ir", postgresql.JSONB()),
        sa.Column("parameters", postgresql.JSONB(), nullable=False),
        sa.Column("credit_price", sa.Integer(), nullable=False),
        sa.Column("failure_code", sa.String(64)),
        sa.Column("failure_message", sa.Text()),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.Column("completed_at", TS),
        sa.CheckConstraint(
            "status IN ('rejected','queued','running','completed','failed','cancelled')",
            name="ck_generations_status",
        ),
    )
    op.create_index("ix_generations_user_created", "generations", ["user_id", "created_at"])
    op.create_table(
        "generation_inputs",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "generation_id",
            sa.String(64),
            sa.ForeignKey("generations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("asset_id", sa.String(64), sa.ForeignKey("assets.id"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
    )
    op.create_table(
        "generation_outputs",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "generation_id",
            sa.String(64),
            sa.ForeignKey("generations.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("storage_key", sa.String(500), nullable=False),
        sa.Column("content_type", sa.String(120), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("gpu_millis", sa.Integer(), nullable=False),
        sa.Column("created_at", TS, nullable=False),
    )
    op.create_table(
        "jobs",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "generation_id",
            sa.String(64),
            sa.ForeignKey("generations.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("model_id", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("not_before", TS),
        sa.Column("lease_owner", sa.String(80)),
        sa.Column("lease_expires_at", TS),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.CheckConstraint(
            "status IN ('queued','running','completed','failed','cancelled')",
            name="ck_jobs_status",
        ),
    )
    op.create_index("ix_jobs_status_not_before", "jobs", ["status", "not_before"])
    op.create_table(
        "job_attempts",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "job_id", sa.String(64), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("worker_id", sa.String(80)),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("error_code", sa.String(64)),
        sa.Column("error_message", sa.Text()),
        sa.Column("started_at", TS, nullable=False),
        sa.Column("finished_at", TS),
        sa.UniqueConstraint("job_id", "attempt_number", name="uq_job_attempt"),
    )
    op.create_table(
        "credit_accounts",
        sa.Column(
            "user_id",
            sa.String(64),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("available", sa.BigInteger(), nullable=False),
        sa.Column("reserved", sa.BigInteger(), nullable=False),
        sa.CheckConstraint("available >= 0", name="ck_credits_available"),
        sa.CheckConstraint("reserved >= 0", name="ck_credits_reserved"),
    )
    op.create_table(
        "credit_transactions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "user_id", sa.String(64), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "generation_id", sa.String(64), sa.ForeignKey("generations.id", ondelete="CASCADE")
        ),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("available_after", sa.BigInteger(), nullable=False),
        sa.Column("reserved_after", sa.BigInteger(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("created_at", TS, nullable=False),
    )
    op.create_index("ix_credit_tx_user_created", "credit_transactions", ["user_id", "created_at"])
    op.create_index(
        "uq_credit_tx_generation_type",
        "credit_transactions",
        ["generation_id", "type"],
        unique=True,
        postgresql_where=sa.text(
            "generation_id IS NOT NULL AND type IN ('reservation','consumption','refund')"
        ),
    )
    op.create_table(
        "safety_events",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "user_id", sa.String(64), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "generation_id", sa.String(64), sa.ForeignKey("generations.id", ondelete="CASCADE")
        ),
        sa.Column("category", sa.String(64)),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("rule_id", sa.String(80)),
        sa.Column("prompt_hash", sa.String(64), nullable=False),
        sa.Column("detail", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", TS, nullable=False),
    )
    op.create_table(
        "idempotency_keys",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "user_id", sa.String(64), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("key", sa.String(200), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column(
            "generation_id", sa.String(64), sa.ForeignKey("generations.id", ondelete="CASCADE")
        ),
        sa.Column("error_code", sa.String(64)),
        sa.Column("error_message", sa.Text()),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("expires_at", TS, nullable=False),
        sa.UniqueConstraint("user_id", "key", name="uq_idempotency_user_key"),
    )
    op.create_table(
        "worker_records",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("model_id", sa.String(64), nullable=False),
        sa.Column("gpu", sa.String(64), nullable=False),
        sa.Column("vram_gb", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("advertise_url", sa.String(300), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.Column("stopped_at", TS),
    )


def downgrade() -> None:
    for name in (
        "worker_records",
        "idempotency_keys",
        "safety_events",
        "credit_transactions",
        "credit_accounts",
        "job_attempts",
        "jobs",
        "generation_outputs",
        "generation_inputs",
        "generations",
        "assets",
        "model_versions",
        "models",
        "sessions",
        "oauth_accounts",
        "users",
    ):
        op.drop_table(name)
