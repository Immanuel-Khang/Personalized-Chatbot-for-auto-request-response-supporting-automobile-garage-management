"""rag chunks (pgvector + full-text)

Revision ID: a1c3e5f7b9d2
Revises: 52fc2ea4db26
Create Date: 2026-10-19 10:30:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "a1c3e5f7b9d2"
down_revision: Union[str, Sequence[str], None] = "52fc2ea4db26"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DIM = 1536


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "rag_chunks",
        sa.Column("namespace", sa.String(40), server_default="knowledge", nullable=False),
        sa.Column("id", sa.String(40), nullable=False),
        sa.Column("source", sa.String(200), nullable=False),
        sa.Column("section", sa.Text(), server_default="", nullable=False),
        sa.Column("kind", sa.String(10), server_default="doc", nullable=False),
        sa.Column("tags", postgresql.ARRAY(sa.String()), server_default="{}", nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("index_text", sa.Text(), nullable=False),
        sa.Column("lex_text", sa.Text(), nullable=False),
        sa.Column("tsv", postgresql.TSVECTOR(), sa.Computed("to_tsvector('simple', lex_text)", persisted=True)),
        sa.Column("embedding", Vector(DIM), nullable=True),
        sa.Column("embedding_model", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("namespace", "id", name=op.f("pk_rag_chunks")),
    )
    op.create_index("ix_rag_chunks_tsv", "rag_chunks", ["tsv"], postgresql_using="gin")
    op.create_index("ix_rag_chunks_embedding", "rag_chunks", ["embedding"], postgresql_using="hnsw",
                    postgresql_ops={"embedding": "vector_cosine_ops"})


def downgrade() -> None:
    op.drop_index("ix_rag_chunks_embedding", table_name="rag_chunks")
    op.drop_index("ix_rag_chunks_tsv", table_name="rag_chunks")
    op.drop_table("rag_chunks")
    # không DROP EXTENSION vector: bảng khác có thể đang dùng
