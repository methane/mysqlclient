from test_mysql import *  # noqa: F403


for database in DATABASES.values():  # noqa: F405
    database.setdefault("OPTIONS", {})["executemany_fallback"] = "multi"
