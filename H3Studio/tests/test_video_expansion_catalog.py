"""New adapters retain author workflow requirements and old-version choices."""
import unittest

from custom_loras import normalize_loras, required_lora_triggers, validate_lora_requirements
from lora_catalog import (SWAP_FILE, SWAP_V15_FILE, MOTION_REPAIR_FILE,
                          describe_lora, recommended_lora_items, builtin_lora_items)


def row(filename, **values):
    return dict(name='h3studio_custom/' + filename, family='both', strength=.5, **values)


class VideoExpansionCatalogTests(unittest.TestCase):
    def test_swap_versions_are_independent_and_both_require_video_and_character(self):
        for filename in (SWAP_FILE, SWAP_V15_FILE):
            selection = row(filename)
            self.assertEqual(normalize_loras([selection])[0]['family'], 'ref2va')
            with self.assertRaisesRegex(ValueError, '來源影片'):
                validate_lora_requirements([selection], 'ref2va', has_replacement_image=True)
            with self.assertRaisesRegex(ValueError, '參考圖片'):
                validate_lora_requirements([selection], 'ref2va', has_source_video=True)
            accepted = validate_lora_requirements([selection], 'ref2va', has_source_video=True, has_replacement_image=True)
            self.assertEqual(accepted[0]['name'], selection['name'])
        self.assertNotEqual(describe_lora(SWAP_FILE)['revision'], describe_lora(SWAP_V15_FILE)['revision'])
        self.assertNotEqual(describe_lora(SWAP_FILE)['sha256'], describe_lora(SWAP_V15_FILE)['sha256'])

    def test_motion_repair_optional_cue_is_not_inserted_or_assigned_an_unverified_family(self):
        description = describe_lora(MOTION_REPAIR_FILE)
        self.assertIsNone(description['family'])
        self.assertEqual(description['trigger'], 'bunny_crisp_motion')
        self.assertFalse(description['trigger_required'])
        for family in ('fl2va', 'ref2va'):
            selection = [row(MOTION_REPAIR_FILE)]
            self.assertEqual(required_lora_triggers(selection, family), [])
            self.assertEqual(validate_lora_requirements(selection, family)[0]['family'], 'both')

    def test_missing_new_entries_are_not_selectable_and_installed_exact_names_are_retained(self):
        names = ['h3studio_custom/' + SWAP_V15_FILE, 'h3studio_custom/' + MOTION_REPAIR_FILE]
        missing = {item['name']: item for item in recommended_lora_items([])}
        installed = {item['name']: item for item in recommended_lora_items(names)}
        for name in names:
            self.assertFalse(missing[name]['installed'])
            self.assertFalse(missing[name]['manual_selectable'])
            self.assertTrue(installed[name]['installed'])
            self.assertTrue(installed[name]['manual_selectable'])
            self.assertEqual(len(installed[name]['sha256']), 64)
            self.assertEqual(len(installed[name]['revision']), 40)

    def test_new_turbo_records_point_to_author_and_do_not_replace_old_profiles(self):
        items = {item['quality_mode']: item for item in builtin_lora_items([])}
        for quality in ('turbo_fast_v13', 'turbo_quality_v11', 'turbo_ref_fast_v10', 'turbo_ref_quality_v11'):
            item = items[quality]
            self.assertEqual(item['source_url'], 'https://huggingface.co/lightx2v/Minimax-h3-Turbo')
            self.assertFalse(item['manual_selectable'])
            self.assertFalse(item['installed'])
        for quality in ('turbo_fast', 'turbo_quality', 'turbo_audio', 'turbo_ref_quality'):
            self.assertIn(quality, items)


if __name__ == '__main__':
    unittest.main()
