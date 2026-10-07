"""Sample app for the acceptance test (quickstart.md, "The sample app fixture").

`GET /health` returns the port the server was told to use, the database it is connected to,
and the number of rows in `items`. It reads `PORT` and `DATABASE_URL` from the environment.
"""

import os

import psycopg
from fastapi import FastAPI

app = FastAPI()


@app.get("/health")
def health() -> dict[str, object]:
    """Report the port, the current database, and the row count of `items`."""
    with psycopg.connect(os.environ["DATABASE_URL"]) as connection:
        database = connection.execute("SELECT current_database()").fetchone()
        items = connection.execute("SELECT count(*) FROM items").fetchone()
    assert database is not None and items is not None
    return {"port": int(os.environ["PORT"]), "database": database[0], "items": items[0]}
