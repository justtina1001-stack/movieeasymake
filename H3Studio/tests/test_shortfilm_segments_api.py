import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from aiohttp.test_utils import TestClient, TestServer
from PIL import Image

import app as studio
from shortfilm import append_continuous_scene, new_project


class SegmentAPITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.paths = patch.multiple(studio, DATA_DIR=self.root, ASSET_DIR=self.root / 'assets',
                                    JOB_DIR=self.root / 'jobs', OUTPUT_DIR=self.root / 'outputs',
                                    CONFIG_PATH=self.root / 'config.json')
        self.paths.start()
        self.app = studio.create_app()
        # No engine starts, job recovery, gateway, or network model checks in tests.
        self.app.on_startup.clear()
        self.app.on_cleanup.clear()
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.paths.stop()
        self.temp.cleanup()

    async def test_append_is_atomic_and_preserves_old_project(self):
        project = self.app['shortfilms'].create(new_project('測試'))
        url = f"/api/shortfilms/{project['id']}/segments"
        response = await self.client.post(url, json={'duration': 5, 'prompts': ['第一段', '']})
        self.assertEqual(response.status, 400)
        self.assertEqual(self.app['shortfilms'].get(project['id'])['scenes'], [])
        response = await self.client.post(url, json={'duration': 5, 'prompts': ['走路'] * 12})
        self.assertEqual(response.status, 201)
        result = await response.json()
        self.assertEqual(len(result['scenes'][0]['shots']), 12)
        self.assertEqual(result['target_duration'], 60)
        self.assertEqual(self.app['jobs'].jobs, {})

    async def test_continuity_requires_completed_source_and_refreshes_stale_cached_frame(self):
        project = self.app['shortfilms'].create(append_continuous_scene(new_project(), {'prompts': ['walk', 'run']}))
        first, second = project['scenes'][0]['shots']
        url = f"/api/shortfilms/{project['id']}/shots/{second['id']}/compile"
        response = await self.client.post(url, json={'prepare_continuity': True})
        self.assertEqual(response.status, 409)
        old_frame = self.app['assets'].save_image(Image.new('RGB', (32, 32), 'red'), 'old', 'test')
        new_frame = self.app['assets'].save_image(Image.new('RGB', (32, 32), 'blue'), 'new', 'test')
        first['job_id'] = 'a' * 32
        first['status'] = 'completed'
        second['continuation_asset_id'] = old_frame['id']
        second['continuation_job_id'] = 'b' * 32
        self.app['shortfilms'].update(project['id'], project)
        self.app['jobs'].jobs[first['job_id']] = {'id': first['job_id'], 'status': 'completed', 'output': {'filename': 'new.mp4'}}
        (self.root / 'jobs' / f"{first['job_id']}.request.json").write_text(json.dumps({'references': []}), encoding='utf-8')
        self.app['jobs'].job_output_path = AsyncMock(return_value=self.root / 'new.mp4')
        with patch.object(studio, 'extract_continuation_frame', return_value=new_frame) as extract:
            response = await self.client.post(url, json={'prepare_continuity': True})
            result = await response.json()
            self.assertEqual(response.status, 200, result)
            self.assertEqual(result['payload']['first_image_asset_id'], new_frame['id'])
            self.assertEqual(result['project']['scenes'][0]['shots'][1]['continuation_job_id'], first['job_id'])
            self.assertEqual(extract.call_count, 1)
            # The same source can reuse its frame, but a different source cannot.
            response = await self.client.post(url, json={'prepare_continuity': True})
            self.assertEqual(response.status, 200)
            self.assertEqual(extract.call_count, 1)


if __name__ == '__main__':
    unittest.main()
