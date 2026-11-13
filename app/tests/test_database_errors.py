import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch
import oracledb
from fastapi.testclient import TestClient
from lasttable import api

class DatabaseErrorTests(unittest.TestCase):
    def test_database_failure_returns_retryable_json_without_internal_details(self):
        @contextmanager
        def unavailable(*args):
            raise oracledb.DatabaseError(SimpleNamespace(code=1536, message='private database detail'))
            yield
        with patch.object(api,'database',unavailable):
            response=TestClient(api.app).get('/api/restaurants')
        self.assertEqual(response.status_code,503)
        self.assertEqual(response.headers['retry-after'],'30')
        self.assertIn('Riprova',response.json()['detail'])
        self.assertNotIn('private',response.text)
        self.assertNotIn('1536',response.text)

if __name__=='__main__':unittest.main()
