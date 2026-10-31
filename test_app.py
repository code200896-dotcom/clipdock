import asyncio
import json
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient
import app
import worker


class WebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.multiple(app, PASSWORD='test-password-long-enough', DATA=Path(self.temp.name), busy=False)
        self.env.start()
        app.jobs.clear()
        self.client_context = TestClient(app.app)
        self.client = self.client_context.__enter__()
        self.auth = ('owner', app.PASSWORD)

    def tearDown(self):
        self.client_context.__exit__(None, None, None)
        self.env.stop()
        self.temp.cleanup()

    def test_password_and_static_routes(self):
        self.assertEqual(self.client.get('/health').status_code, 200)
        self.assertEqual(self.client.get('/').status_code, 401)
        self.assertEqual(self.client.get('/', auth=('owner', 'wrong')).status_code, 401)
        page = self.client.get('/', auth=self.auth)
        self.assertEqual(page.status_code, 200)
        self.assertIn('ClipDock', page.text)
        self.assertIn("frame-ancestors 'none'", page.headers['content-security-policy'])
        self.assertEqual(self.client.get('/app.js', auth=self.auth).status_code, 200)

    def test_input_validation(self):
        for body in ({'url':'file:///tmp/video.mp4'}, {'url':'https://example.org','height':999}, {'url':'https://user:pass@example.org/video'}):
            self.assertEqual(self.client.post('/api/jobs', json=body, auth=self.auth).status_code, 400)
        self.assertEqual(self.client.post('/api/jobs', json={'url':'https://example.org'}, auth=self.auth, headers={'sec-fetch-site':'cross-site'}).status_code, 403)

    def test_inspection_and_capacity_release(self):
        with patch.object(app, 'execute', AsyncMock(return_value={'title':'Example','kind':'info'})):
            response = self.client.post('/api/inspect', json={'url':'https://example.org/video'}, auth=self.auth)
            self.assertEqual(response.json()['title'], 'Example')
            self.assertFalse(app.busy)
        with patch.object(app, 'execute', AsyncMock(side_effect=ValueError('Source unavailable'))):
            self.assertEqual(self.client.post('/api/inspect', json={'url':'https://example.org/video'}, auth=self.auth).status_code, 422)
            self.assertFalse(app.busy)
        app.busy = True
        self.assertEqual(self.client.post('/api/jobs', json={'url':'https://example.org/video'}, auth=self.auth).status_code, 409)

    def test_files_and_status_are_protected(self):
        path = Path(self.temp.name) / 'sample.mp4'
        path.write_bytes(b'sample-video')
        app.jobs['test'] = {'status':'ready','file':str(path)}
        self.assertEqual(self.client.get('/files/test').status_code, 401)
        self.assertNotIn('file', self.client.get('/api/jobs/test', auth=self.auth).json())
        response = self.client.get('/files/test', auth=self.auth)
        self.assertEqual(response.content, b'sample-video')
        self.assertIn('attachment', response.headers['content-disposition'])
        self.assertEqual(self.client.get('/files/unknown', auth=self.auth).status_code, 404)
        self.assertEqual(self.client.get('/files/../../app.py', auth=self.auth).status_code, 404)

    def test_download_failure_releases_capacity_and_removes_files(self):
        app.jobs['job-test'] = {'status':'working'}
        app.busy = True
        with patch.object(app, 'execute', AsyncMock(side_effect=ValueError('Blocked by source'))):
            asyncio.run(app.download('job-test', app.Video(url='https://example.org/video')))
        self.assertEqual(app.jobs['job-test']['status'], 'error')
        self.assertFalse(app.busy)
        self.assertFalse((Path(self.temp.name) / 'job-test').exists())

    def test_download_success(self):
        app.jobs['job-test'] = {'status':'working'}
        async def fake(spec, callback, timeout):
            Path(spec['directory'], 'video.mp4').write_bytes(b'example')
            callback({'percent':100, 'message':'Done'})
        with patch.object(app, 'execute', fake):
            asyncio.run(app.download('job-test', app.Video(url='https://example.org/video')))
        self.assertEqual(app.jobs['job-test']['status'], 'ready')


class NetworkTests(unittest.TestCase):
    def test_private_and_metadata_addresses_blocked(self):
        for host in ('127.0.0.1','10.0.0.1','169.254.169.254','::1','fc00::1','0.0.0.0'):
            with self.assertRaises(ValueError): worker.public_address((host,443))

    def test_mixed_dns_answers_blocked(self):
        with patch.object(socket,'getaddrinfo',return_value=[(2,1,6,'',('8.8.8.8',443)),(2,1,6,'',('127.0.0.1',443))]):
            with self.assertRaises(ValueError): worker.public_address(('example.org',443))

    def test_public_address_and_protocols(self):
        self.assertEqual(worker.public_address(('8.8.8.8',443)), [('8.8.8.8',443)])
        for url in ('ftp://example.org/video','https://example.org:8080/video','https://a:b@example.org/video'):
            with self.assertRaises(ValueError): worker.public_url(url)


if __name__ == '__main__': unittest.main()
