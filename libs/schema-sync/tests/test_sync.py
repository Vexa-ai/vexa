from unittest.mock import MagicMock, patch

from sqlalchemy import Column, Integer, MetaData, String
from sqlalchemy.orm import declarative_base

from schema_sync import sync


Base = declarative_base(metadata=MetaData(schema="vexa"))


class Widget(Base):
    __tablename__ = "widgets"

    id = Column(Integer, primary_key=True)
    name = Column(String(32), nullable=False)


def test_ensure_schemas_creates_declared_schema():
    conn = MagicMock()

    sync._ensure_schemas(conn, Base)

    executed = conn.execute.call_args.args[0]
    assert executed.text == 'CREATE SCHEMA IF NOT EXISTS "vexa"'


def test_sync_columns_uses_schema_aware_introspection_and_qualified_alter():
    conn = MagicMock()
    inspector = MagicMock()
    inspector.get_table_names.return_value = ["widgets"]
    inspector.get_columns.return_value = [{"name": "id"}]

    with patch("schema_sync.sync.inspect", return_value=inspector):
        sync._sync_columns(conn, Base)

    inspector.get_table_names.assert_called_once_with(schema="vexa")
    inspector.get_columns.assert_called_once_with("widgets", schema="vexa")
    executed = conn.execute.call_args.args[0]
    assert executed.text == (
        'ALTER TABLE "vexa"."widgets" ADD COLUMN "name" VARCHAR(32) NOT NULL DEFAULT \'\''
    )
