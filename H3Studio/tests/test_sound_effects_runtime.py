import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

import setup_sound_effects as setup
import sound_effects_worker as worker


class SoundEffectsWorkerTests(unittest.TestCase):
    def test_invalid_duration_and_uninstalled_model_never_load_torch(self):
        for seconds in (0, 30.1, float('nan'), float('inf')):
            with self.subTest(seconds=seconds), self.assertRaisesRegex(ValueError, '長度'):
                worker.validate_request({'prompt': 'Rain', 'seconds': seconds})
        with self.assertRaisesRegex(ValueError, '尚未安裝'):
            worker.validate_request({'prompt': 'Rain', 'seconds': 5, 'model_dir': 'missing-moss-model'})

    def test_valid_seed_and_tenth_duration_are_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            model = Path(temporary)
            (model / 'model_index.json').write_text('{}')
            result = worker.validate_request({'prompt': '  腳步聲  ', 'seconds': 4.16,
                                             'seed': 2**64 - 1, 'model_dir': str(model)})
        self.assertEqual(result['prompt'], '腳步聲')
        self.assertEqual(result['seconds'], 4.2)
        self.assertEqual(result['seed'], 2**64 - 1)
        self.assertEqual(result['steps'], 100)

    def test_invalid_prompt_seed_steps_do_not_enter_inference(self):
        for request in ({'prompt': ''}, {'prompt': 'x' * 4001},
                        {'prompt': 'Rain', 'seed': -1}, {'prompt': 'Rain', 'seed': 2**64},
                        {'prompt': 'Rain', 'steps': 19}, {'prompt': 'Rain', 'steps': 101}):
            with self.subTest(request=str(request)[:70]), self.assertRaises(ValueError):
                worker.validate_request(request)

    def test_objective_quality_rejects_nonfinite_or_empty_and_reports_clipping(self):
        for audio in ([], [float('nan')], [float('inf')]):
            with self.subTest(audio=audio), self.assertRaises(ValueError):
                worker.waveform_quality(audio)
        metrics = worker.waveform_quality(np.array([0.0, 0.5, -1.5, 1.0]))
        self.assertTrue(metrics['finite'])
        self.assertEqual(metrics['peak_abs'], 1.5)
        self.assertEqual(metrics['clipped_fraction'], 0.5)
        self.assertFalse(metrics['near_silent'])
        self.assertTrue(worker.waveform_quality(np.zeros(24000))['near_silent'])

    def test_progress_wraps_exactly_the_existing_sampler_and_finishes_at_decode(self):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            values = list(worker.sampler_progress([10, 20, 30]))
        events = [json.loads(line) for line in buffer.getvalue().splitlines()]
        self.assertEqual(values, [10, 20, 30])
        self.assertEqual(events[-1]['stage'], 'decoding')
        self.assertEqual(events[-1]['steps'], 3)
        self.assertEqual([item['progress'] for item in events], sorted(item['progress'] for item in events))


class SoundEffectsSetupTests(unittest.TestCase):
    def test_runtime_paths_are_isolated_and_status_never_claims_missing_environment(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = setup.runtime_paths(root)
            self.assertEqual(paths['root'], root / 'sfx_runtime')
            self.assertTrue(paths['python'].is_relative_to(paths['root']))
            self.assertTrue(paths['model'].is_relative_to(paths['root']))
            status = setup.probe_installation(root)
        self.assertFalse(status['installed'])
        self.assertFalse(status['runtime_installed'])
        self.assertEqual(status['model_revision'], setup.MODEL_REVISION)

    def test_model_verification_detects_same_size_corruption(self):
        with tempfile.TemporaryDirectory() as temporary:
            model = Path(temporary)
            path = model / 'weight.bin'
            path.write_bytes(b'original')
            expected = setup.sha256(path)
            with patch.object(setup, 'MODEL_FILES', {'weight.bin': (8, expected)}):
                self.assertEqual(setup.verify_model(model)['weight.bin'], expected)
                path.write_bytes(b'corrupt!')
                with self.assertRaisesRegex(RuntimeError, '驗證失敗'):
                    setup.verify_model(model)

    def test_status_requires_completed_pinned_manifest_and_full_size_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = setup.runtime_paths(root)
            paths['python'].parent.mkdir(parents=True)
            paths['python'].touch()
            paths['source'].mkdir(parents=True)
            (paths['source'] / 'pipeline_moss_soundeffect.py').touch()
            paths['model'].mkdir(parents=True)
            (paths['model'] / 'weight.bin').write_bytes(b'ok')
            manifest = {'version': setup.VERSION, 'model_revision': setup.MODEL_REVISION,
                        'source_revision': setup.SOURCE_REVISION, 'verified': True}
            paths['manifest'].write_text(json.dumps(manifest))
            with patch.object(setup, 'MODEL_FILES', {'weight.bin': (2, None)}):
                self.assertTrue(setup.probe_installation(root)['installed'])
                manifest['source_revision'] = 'another-source'
                paths['manifest'].write_text(json.dumps(manifest))
                self.assertFalse(setup.probe_installation(root)['installed'])
                (paths['model'] / 'weight.bin').write_bytes(b'incomplete')
                self.assertFalse(setup.probe_installation(root)['model_installed'])


if __name__ == '__main__':
    unittest.main()
