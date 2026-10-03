"""Durable per-developer token grants for the IT-owned management service.

The service reserves a trusted input-token upper bound plus the output cap before
each provider attempt. Requests whose input cannot be bounded must not use a
finite grant. Unsettled reservations remain charged after a crash or uncertain
failure; retrying requires a new reservation. No UI or project process writes
this database. A new administrator-issued grant ID is the only reset mechanism.
"""

from contextlib import contextmanager
import os
from pathlib import Path
import sqlite3
import uuid

from .enterprise_policy import ADMIN_UID, Developer, trusted_path


class QuotaExceeded(PermissionError):
    pass


class QuotaLedger:
    def __init__(self, path):
        self.path = Path(path).absolute()
        if os.geteuid() != ADMIN_UID:
            raise PermissionError("Quota state is writable only by the IT management service.")
        trusted_path(self.path.parent, directory=True, private=True)
        try:
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(fd)
        trusted_path(self.path, private=True)
        with self._transaction() as database:
            database.executescript("""
                CREATE TABLE IF NOT EXISTS grants (
                    developer TEXT NOT NULL, grant_id TEXT NOT NULL,
                    suspended INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (developer, grant_id)
                );
                CREATE TABLE IF NOT EXISTS reservations (
                    id TEXT PRIMARY KEY, developer TEXT NOT NULL, grant_id TEXT NOT NULL,
                    reserved INTEGER NOT NULL, charged INTEGER NOT NULL,
                    settled INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS reservation_grant ON reservations (developer, grant_id);
            """)

    @contextmanager
    def _transaction(self):
        trusted_path(self.path, private=True)
        database = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        try:
            database.execute("PRAGMA synchronous=FULL")
            database.execute("BEGIN IMMEDIATE")
            yield database
            if database.in_transaction:
                database.execute("COMMIT")
        except BaseException:
            if database.in_transaction:
                database.execute("ROLLBACK")
            raise
        finally:
            database.close()

    @staticmethod
    def _count(value):
        # Keep additions safely within SQLite's signed 64-bit integer range.
        if type(value) is not int or not 0 <= value <= 10**12:
            raise ValueError("Token count must be a nonnegative bounded integer.")
        return value

    def reserve(self, developer: Developer, input_bound, output_cap):
        if not developer.enabled:
            raise PermissionError("Disabled developers cannot reserve quota.")
        amount = self._count(input_bound) + self._count(output_cap)
        if amount == 0:
            raise ValueError("A request reservation must be positive.")
        identifier = uuid.uuid4().hex
        with self._transaction() as database:
            key = (developer.object_id, developer.grant)
            database.execute("INSERT OR IGNORE INTO grants (developer, grant_id) VALUES (?, ?)", key)
            suspended = database.execute(
                "SELECT suspended FROM grants WHERE developer=? AND grant_id=?", key).fetchone()[0]
            used = database.execute(
                "SELECT COALESCE(SUM(charged), 0) FROM reservations WHERE developer=? AND grant_id=?", key).fetchone()[0]
            if suspended:
                raise QuotaExceeded("Quota grant requires IT review after invalid provider accounting.")
            if developer.token_limit is not None and used + amount > developer.token_limit:
                raise QuotaExceeded("Developer token limit reached; company IT must change or reset the grant.")
            database.execute("INSERT INTO reservations VALUES (?, ?, ?, ?, ?, 0)",
                             (identifier, *key, amount, amount))
        return identifier

    def settle(self, identifier, input_tokens, output_tokens):
        """Commit valid actual usage once; leave missing/uncertain usage charged.

        Provider usage above its established bound is an accounting fault. Record
        the actual charge and suspend the grant instead of hiding the overrun or
        permitting further requests. Callers must report the failed bound.
        """
        actual = self._count(input_tokens) + self._count(output_tokens)
        violation = False
        with self._transaction() as database:
            row = database.execute(
                "SELECT developer, grant_id, reserved, charged, settled FROM reservations WHERE id=?",
                (identifier,)).fetchone()
            if row is None:
                raise ValueError("Unknown quota reservation.")
            developer, grant, reserved, charged, settled = row
            if settled:
                if actual != charged:
                    raise ValueError("Quota reservation already settled with different usage.")
                return
            database.execute("UPDATE reservations SET charged=?, settled=1 WHERE id=?", (actual, identifier))
            if actual > reserved:
                database.execute("UPDATE grants SET suspended=1 WHERE developer=? AND grant_id=?", (developer, grant))
                violation = True
        if violation:
            raise QuotaExceeded("Provider exceeded its token bound; grant suspended for IT review.")

    def status(self, developer: Developer):
        with self._transaction() as database:
            key = (developer.object_id, developer.grant)
            charged, pending = database.execute(
                "SELECT COALESCE(SUM(charged), 0), COALESCE(SUM(1-settled), 0) "
                "FROM reservations WHERE developer=? AND grant_id=?", key).fetchone()
            row = database.execute("SELECT suspended FROM grants WHERE developer=? AND grant_id=?", key).fetchone()
        return {"limit": developer.token_limit, "charged": charged, "pending_requests": pending,
                "grant": developer.grant, "suspended": bool(row and row[0]),
                "remaining": None if developer.token_limit is None else max(0, developer.token_limit - charged)}
