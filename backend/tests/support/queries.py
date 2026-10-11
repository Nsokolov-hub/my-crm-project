"""Count SQL statements to keep list endpoints free of per-row queries."""

from sqlalchemy import event


def statements_while(env, action) -> list[str]:
    engine = env["sessions"].kw["bind"]
    seen: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        action()
    finally:
        event.remove(engine, "before_cursor_execute", record)
    return seen


def query_count(env, path) -> int:
    def fetch():
        response = env["client"].get("/api/v1" + path)
        assert response.status_code == 200, response.text

    return len(statements_while(env, fetch))
