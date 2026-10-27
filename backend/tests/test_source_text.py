import pytest
from sqlalchemy import Column, Integer, MetaData, Table, create_engine, select
from sqlalchemy.dialects import postgresql, sqlite

from app.core.source_text import SourceText


@pytest.mark.parametrize("dialect", [postgresql.dialect(), sqlite.dialect()])
@pytest.mark.parametrize("value", [None, "", "ordinary text", "公式 β₁", "a\0b", "\0\0",
    "__paperpilot_source_text_v1__:literal", '__paperpilot_source_text_v1__:"collision"'])
def test_source_text_exact_round_trip_without_database_nul(value, dialect):
    column = SourceText()
    bound = column.process_bind_param(value, dialect)
    assert bound is None or "\0" not in bound
    assert column.process_result_value(bound, dialect) == value
    if value is not None and "\0" not in value and not value.startswith(column.prefix):
        assert bound == value


def test_orm_reads_decode_while_raw_sql_contains_no_nul():
    engine = create_engine("sqlite://")
    table = Table("source", MetaData(), Column("id", Integer, primary_key=True), Column("text", SourceText))
    table.metadata.create_all(engine)
    original = "PDF evidence\0next symbol β"
    with engine.begin() as connection:
        connection.execute(table.insert().values(id=1, text=original))
        assert connection.scalar(select(table.c.text)) == original
        assert "\0" not in connection.exec_driver_sql("select text from source").scalar()
        connection.exec_driver_sql("insert into source(id, text) values (2, 'legacy unencoded')")
        assert connection.scalar(select(table.c.text).where(table.c.id == 2)) == 'legacy unencoded'
