import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from music3 import (
    MUSIC3_DIT,
    MUSIC3_TEXT_ENCODER,
    MUSIC3_VAE,
    Music3Error,
    Music3Installer,
    MusicJobManager,
    build_music3_workflow,
    compile_music_request,
    find_audio_output,
)


class MusicPromptTests(unittest.TestCase):
    def test_instrumental_prompt_forbids_voice_and_uses_structure_tags(self):
        compiled = compile_music_request({
            "mode": "instrumental",
            "duration": 45,
            "seed": 123,
            "genre": "slot game victory music",
        })
        self.assertIn("Global Metadata:", compiled["caption"])
        self.assertIn("Vocal Details:", compiled["caption"])
        self.assertIn("Instrumental only", compiled["caption"])
        self.assertIn("Arrangement:", compiled["caption"])
        self.assertIn("[Instrumental]", compiled["lyrics"])

    def test_song_requires_lyrics(self):
        with self.assertRaisesRegex(Music3Error, "歌詞"):
            compile_music_request({"mode": "song", "duration": 60})

    def test_song_preserves_lyrics(self):
        lyrics = "[Verse]\n好運來\n\n[Chorus]\n恭喜發財"
        compiled = compile_music_request({"mode": "song", "duration": 60, "lyrics": lyrics})
        self.assertEqual(compiled["lyrics"], lyrics)

    def test_duration_is_limited(self):
        with self.assertRaisesRegex(Music3Error, "10 到 300"):
            compile_music_request({"duration": 301})

    def test_workflow_uses_official_low_vram_models_and_nodes(self):
        compiled = compile_music_request({"mode": "instrumental", "duration": 30, "format": "mp3"})
        workflow = build_music3_workflow(compiled, "test_music")
        self.assertEqual(workflow["1"]["inputs"]["unet_name"], MUSIC3_DIT)
        self.assertEqual(workflow["2"]["inputs"]["clip_name"], MUSIC3_TEXT_ENCODER)
        self.assertEqual(workflow["3"]["inputs"]["vae_name"], MUSIC3_VAE)
        self.assertEqual(workflow["4"]["class_type"], "MiniMaxMusic3TextEncode")
        self.assertEqual(workflow["8"]["class_type"], "VAEDecodeAudioTiled")
        self.assertEqual(workflow["9"]["inputs"]["format"], "mp3")
        self.assertEqual(workflow["9"]["inputs"]["format.quality"], "V0")

    def test_audio_output_is_found_recursively(self):
        value = {"outputs": {"9": {"audio": [{"filename": "song.mp3", "subfolder": "music", "type": "output"}]}}}
        self.assertEqual(find_audio_output(value)["filename"], "song.mp3")


class FakeComfy:
    mode = "local"

    def __init__(self, comfy_dir: Path):
        self.comfy_dir = comfy_dir
        self._model_cache = None

    async def ensure_running(self):
        return None

    async def run_prompt(self, workflow, callback, _cancel_event):
        await callback({"status": "running", "progress": 50, "current_node": "7"})
        output = {"filename": "generated.mp3", "subfolder": "H3StudioMusic", "type": "output"}
        return "prompt-music", {"outputs": {"9": {"audio": [output]}}}

    async def fetch_output(self, _output):
        return b"fake-mp3", "audio/mpeg"


class MusicJobTests(unittest.IsolatedAsyncioTestCase):
    async def test_music_job_is_persisted_and_completed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            comfy_dir = root / "ComfyUI"
            (comfy_dir / "models").mkdir(parents=True)
            comfy = FakeComfy(comfy_dir)
            installer = Music3Installer(comfy)
            installer.public_status = lambda: {"installed": True}
            manager = MusicJobManager(comfy, root / "data", asyncio.Lock(), installer)
            job = manager.create({"mode": "instrumental", "duration": 30, "seed": 42, "job_name": "測試配樂"})
            await manager.tasks[job["id"]]
            completed = manager.jobs[job["id"]]
            self.assertEqual(completed["status"], "completed")
            self.assertEqual(completed["prompt_id"], "prompt-music")
            self.assertEqual(manager.local_output_path(completed).read_bytes(), b"fake-mp3")
            persisted = json.loads((manager.job_dir / f"{job['id']}.json").read_text(encoding="utf-8"))
            self.assertEqual(persisted["name"], "測試配樂")


if __name__ == "__main__":
    unittest.main()
