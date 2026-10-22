import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from game.mlflow_tracking import local_uri, TrackingClient
from game.server import create_app, DemoClient
from tools.container_entrypoint import prepare_token


class ContainerConfigurationTests(unittest.TestCase):
    def test_operator_key_is_created_once_and_survives_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'ops-token'
            prepare_token(path)
            first = path.read_text()
            self.assertGreaterEqual(len(first.strip()), 32)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            prepare_token(path)
            self.assertEqual(path.read_text(), first)
            path.write_text('bad')
            with self.assertRaises(ValueError): prepare_token(path)
            self.assertEqual(path.read_text(), 'bad')

    def test_container_peer_needs_key_and_still_checks_origin(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'ops-token'; prepare_token(path)
            token = path.read_text().strip()
            with patch.dict(os.environ, {'GAME_OPS_TOKEN_FILE': str(path), 'GAME_MLFLOW_PUBLIC_URI': 'http://127.0.0.1:5600'}):
                app = create_app(':memory:', DemoClient, auto_workers=False)
            with TestClient(app, base_url='http://127.0.0.1:8300', client=('172.20.0.1', 1234)) as client:
                self.assertEqual(client.get('/ops').status_code, 200)
                self.assertEqual(client.get('/api/ops').status_code, 401)
                self.assertEqual(client.get('/api/ops', headers={'X-Ops-Token':'bad'}).status_code, 401)
                headers = {'X-Ops-Token': token}
                response = client.get('/api/ops', headers=headers)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers['cache-control'], 'no-store')
                self.assertEqual(response.json()['mlflow']['public_uri'], 'http://127.0.0.1:5600')
                self.assertNotIn(token, response.text)
                for extra in ({'Origin':'https://evil.example'}, {'Host':'evil.example'}, {'X-Forwarded-For':'127.0.0.1'}, {'Sec-Fetch-Site':'cross-site'}):
                    self.assertEqual(client.get('/api/ops', headers={**headers, **extra}).status_code, 403)

    def test_missing_or_invalid_token_file_prevents_startup(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'missing'
            with patch.dict(os.environ, {'GAME_OPS_TOKEN_FILE':str(path)}):
                with self.assertRaises(FileNotFoundError): create_app(':memory:', DemoClient, auto_workers=False)
                path.write_text('short')
                with self.assertRaises(ValueError): create_app(':memory:', DemoClient, auto_workers=False)

    def test_internal_mlflow_endpoint_is_explicit_and_narrow(self):
        with self.assertRaises(ValueError): local_uri('http://mlflow:5500')
        self.assertEqual(local_uri('http://mlflow:5500', allow_compose=True), 'http://mlflow:5500')
        for uri in ('http://mlflow:5000', 'http://mlflow.evil:5500', 'http://user:secret@mlflow:5500',
                    'http://mlflow:5500/path', 'http://mlflow:5500?x=1', 'https://mlflow:5500', 'http://192.168.1.1:5500'):
            with self.assertRaises(ValueError): local_uri(uri, allow_compose=True)
        with patch.dict(os.environ, {'GAME_MLFLOW_ALLOW_COMPOSE':'true'}):
            client = TrackingClient('http://mlflow:5500')
            self.assertEqual(client.uri, 'http://mlflow:5500')
            client.close()

    def test_browser_mlflow_link_cannot_be_an_arbitrary_remote_origin(self):
        with patch.dict(os.environ, {'GAME_MLFLOW_PUBLIC_URI':'http://evil.example'}):
            with self.assertRaises(ValueError): create_app(':memory:', DemoClient, auto_workers=False)
