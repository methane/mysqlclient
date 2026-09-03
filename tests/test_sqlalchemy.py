from contextlib import contextmanager

import pytest


pytest.importorskip("sqlalchemy", minversion="2.0")

from sqlalchemy import (
    Integer,
    bindparam,
    create_engine,
    delete,
    event,
    insert,
    select,
    update,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column
from sqlalchemy.pool import NullPool

from MySQLdb.constants import CLIENT
from configdb import connection_kwargs


class Base(DeclarativeBase):
    pass


class BulkRow(Base):
    __tablename__ = "test_sqlalchemy_executemany"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    value: Mapped[int] = mapped_column(Integer, nullable=False)


@pytest.fixture(scope="module")
def engine():
    engine = create_engine(
        "mysql+mysqldb://",
        connect_args=connection_kwargs({"executemany_fallback": "multi"}),
        poolclass=NullPool,
    )
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


def reset_rows(engine):
    with engine.begin() as connection:
        connection.execute(delete(BulkRow))
        connection.execute(
            insert(BulkRow),
            [
                {"id": 1, "value": 10},
                {"id": 2, "value": 20},
                {"id": 3, "value": 30},
            ],
        )


@contextmanager
def capture_executemany(engine):
    calls = []

    def after_cursor_execute(
        connection, cursor, statement, parameters, context, executemany
    ):
        calls.append(
            {
                "statement": statement,
                "executemany": executemany,
                "rowcount": cursor.rowcount,
                "executed": cursor._executed,
            }
        )

    event.listen(engine, "after_cursor_execute", after_cursor_execute)
    try:
        yield calls
    finally:
        event.remove(engine, "after_cursor_execute", after_cursor_execute)


def assert_executemany_call(calls, operation, rowcount):
    calls = [
        call
        for call in calls
        if call["statement"].lstrip().upper().startswith(operation)
    ]
    assert len(calls) == 1
    assert calls[0]["executemany"] is True
    assert calls[0]["rowcount"] == rowcount
    # The ORM passed one statement template to DB-API executemany(), while
    # mysqlclient sent the rendered statements in one multi-statement query.
    assert b";" in calls[0]["executed"]


def test_connect_args_enable_multi_fallback_and_found_rows(engine):
    with engine.connect() as connection:
        driver_connection = connection.connection.driver_connection
        assert driver_connection.executemany_fallback == "multi"
        assert driver_connection.client_flag & CLIENT.FOUND_ROWS


def test_bulk_update_mappings_uses_executemany(engine):
    reset_rows(engine)

    with capture_executemany(engine) as calls, Session(engine) as session:
        session.bulk_update_mappings(
            BulkRow,
            [
                {"id": 1, "value": 10},  # no-op; FOUND_ROWS still counts it
                {"id": 2, "value": 21},
            ],
        )
        session.commit()

    assert_executemany_call(calls, "UPDATE ", 2)


def test_orm_bulk_update_by_primary_key_uses_executemany(engine):
    reset_rows(engine)

    with capture_executemany(engine) as calls, Session(engine) as session:
        session.execute(
            update(BulkRow),
            [
                {"id": 1, "value": 11},
                {"id": 2, "value": 22},
            ],
        )
        session.commit()

    assert_executemany_call(calls, "UPDATE ", 2)


def test_core_executemany_delete_rowcount(engine):
    reset_rows(engine)

    with capture_executemany(engine) as calls, engine.begin() as connection:
        result = connection.execute(
            delete(BulkRow).where(BulkRow.id == bindparam("target_id")),
            [{"target_id": 1}, {"target_id": 99}, {"target_id": 3}],
        )
        assert result.rowcount == 2

    assert_executemany_call(calls, "DELETE ", 2)
    with engine.connect() as connection:
        assert connection.scalars(select(BulkRow.id)).all() == [2]
