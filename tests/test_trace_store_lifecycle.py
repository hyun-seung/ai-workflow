import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import trace_store


class StoreInitializationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name).resolve() / 'trace.sqlite3'
        self.real_connect = sqlite3.connect

    def assert_failed_connection_closed(self, error, readonly=False, factory=None, authorizer=None):
        opened = []

        def connect(*args, **kwargs):
            if factory is not None:
                kwargs['factory'] = factory
            connection = self.real_connect(*args, **kwargs)
            opened.append(connection)
            if authorizer is not None:
                connection.set_authorizer(authorizer)
            return connection

        try:
            with patch.object(trace_store.sqlite3, 'connect', side_effect=connect):
                with self.assertRaises(error):
                    trace_store.Store(self.path, readonly=readonly)
            self.assertEqual(len(opened), 1)
            with self.assertRaises(sqlite3.ProgrammingError):
                opened[0].execute('SELECT 1')
        finally:
            # A failing regression must not itself leak the real connection.
            for connection in opened:
                connection.close()

    def test_corrupt_database_closes_connection_in_both_modes(self):
        self.path.write_bytes(b'not a sqlite database')
        self.path.chmod(0o600)
        for readonly in (False, True):
            with self.subTest(readonly=readonly):
                self.assert_failed_connection_closed(sqlite3.DatabaseError, readonly=readonly)
                self.assertEqual(self.path.read_bytes(), b'not a sqlite database')

    def test_schema_creation_error_closes_connection(self):
        def authorize(action, *args):
            return sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_CREATE_TABLE else sqlite3.SQLITE_OK

        self.assert_failed_connection_closed(sqlite3.DatabaseError, authorizer=authorize)

    def test_final_pragma_error_closes_connection(self):
        def authorize(action, name, *args):
            return sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_PRAGMA and name == 'query_only' else sqlite3.SQLITE_OK

        self.assert_failed_connection_closed(sqlite3.DatabaseError, authorizer=authorize)

    def test_interrupted_initialization_closes_connection_and_preserves_interrupt(self):
        class InterruptedConnection(sqlite3.Connection):
            def execute(self, sql, *args, **kwargs):
                if sql == 'PRAGMA user_version':
                    raise KeyboardInterrupt
                return super().execute(sql, *args, **kwargs)

        self.assert_failed_connection_closed(KeyboardInterrupt, factory=InterruptedConnection)

    def test_unsupported_database_closes_connection(self):
        connection = self.real_connect(self.path)
        try:
            connection.execute('PRAGMA user_version=99')
        finally:
            connection.close()
        self.path.chmod(0o600)
        self.assert_failed_connection_closed(ValueError)

    def test_successful_connection_remains_usable_until_explicit_close(self):
        for readonly in (False, True):
            with self.subTest(readonly=readonly):
                store = trace_store.Store(self.path, readonly=readonly)
                try:
                    self.assertEqual(store.db.execute('SELECT 1').fetchone(), (1,))
                finally:
                    store.close()
                with self.assertRaises(sqlite3.ProgrammingError):
                    store.db.execute('SELECT 1')


if __name__ == '__main__':
    unittest.main()
