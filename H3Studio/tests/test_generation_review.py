"""Final review receipts bind the exact server preparation, not client compilation."""
import copy
import unittest
from unittest.mock import AsyncMock, patch

import app as studio
from domain import compile_request
from generation_review import GenerationReviewStore, GenerationReviewError
from shortfilm import new_project, new_scene, new_shot
from tests import test_dialogue_compile as fixture


class GenerationReviewStoreTests(unittest.TestCase):
    def test_receipts_are_bound_expiring_single_use_and_server_owned(self):
        source = fixture.request_payload("Original direction.")
        prepared = {**source, "prompt": "Prepared direction."}
        compiled = compile_request(prepared)
        with patch("generation_review.time.monotonic", return_value=100):
            store = GenerationReviewStore(ttl=10)
            receipt = store.issue(source, prepared, compiled)
            token, record = store.claim({**source, "review_receipt": receipt["review_receipt"]})
            self.assertEqual(record["compiled"].prompt, compiled.prompt)
            record["prepared"]["prompt"] = "Mutated caller copy."
            with self.assertRaises(GenerationReviewError):
                store.claim({**prepared, "review_receipt": token})
            store.release(token)
            _, record = store.claim({**prepared, "review_receipt": token})
            self.assertEqual(record["prepared"], prepared)
            store.consume(token)
            with self.assertRaises(GenerationReviewError):
                store.claim({**prepared, "review_receipt": token})
            expiring = store.issue(source, prepared, compiled)["review_receipt"]
        with patch("generation_review.time.monotonic", return_value=110):
            with self.assertRaises(GenerationReviewError):
                store.claim({**prepared, "review_receipt": expiring})

    def test_receipt_limit_and_all_generation_changes_invalidate_review(self):
        source = fixture.request_payload("Original direction.")
        store = GenerationReviewStore(limit=2)
        compiled = compile_request(source)
        token = store.issue(source, source, compiled)["review_receipt"]
        for changes in ({"seed": 43}, {"duration": 10}, {"prompt": "New direction."},
                        {"video_model": "fasth3_omni_nvfp4"}, {"compiled": {"prompt": compiled.prompt}}):
            with self.subTest(changes=changes), self.assertRaises(GenerationReviewError):
                store.claim({**source, **changes, "review_receipt": token})
        store.issue(source, source, compiled)
        store.issue(source, source, compiled)
        self.assertEqual(len(store.records), 2)
        with self.assertRaises(GenerationReviewError):
            store.claim({**source, "review_receipt": token})


class GenerationReviewAPITests(fixture.DialogueSubmissionAPITests):
    async def preview(self, payload):
        response = await self.client.post("/api/compile", json=payload)
        self.assertEqual(response.status, 200, await response.text())
        return await response.json()

    async def test_confirmed_submission_uses_server_prepared_prompt_without_translation(self):
        source = fixture.request_payload("User manuscript.")
        async def prepare(_, raw):
            return {**copy.deepcopy(raw), "prompt": "Prepared exact action.",
                    "h3_translation": {"status": "translated"}}
        with patch.object(studio, "prepare_h3_payload", new=AsyncMock(side_effect=prepare)) as preparation:
            preview = await self.preview(source)
            submitted = {**preview["prepared_payload"], "review_receipt": preview["review_receipt"]}
            with patch.object(self.app["jobs"], "create", return_value={"id": "a" * 32, "status": "queued"}) as create:
                response = await self.client.post("/api/render", json=submitted)
                self.assertEqual(response.status, 202, await response.text())
                frozen, payload = create.call_args.args
                self.assertEqual(frozen.prompt, preview["prompt"])
                self.assertEqual(frozen.seed, preview["seed"])
                self.assertEqual(payload, preview["prepared_payload"])
                self.assertNotIn("review_receipt", payload)
                duplicate = await self.client.post("/api/render", json=submitted)
                self.assertEqual(duplicate.status, 409, await duplicate.text())
                create.assert_called_once()
            preparation.assert_awaited_once()

    async def test_changed_expired_and_forged_reviews_never_create_jobs(self):
        source = fixture.request_payload("Final action.")
        preview = await self.preview(source)
        token = preview["review_receipt"]
        with patch.object(self.app["jobs"], "create") as create:
            for raw in ({**source, "seed": 43, "review_receipt": token},
                        {**source, "review_receipt": "forged"},
                        {**source, "review_receipt": None},
                        {**source, "compiled": preview, "review_receipt": token}):
                response = await self.client.post("/api/render", json=raw)
                self.assertEqual(response.status, 409, await response.text())
            self.app["generation_reviews"].records[token]["deadline"] = 0
            response = await self.client.post("/api/render", json={**source, "review_receipt": token})
            self.assertEqual(response.status, 409, await response.text())
            create.assert_not_called()

    async def test_shortfilm_review_rejects_changed_saved_source(self):
        project, scene, shot = new_project("Review film"), new_scene(), new_shot()
        shot["action"] = "The actor raises one hand."
        scene["shots"] = [shot]
        project["scenes"] = [scene]
        project = self.app["shortfilms"].create(project)
        response = await self.client.post(f"/api/shortfilms/{project['id']}/shots/{shot['id']}/compile", json={})
        self.assertEqual(response.status, 200, await response.text())
        preview = await response.json()
        self.assertEqual(preview["review_receipt"], preview["compiled"]["review_receipt"])
        project["scenes"][0]["shots"][0]["action"] = "The actor lowers both hands."
        self.app["shortfilms"].update(project["id"], project)
        with patch.object(self.app["jobs"], "create") as create:
            response = await self.client.post("/api/render", json={**preview["payload"], "review_receipt": preview["review_receipt"]})
            self.assertEqual(response.status, 409, await response.text())
            create.assert_not_called()

    async def test_shortfilm_batch_http_requires_review_before_creating_job(self):
        project, scene, shot = new_project("Review batch"), new_scene(), new_shot()
        shot["action"] = "The actor raises one hand."
        scene["shots"] = [shot]
        project["scenes"] = [scene]
        project = self.app["shortfilms"].create(project)
        batches = self.app["shortfilm_batches"]
        def create(compiled, payload):
            result = {"id": "a" * 32, "status": "completed"}
            self.app["jobs"].jobs[result["id"]] = result
            return result
        with patch.object(self.app["jobs"], "create", side_effect=create) as submit:
            response = await self.client.post(f"/api/shortfilms/{project['id']}/batch", json={"require_prompt_review": True})
            self.assertEqual(response.status, 202, await response.text())
            batch = await response.json()
            await batches.tasks[batch["id"]]
            waiting = batches.get(batch["id"])
            self.assertEqual(waiting["status"], "awaiting_review", waiting.get("error"))
            submit.assert_not_called()
            prompt = waiting["review"]["prompts"][0]["prompt"]
            response = await self.client.post(f"/api/shortfilm-batches/{batch['id']}/approve", json={"review_id": waiting["review"]["id"]})
            self.assertEqual(response.status, 202, await response.text())
            await batches.tasks[batch["id"]]
            self.assertEqual(batches.get(batch["id"])["status"], "completed")
            compiled, _ = submit.call_args.args
            self.assertEqual(compiled.prompt, prompt)
            submit.assert_called_once()

    async def test_long_replacement_plan_receipt_enables_per_segment_confirmation(self):
        from PIL import Image
        from tests.test_continuation import make_video
        source_id = "d" * 32
        assets = self.app["assets"]
        make_video(assets.directory / f"{source_id}.mp4", [(20, 30, 40)] * (16 * 24), with_audio=False)
        assets.register_derived_video(source_id, "source.mp4", "replacement-performance-video")
        image = assets.save_image(Image.new("RGB", (32, 32), "red"), "new.png", "replacement-character")
        original = assets.save_image(Image.new("RGB", (32, 32), "blue"), "original.png", "replacement-source-character")
        source = fixture.request_payload("The new actor follows the original performance.", mode="replace",
            replacement_auto_split=True, replacement_split_strategy="balanced",
            replacement_source_image_asset_ids=[original["id"]],
            references=[{"alias": "New actor", "type": "character", "image_asset_ids": [image["id"]],
                         "video_asset_id": source_id, "video_use_audio": False}])
        preview = await self.preview(source)
        self.assertEqual(preview["review_kind"], "replacement_plan")
        with patch.object(self.app["jobs"], "create_replacement_batch", return_value={"id": "a" * 32, "status": "queued"}) as create:
            response = await self.client.post("/api/render", json={**preview["prepared_payload"], "review_receipt": preview["review_receipt"]})
            self.assertEqual(response.status, 202, await response.text())
            self.assertTrue(create.call_args.kwargs["require_prompt_review"])
            self.assertGreater(len(create.call_args.args[3]), 1)
        response = await self.client.post(f"/api/jobs/{'a' * 32}/approve", json={"review_id": "forged"})
        self.assertEqual(response.status, 409, await response.text())
