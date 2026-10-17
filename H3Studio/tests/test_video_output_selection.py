import unittest

from app import find_video_output


def video(filename, media_type="output", subfolder="H3Studio"):
    return {"filename": filename, "subfolder": subfolder, "type": media_type}


class VideoOutputSelectionTests(unittest.TestCase):
    def test_load_video_preview_before_save_video_does_not_become_result(self):
        source = video("uploaded-source.mov", "input", "h3studio/job-id")
        generated = video("人物影片測試_00004_.mp4")
        # Comfy LoadVideo and SaveVideo both use outputs.images. Dict order is
        # significant: the loader finishes before the generated file is saved.
        history = {"outputs": {
            "8": {"images": [source], "animated": [True]},
            "2": {"text": ["model information"]},
            "19": {"images": [generated], "animated": [True]},
        }}
        self.assertEqual(find_video_output(history), generated)

    def test_input_video_alone_is_not_a_completed_generation(self):
        history = {"outputs": {"8": {"images": [video("source.mov", "input")]}}}
        self.assertIsNone(find_video_output(history))

    def test_input_video_is_rejected_in_each_supported_media_container(self):
        for container in ("images", "videos", "gifs"):
            with self.subTest(container=container):
                self.assertIsNone(find_video_output({container: [video("source.mp4", "input")]}))

    def test_native_save_video_and_vhs_containers_are_supported(self):
        for container in ("images", "videos", "gifs"):
            with self.subTest(container=container):
                generated = video("generated.mp4")
                self.assertEqual(find_video_output({"outputs": {"19": {container: [generated]}}}), generated)

    def test_saved_output_beats_earlier_temp_preview(self):
        generated = video("generated.mp4")
        history = {"outputs": {
            "preview": {"videos": [video("preview.mp4", "temp", "")]},
            "save": {"gifs": [generated]},
        }}
        self.assertEqual(find_video_output(history), generated)

    def test_temp_video_is_preserved_when_no_saved_output_exists(self):
        preview = video("preview.webm", "temp", "previews")
        self.assertEqual(find_video_output({"outputs": {"1": {"gifs": [preview]}}}), preview)

    def test_explicit_output_beats_earlier_legacy_untyped_descriptor(self):
        generated = video("generated.mp4")
        legacy = {"filename": "legacy.mp4", "subfolder": "older-node"}
        self.assertEqual(find_video_output([legacy, generated]), generated)

    def test_legacy_untyped_descriptor_keeps_output_default(self):
        self.assertEqual(find_video_output({"filename": "generated.mp4"}), {
            "filename": "generated.mp4", "subfolder": "", "type": "output",
        })

    def test_legacy_untyped_result_beats_temp_file(self):
        self.assertEqual(find_video_output([
            video("preview.mp4", "temp"), {"filename": "generated.mp4"},
        ])["filename"], "generated.mp4")

    def test_history_prompt_and_status_cannot_override_node_outputs(self):
        generated = video("generated.mp4")
        history = {
            "prompt": [0, "prompt-id", {"loader": {"inputs": video("metadata.mp4")}}],
            "status": {"messages": [["debug", video("status.mov")]]},
            "extra_data": {"workflow": video("workflow.mov")},
            "outputs": {"19": {"images": [generated]}},
        }
        self.assertEqual(find_video_output(history), generated)
        history["outputs"] = {}
        self.assertIsNone(find_video_output(history))

    def test_missing_history_outputs_does_not_reuse_prompt_metadata(self):
        self.assertIsNone(find_video_output({
            "prompt": {"filename": "reference.mov"},
            "status": {"debug": video("status.mp4")},
            "extra_data": {"workflow": video("workflow.mp4")},
        }))

    def test_prompt_id_wrapped_history_is_supported(self):
        generated = video("generated.mp4")
        self.assertEqual(find_video_output({"prompt-id": {"outputs": {
            "8": {"images": [video("source.mov", "input")]},
            "19": {"images": [generated]},
        }}}), generated)

    def test_supported_extensions_are_case_insensitive(self):
        for extension in ("mp4", "WEBM", "MOV", "MKV"):
            with self.subTest(extension=extension):
                generated = video(f"generated.{extension}")
                self.assertEqual(find_video_output(generated), generated)

    def test_non_video_and_unknown_storage_types_are_rejected(self):
        for descriptor in (
            video("thumbnail.png"), video("audio.mp3"), video("movie.gif"),
            video("source.mp4", "input"), video("movie.mp4", "remote"),
            {"filename": 123}, None, "generated.mp4",
        ):
            with self.subTest(descriptor=descriptor):
                self.assertIsNone(find_video_output(descriptor))

    def test_nested_results_preserve_first_same_priority_result(self):
        first = video("first.mp4", subfolder="generated")
        second = video("second.mp4")
        self.assertEqual(find_video_output([None, {"nodes": [[first], [second]]}]), first)

    def test_selection_does_not_mutate_history(self):
        import copy

        history = {"outputs": {
            "8": {"images": [video("source.mov", "input")]},
            "19": {"gifs": [video("generated.mp4")]},
        }}
        before = copy.deepcopy(history)
        find_video_output(history)
        self.assertEqual(history, before)


if __name__ == "__main__":
    unittest.main()
