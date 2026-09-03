import pytest

from MySQLdb.connections import Connection
from MySQLdb._exceptions import ProgrammingError

from configdb import connection_factory, connection_kwargs


def test_multi_statements_default_true():
    conn = connection_factory()
    cursor = conn.cursor()

    cursor.execute("select 17; select 2")
    assert conn.more_results() is True
    rows = cursor.fetchall()
    assert rows == ((17,),)
    assert cursor.nextset() == 1
    assert conn.more_results() is False


def test_multi_statements_false():
    conn = connection_factory(multi_statements=False)
    cursor = conn.cursor()
    assert conn._executemany_multi_enabled is False

    with pytest.raises(ProgrammingError):
        cursor.execute("select 17; select 2")

    cursor.execute("select 17")
    rows = cursor.fetchall()
    assert rows == ((17,),)


def test_executemany_fallback_option():
    with connection_factory() as conn:
        assert conn.executemany_fallback == "loop"

    with connection_factory(executemany_fallback="multi") as conn:
        assert conn.executemany_fallback == "multi"

    with pytest.raises(ValueError, match="executemany_fallback"):
        connection_factory(executemany_fallback="invalid")


def test_executemany_fallback_connection_subclass_default():
    class MultiConnection(Connection):
        executemany_fallback = "multi"

    with MultiConnection(**connection_kwargs({})) as conn:
        assert conn.executemany_fallback == "multi"

    with MultiConnection(
        **connection_kwargs({"executemany_fallback": "loop"})
    ) as conn:
        assert conn.executemany_fallback == "loop"


def test_set_server_option_disables_executemany_multi():
    with connection_factory() as conn:
        assert conn._executemany_multi_enabled is True
        conn.set_server_option(1)  # MYSQL_OPTION_MULTI_STATEMENTS_OFF
        assert conn._executemany_multi_enabled is False
        conn.set_server_option(0)  # MYSQL_OPTION_MULTI_STATEMENTS_ON
        assert conn._executemany_multi_enabled is False

        cursor = conn.cursor()
        cursor.execute("select 1; select 2")
        assert cursor.fetchone() == (1,)
        assert cursor.nextset() == 1
        assert cursor.fetchone() == (2,)
