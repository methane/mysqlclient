import pytest
import MySQLdb.cursors
from MySQLdb._exceptions import IntegrityError, InternalError, OperationalError
from MySQLdb.converters import conversions
from configdb import connection_factory


_conns = []
_tables = []


def connect(**kwargs):
    conn = connection_factory(**kwargs)
    _conns.append(conn)
    return conn


def teardown_function(function):
    if _tables:
        c = _conns[0]
        cur = c.cursor()
        for t in _tables:
            cur.execute(f"DROP TABLE {t}")
        cur.close()
        del _tables[:]

    for c in _conns:
        c.close()
    del _conns[:]


def test_executemany():
    conn = connect()
    cursor = conn.cursor()

    cursor.execute("create table test (data varchar(10))")
    _tables.append("test")

    m = MySQLdb.cursors.RE_INSERT_VALUES.match(
        "INSERT INTO TEST (ID, NAME) VALUES (%s, %s)"
    )
    assert m is not None, "error parse %s"
    assert m.group(3) == "", "group 3 not blank, bug in RE_INSERT_VALUES?"

    m = MySQLdb.cursors.RE_INSERT_VALUES.match(
        "INSERT INTO TEST (ID, NAME) VALUES (%(id)s, %(name)s)"
    )
    assert m is not None, "error parse %(name)s"
    assert m.group(3) == "", "group 3 not blank, bug in RE_INSERT_VALUES?"

    m = MySQLdb.cursors.RE_INSERT_VALUES.match(
        "INSERT INTO TEST (ID, NAME) VALUES (%(id_name)s, %(name)s)"
    )
    assert m is not None, "error parse %(id_name)s"
    assert m.group(3) == "", "group 3 not blank, bug in RE_INSERT_VALUES?"

    m = MySQLdb.cursors.RE_INSERT_VALUES.match(
        "INSERT INTO TEST (ID, NAME) VALUES (%(id_name)s, %(name)s) ON duplicate update"
    )
    assert m is not None, "error parse %(id_name)s"
    assert (
        m.group(3) == " ON duplicate update"
    ), "group 3 not ON duplicate update, bug in RE_INSERT_VALUES?"

    # https://github.com/PyMySQL/mysqlclient-python/issues/178
    m = MySQLdb.cursors.RE_INSERT_VALUES.match(
        "INSERT INTO bloup(foo, bar)VALUES(%s, %s)"
    )
    assert m is not None

    # cursor._executed myst bee
    # """
    # insert into test (data)
    # values (0),(1),(2),(3),(4),(5),(6),(7),(8),(9)
    # """
    # list args
    data = [(i,) for i in range(10)]
    cursor.executemany("insert into test (data) values (%s)", data)
    assert cursor._executed.endswith(
        b",(7),(8),(9)"
    ), "execute many with %s not in one query"

    # bytes and bytearray queries use the same INSERT/REPLACE fast path.
    cursor.executemany(b"insert into test (data) values (%s)", [(10,), (11,)])
    assert cursor._executed.endswith(b"(10),(11)")
    cursor.executemany(
        bytearray(b"insert into test (data) values (%s)"), [(12,), (13,)]
    )
    assert cursor._executed.endswith(b"(12),(13)")

    # dict args
    data_dict = [{"data": i} for i in range(10)]
    cursor.executemany("insert into test (data) values (%(data)s)", data_dict)
    assert cursor._executed.endswith(
        b",(7),(8),(9)"
    ), "execute many with %(data)s not in one query"

    # %% in column set
    cursor.execute(
        """\
        CREATE TABLE percent_test (
            `A%` INTEGER,
            `B%` INTEGER)"""
    )
    try:
        q = "INSERT INTO percent_test (`A%%`, `B%%`) VALUES (%s, %s)"
        assert MySQLdb.cursors.RE_INSERT_VALUES.match(q) is not None
        cursor.executemany(q, [(3, 4), (5, 6)])
        assert cursor._executed.endswith(
            b"(3, 4),(5, 6)"
        ), "executemany with %% not in one query"
    finally:
        cursor.execute("DROP TABLE IF EXISTS percent_test")


@pytest.mark.parametrize(
    "Cursor", [MySQLdb.cursors.Cursor, MySQLdb.cursors.SSCursor]
)
def test_executemany_multi_update(Cursor):
    conn = connect(executemany_fallback="multi")
    cursor = conn.cursor(Cursor)
    cursor.execute(
        "CREATE TABLE executemany_multi_update "
        "(id int primary key, data varchar(100))"
    )
    _tables.append("executemany_multi_update")
    cursor.executemany(
        "INSERT INTO executemany_multi_update (id, data) VALUES (%s, %s)",
        [(1, 0), (2, 0), (3, 0)],
    )
    assert MySQLdb.cursors._EXECUTEMANY_MULTI_SEPARATOR not in cursor._executed
    assert b"),(" in cursor._executed

    rows = cursor.executemany(
        "UPDATE executemany_multi_update "
        "SET data=%(data)s WHERE id=%(id)s",
        [
            {"id": 1, "data": "ten;still-a-value"},
            {"id": 2, "data": "twenty"},
            {"id": 3, "data": "thirty"},
        ],
    )

    assert rows == 3
    assert cursor.rowcount == 3
    assert cursor.description is None
    assert cursor._executed.count(MySQLdb.cursors._EXECUTEMANY_MULTI_SEPARATOR) == 2
    assert conn.affected_rows() == 1
    assert conn.warning_count() == 0
    assert conn.more_results() is False

    cursor.execute("SELECT id, data FROM executemany_multi_update ORDER BY id")
    assert cursor.fetchall() == (
        (1, "ten;still-a-value"),
        (2, "twenty"),
        (3, "thirty"),
    )


def test_executemany_multi_delete():
    conn = connect(executemany_fallback="multi")
    cursor = conn.cursor()
    cursor.execute(
        "CREATE TABLE executemany_multi_delete (id int primary key, data int)"
    )
    _tables.append("executemany_multi_delete")
    cursor.executemany(
        "INSERT INTO executemany_multi_delete (id, data) VALUES (%s, %s)",
        [(1, 10), (2, 20), (3, 30)],
    )

    assert (
        cursor.executemany(
            "DELETE FROM executemany_multi_delete WHERE id=%s", [(1,), (3,)]
        )
        == 2
    )
    assert cursor.rowcount == 2
    assert cursor._executed.count(MySQLdb.cursors._EXECUTEMANY_MULTI_SEPARATOR) == 1
    assert conn.affected_rows() == 1
    cursor.execute("SELECT id FROM executemany_multi_delete")
    assert cursor.fetchall() == ((2,),)


def test_executemany_multi_keeps_last_statement_metadata():
    conn = connect(executemany_fallback="multi")
    cursor = conn.cursor()
    cursor.execute(
        "CREATE TABLE executemany_multi_metadata "
        "(id int primary key auto_increment, data varchar(1))"
    )
    _tables.append("executemany_multi_metadata")

    assert (
        cursor.executemany(
            "INSERT IGNORE INTO executemany_multi_metadata SET data=%s",
            [("a",), ("b",), ("too long",)],
        )
        == 3
    )
    assert cursor.rowcount == 3
    assert cursor.lastrowid == 3
    assert conn.insert_id() == 3
    assert conn.affected_rows() == 1
    assert conn.warning_count() > 0
    assert conn.more_results() is False


def test_executemany_multi_policy_and_capability():
    conn = connect()
    cursor = conn.cursor()
    cursor.execute(
        "CREATE TABLE executemany_multi_policy (id int primary key, data int)"
    )
    _tables.append("executemany_multi_policy")
    cursor.executemany(
        "INSERT INTO executemany_multi_policy (id, data) VALUES (%s, %s)",
        [(1, 0), (2, 0)],
    )

    query = "UPDATE executemany_multi_policy SET data=%s WHERE id=%s"
    cursor.executemany(query, [(10, 1), (20, 2)])
    assert MySQLdb.cursors._EXECUTEMANY_MULTI_SEPARATOR not in cursor._executed

    class MultiCursor(MySQLdb.cursors.Cursor):
        executemany_fallback = "multi"

    subclass_cursor = conn.cursor(MultiCursor)
    subclass_cursor.executemany(query, [(11, 1), (21, 2)])
    assert (
        subclass_cursor._executed.count(
            MySQLdb.cursors._EXECUTEMANY_MULTI_SEPARATOR
        )
        == 1
    )

    cursor.executemany_fallback = "multi"
    cursor.executemany(query, [(12, 1), (22, 2)])
    assert cursor._executed.count(MySQLdb.cursors._EXECUTEMANY_MULTI_SEPARATOR) == 1
    assert cursor.executemany(
        query + " -- trailing comment", [(13, 1), (23, 2)]
    ) == 2
    assert cursor._executed.count(MySQLdb.cursors._EXECUTEMANY_MULTI_SEPARATOR) == 1

    cursor.executemany_fallback = "loop"
    cursor.executemany(query, [(14, 1), (24, 2)])
    assert MySQLdb.cursors._EXECUTEMANY_MULTI_SEPARATOR not in cursor._executed

    with pytest.raises(ValueError, match="executemany_fallback"):
        cursor.executemany_fallback = "invalid"
        cursor.executemany(query, [(15, 1), (25, 2)])

    conn.commit()
    no_multi_conn = connect(
        executemany_fallback="multi", multi_statements=False
    )
    no_multi_cursor = no_multi_conn.cursor()
    no_multi_cursor.executemany(query, [(16, 1), (26, 2)])
    assert (
        MySQLdb.cursors._EXECUTEMANY_MULTI_SEPARATOR
        not in no_multi_cursor._executed
    )
    no_multi_conn.rollback()


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("UPDATE t SET value=%s", True),
        (b"DELETE FROM t WHERE id=%s", True),
        ("INSERT INTO t SET value=%s", True),
        ("REPLACE INTO t SET value=%s", True),
        ("WITH values_ AS (SELECT 1) UPDATE t SET value=%s", False),
        ("UPDATE t SET value=%s;", False),
        ("UPDATE t SET value=%s RETURNING id", False),
        ("SELECT %s", False),
        ("/* comment */ UPDATE t SET value=%s", False),
    ],
)
def test_is_executemany_dml(query, expected):
    assert MySQLdb.cursors._is_executemany_dml(query) is expected


def test_executemany_multi_batch_limits_and_single_arg():
    class RecordingCursor(MySQLdb.cursors.Cursor):
        max_multi_stmt_length = 1_000_000
        max_multi_stmt_count = 2

        def __init__(self, connection):
            super().__init__(connection)
            self.execute_calls = []

        def execute(self, query, args=None):
            self.execute_calls.append((query, args))
            return super().execute(query, args)

    conn = connect(executemany_fallback="multi")
    cursor = conn.cursor(RecordingCursor)
    cursor.execute(
        "CREATE TABLE executemany_multi_limits "
        "(id int primary key, data text)"
    )
    _tables.append("executemany_multi_limits")
    cursor.executemany(
        "INSERT INTO executemany_multi_limits (id, data) VALUES (%s, %s)",
        [(i, 0) for i in range(1, 7)],
    )

    query = "UPDATE executemany_multi_limits SET data=%s WHERE id=%s"
    cursor.execute_calls.clear()
    assert cursor.executemany(query, [(i * 10, i) for i in range(1, 6)]) == 5
    assert len(cursor.execute_calls) == 3
    assert [
        bytes(q).count(MySQLdb.cursors._EXECUTEMANY_MULTI_SEPARATOR)
        for q, args in cursor.execute_calls
    ] == [1, 1, 0]
    assert all(args is None for query, args in cursor.execute_calls)
    assert cursor.rowcount == 5
    assert conn.affected_rows() == 1

    first_arg = ("a", 1)
    second_base_arg = ("", 2)
    first_statement = cursor._mogrify(query, first_arg)
    second_base_statement = cursor._mogrify(query, second_base_arg)
    filler_length = (
        16_000
        - len(first_statement)
        - len(MySQLdb.cursors._EXECUTEMANY_MULTI_SEPARATOR)
        - len(second_base_statement)
    )
    boundary_args = [first_arg, ("x" * filler_length, 2), ("c", 3)]
    cursor.max_multi_stmt_count = 200
    cursor.max_multi_stmt_length = 16_000
    cursor.execute_calls.clear()
    assert cursor.executemany(query, boundary_args) == 3
    assert len(cursor.execute_calls) == 2
    assert len(cursor.execute_calls[0][0]) == 16_000

    cursor.max_multi_stmt_length = 1_000_000
    cursor.max_multi_stmt_count = 200
    cursor.execute_calls.clear()
    assert (
        cursor.executemany(
            "DELETE FROM executemany_multi_limits WHERE id=%s",
            ((1000 + i,) for i in range(201)),
        )
        == 0
    )
    assert len(cursor.execute_calls) == 2
    assert [
        bytes(q).count(MySQLdb.cursors._EXECUTEMANY_MULTI_SEPARATOR)
        for q, args in cursor.execute_calls
    ] == [199, 0]

    cursor.execute_calls.clear()
    arg = (60, 6)
    assert cursor.executemany(query, [arg]) == 1
    assert cursor.execute_calls == [(query, arg)]

    assert MySQLdb.cursors.BaseCursor.max_multi_stmt_length == 16_000
    assert MySQLdb.cursors.BaseCursor.max_multi_stmt_count == 200


def test_executemany_multi_oversized_statement_runs_alone():
    class TinyBatchCursor(MySQLdb.cursors.Cursor):
        max_multi_stmt_length = 1

        def __init__(self, connection):
            super().__init__(connection)
            self.execute_calls = []

        def execute(self, query, args=None):
            self.execute_calls.append((query, args))
            return super().execute(query, args)

    conn = connect(executemany_fallback="multi")
    cursor = conn.cursor(TinyBatchCursor)
    cursor.execute(
        "CREATE TABLE executemany_multi_oversized (id int primary key, data int)"
    )
    _tables.append("executemany_multi_oversized")
    cursor.execute_calls.clear()

    query = "UPDATE executemany_multi_oversized SET data=%s WHERE id=%s"
    cursor.executemany(query, [(10, 1), (20, 2)])
    assert len(cursor.execute_calls) == 2
    assert all(
        MySQLdb.cursors._EXECUTEMANY_MULTI_SEPARATOR not in bytes(q)
        for q, args in cursor.execute_calls
    )


def test_executemany_multi_generator_and_empty_iterator():
    conn = connect(executemany_fallback="multi")
    cursor = conn.cursor()
    cursor.execute(
        "CREATE TABLE executemany_multi_generator (id int primary key, data int)"
    )
    _tables.append("executemany_multi_generator")
    cursor.executemany(
        "INSERT INTO executemany_multi_generator (id, data) VALUES (%s, %s)",
        [(1, 0), (2, 0), (3, 0)],
    )

    def params():
        for i in range(1, 4):
            yield (i * 10, i)

    query = "UPDATE executemany_multi_generator SET data=%s WHERE id=%s"
    assert cursor.executemany(query, params()) == 3
    assert cursor._executed.count(MySQLdb.cursors._EXECUTEMANY_MULTI_SEPARATOR) == 2
    assert cursor.executemany(query, iter(())) == 0
    assert cursor.rowcount == 0

    cursor.execute("SELECT id, data FROM executemany_multi_generator ORDER BY id")
    assert cursor.fetchall() == ((1, 10), (2, 20), (3, 30))


@pytest.mark.parametrize(
    ("args", "expected_ids"),
    [
        ([(99, 10), (1, 10), (2, 20)], (99,)),
        ([(1, 10), (99, 10), (2, 20)], (1, 99)),
        ([(1, 10), (2, 20), (99, 10)], (1, 2, 99)),
    ],
)
def test_executemany_multi_sql_error(args, expected_ids):
    conn = connect(executemany_fallback="multi")
    cursor = conn.cursor()
    cursor.execute(
        "CREATE TABLE executemany_multi_error (id int primary key, data int)"
    )
    _tables.append("executemany_multi_error")
    cursor.execute("INSERT INTO executemany_multi_error VALUES (99, 0)")

    with pytest.raises(IntegrityError):
        cursor.executemany(
            "INSERT INTO executemany_multi_error SET id=%s, data=%s", args
        )

    assert cursor.rowcount is None
    assert conn.open
    assert conn.more_results() is False
    cursor.execute("SELECT id FROM executemany_multi_error ORDER BY id")
    assert tuple(row[0] for row in cursor.fetchall()) == expected_ids


@pytest.mark.parametrize(
    "Cursor", [MySQLdb.cursors.Cursor, MySQLdb.cursors.SSCursor]
)
def test_executemany_multi_rejects_unexpected_result_count(Cursor):
    class RawSQL:
        pass

    def raw_sql_literal(value, conv):
        return b"1; SELECT 1"

    cleanup_conn = connect()
    cleanup_cursor = cleanup_conn.cursor()
    cleanup_cursor.execute(
        "CREATE TABLE executemany_multi_result_count (id int primary key, data int)"
    )
    _tables.append("executemany_multi_result_count")
    cleanup_cursor.execute(
        "INSERT INTO executemany_multi_result_count VALUES (1, 0)"
    )
    cleanup_conn.commit()

    custom_conversions = conversions.copy()
    custom_conversions[RawSQL] = raw_sql_literal
    conn = connect(executemany_fallback="multi", conv=custom_conversions)
    cursor = conn.cursor(Cursor)

    with pytest.raises(InternalError, match="multi-statement executemany"):
        cursor.executemany(
            "UPDATE executemany_multi_result_count SET data=%s",
            [(RawSQL(),), (RawSQL(),)],
        )

    assert not conn.open
    _conns.remove(conn)


@pytest.mark.parametrize(
    "failure", [KeyboardInterrupt(), OperationalError(2013, "server lost")]
)
def test_executemany_multi_drain_failure_closes_connection(failure):
    class FailingCursor(MySQLdb.cursors.Cursor):
        armed = False
        result_number = 0

        def _do_get_result(self, db):
            super()._do_get_result(db)
            if self.armed:
                self.result_number += 1
                if self.result_number == 2:
                    raise failure

    cleanup_conn = connect()
    cleanup_cursor = cleanup_conn.cursor()
    cleanup_cursor.execute(
        "CREATE TABLE executemany_multi_drain_failure "
        "(id int primary key, data int)"
    )
    _tables.append("executemany_multi_drain_failure")
    cleanup_cursor.execute(
        "INSERT INTO executemany_multi_drain_failure VALUES (1, 0), (2, 0)"
    )
    cleanup_conn.commit()

    conn = connect(executemany_fallback="multi")
    cursor = conn.cursor(FailingCursor)
    cursor.armed = True
    with pytest.raises(type(failure)) as exc_info:
        cursor.executemany(
            "UPDATE executemany_multi_drain_failure SET data=%s WHERE id=%s",
            [(10, 1), (20, 2)],
        )

    assert exc_info.value is failure
    assert not conn.open
    _conns.remove(conn)


def test_pyparam():
    conn = connect()
    cursor = conn.cursor()

    cursor.execute("SELECT %(a)s, %(b)s", {"a": 1, "b": 2})
    assert cursor._executed == b"SELECT 1, 2"
    cursor.execute(b"SELECT %(a)s, %(b)s", {b"a": 3, b"b": 4})
    assert cursor._executed == b"SELECT 3, 4"


def test_dictcursor():
    conn = connect()
    cursor = conn.cursor(MySQLdb.cursors.DictCursor)

    cursor.execute("CREATE TABLE t1 (a int, b int, c int)")
    _tables.append("t1")
    cursor.execute("INSERT INTO t1 (a,b,c) VALUES (1,1,47), (2,2,47)")

    cursor.execute("CREATE TABLE t2 (b int, c int)")
    _tables.append("t2")
    cursor.execute("INSERT INTO t2 (b,c) VALUES (1,1), (2,2)")

    cursor.execute("SELECT * FROM t1 JOIN t2 ON t1.b=t2.b")
    rows = cursor.fetchall()

    assert len(rows) == 2
    assert rows[0] == {"a": 1, "b": 1, "c": 47, "t2.b": 1, "t2.c": 1}
    assert rows[1] == {"a": 2, "b": 2, "c": 47, "t2.b": 2, "t2.c": 2}

    names1 = sorted(rows[0])
    names2 = sorted(rows[1])
    for a, b in zip(names1, names2):
        assert a is b

    # Old fetchtype
    cursor._fetch_type = 2
    cursor.execute("SELECT * FROM t1 JOIN t2 ON t1.b=t2.b")
    rows = cursor.fetchall()

    assert len(rows) == 2
    assert rows[0] == {"t1.a": 1, "t1.b": 1, "t1.c": 47, "t2.b": 1, "t2.c": 1}
    assert rows[1] == {"t1.a": 2, "t1.b": 2, "t1.c": 47, "t2.b": 2, "t2.c": 2}

    names1 = sorted(rows[0])
    names2 = sorted(rows[1])
    for a, b in zip(names1, names2):
        assert a is b


def test_mogrify_without_args():
    conn = connect()
    cursor = conn.cursor()

    query = "SELECT VERSION()"
    mogrified_query = cursor.mogrify(query)
    cursor.execute(query)

    assert mogrified_query == query
    assert mogrified_query == cursor._executed.decode()


def test_mogrify_with_tuple_args():
    conn = connect()
    cursor = conn.cursor()

    query_with_args = "SELECT %s, %s", (1, 2)
    mogrified_query = cursor.mogrify(*query_with_args)
    cursor.execute(*query_with_args)

    assert mogrified_query == "SELECT 1, 2"
    assert mogrified_query == cursor._executed.decode()


def test_mogrify_with_dict_args():
    conn = connect()
    cursor = conn.cursor()

    query_with_args = "SELECT %(a)s, %(b)s", {"a": 1, "b": 2}
    mogrified_query = cursor.mogrify(*query_with_args)
    cursor.execute(*query_with_args)

    assert mogrified_query == "SELECT 1, 2"
    assert mogrified_query == cursor._executed.decode()


# Test that cursor can be used without reading whole resultset.
@pytest.mark.parametrize("Cursor", [MySQLdb.cursors.Cursor, MySQLdb.cursors.SSCursor])
def test_cursor_discard_result(Cursor):
    conn = connect()
    cursor = conn.cursor(Cursor)

    cursor.execute(
        """\
CREATE TABLE test_cursor_discard_result (
    id INTEGER PRIMARY KEY AUTO_INCREMENT,
    data VARCHAR(100)
)"""
    )
    _tables.append("test_cursor_discard_result")

    cursor.executemany(
        "INSERT INTO test_cursor_discard_result (id, data) VALUES (%s, %s)",
        [(i, f"row {i}") for i in range(1, 101)],
    )

    cursor.execute(
        """\
SELECT * FROM test_cursor_discard_result WHERE id <= 10;
SELECT * FROM test_cursor_discard_result WHERE id BETWEEN 11 AND 20;
SELECT * FROM test_cursor_discard_result WHERE id BETWEEN 21 AND 30;
"""
    )
    cursor.nextset()
    assert cursor.fetchone() == (11, "row 11")

    cursor.execute(
        "SELECT * FROM test_cursor_discard_result WHERE id BETWEEN 31 AND 40"
    )
    assert cursor.fetchone() == (31, "row 31")


def test_binary_prefix():
    # https://github.com/PyMySQL/mysqlclient/issues/494
    conn = connect(binary_prefix=True)
    cursor = conn.cursor()

    cursor.execute("DROP TABLE IF EXISTS test_binary_prefix")
    cursor.execute(
        """\
CREATE TABLE test_binary_prefix (
	id INTEGER NOT NULL AUTO_INCREMENT,
	json JSON NOT NULL,
	PRIMARY KEY (id)
) CHARSET=utf8mb4"""
    )

    cursor.executemany(
        "INSERT INTO test_binary_prefix (id, json) VALUES (%(id)s, %(json)s)",
        ({"id": 1, "json": "{}"}, {"id": 2, "json": "{}"}),
    )
