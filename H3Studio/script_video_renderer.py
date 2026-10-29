"""Connect persisted screenplay pipelines to the existing local H3 and editor engines."""
from __future__ import annotations

import asyncio
import copy
import aiohttp
from dataclasses import asdict
from generation_review import compiled_from_review, review_digest

from domain import compile_request, required_asset_ids, validate_runtime_inventory
from scriptwriting import ScriptwritingError, local_base_url
from shortfilm import compile_shot_payload, flatten_shots, resolve_shot_asset_ids, validate_shot_references
from custom_loras import active_loras, validate_loras_for_family, validate_lora_requirements
from h3_prompt_translation import prepare_h3_payload, payload_digest
from script_video_format import normalize_video_settings, video_dimensions, ScriptVideoFormatError
from video_models import VIDEO_MODEL_H3, VIDEO_MODEL_FAST, normalize_video_model, video_model_profiles


class ScriptVideoRenderer:
    def __init__(self, app):
        self.app = app
        self._assemble_lock = asyncio.Lock()

    async def _prepare_payload(self, raw):
        prepare = self.app.get("prepare_generation_payload")
        if callable(prepare):
            return await prepare(raw)
        return await prepare_h3_payload(self.app, raw)

    def _local_engine(self):
        comfy = self.app.get("comfy")
        if comfy is None or getattr(comfy, "mode", None) != "local":
            raise ScriptwritingError("自動影片目前使用本機 H3 引擎，請先在影片工作室連接本機引擎。", 503)
        local_base_url(comfy.base_url)
        return comfy

    @staticmethod
    def _probe_request(video_model=VIDEO_MODEL_H3):
        # Compiling this recipe only verifies model compatibility; it runs no inference.
        return compile_request({"mode": "r2v", "video_model": video_model, "duration": 5, "quality_mode": "native",
                                "megapixels": 0.4, "aspect_ratio": "16:9",
                                "prompt": "Preserve the referenced subject's appearance during a simple visible action.",
                                "references": [{"alias": "參考角色", "type": "character",
                                                "image_asset_ids": ["0" * 32], "description": "Reference appearance."}]})

    async def readiness(self, video_model=None):
        profiles = video_model_profiles({})
        try:
            comfy = self._local_engine()
            if not await comfy.is_ready():
                raise ScriptwritingError("本機影片引擎尚未就緒，請先啟動影片工作室的 H3 引擎並重新檢查。")
            inventory = await comfy.model_inventory()
            profiles = video_model_profiles(inventory)
            for profile in profiles:
                try:
                    validate_runtime_inventory(self._probe_request(profile["id"]), inventory)
                    profile.update(ready=True, error=None)
                except ValueError as error:
                    profile.update(ready=False, error=str(error))
            if self.app.get("editor") is None:
                raise ScriptwritingError("本機影片合成功能尚未啟用。", 503)
        except (ValueError, OSError, RuntimeError, TimeoutError, aiohttp.ClientError) as error:
            for profile in profiles:
                profile.update(ready=False, error=str(error))
        selected = ([profile for profile in profiles if profile["id"] == normalize_video_model(video_model)]
                    if video_model is not None else profiles)
        ready = any(profile["ready"] for profile in selected)
        return {"ready": ready, "error": None if ready else next((profile.get("error") for profile in selected if profile.get("error")), "影片模型尚未就緒。"),
                "video_model_supported": True, "video_models": profiles}

    async def preflight(self, project):
        try:
            settings = normalize_video_settings(project.get("video_settings"))
            video_model = normalize_video_model(settings.get("video_model"))
            loras = [] if video_model == VIDEO_MODEL_FAST else validate_loras_for_family(project.get("custom_loras", []), "ref2va")
            validate_lora_requirements(loras, "ref2va")
        except ValueError as error:
            raise ScriptwritingError(str(error)) from error
        duration = project.get("duration", 0)
        if not 5 <= duration <= 600:
            raise ScriptwritingError("自動劇本到影片目前支援 5～600 秒，最多 50 個生成片段。")
        references = project.get("character_references", [])
        if not any(item.get("image_asset_id") for item in references):
            raise ScriptwritingError("多模態自動影片需要至少一張角色、物件或場景參考圖；請先上傳並填寫外觀描述。")
        for item in references:
            if item.get("type") in {"character", "creature"} and not item.get("image_asset_id"):
                raise ScriptwritingError(f"角色「{item['name']}」缺少參考圖片，請先補齊再啟動自動影片。")
            if item.get("image_asset_id") and not str(item.get("appearance", "")).strip():
                raise ScriptwritingError(f"參考圖「{item['name']}」尚未填寫外觀描述，請先分析外型並套用。")
            if len(item.get("appearance", "")) > 1200:
                raise ScriptwritingError(f"「{item['name']}」的外觀描述超過影片素材 1200 字上限，請先縮短。")
        self.app["scriptwriting"]._validate_reference_assets(references)
        comfy = self._local_engine()
        await comfy.ensure_running()
        selected = active_loras(loras, "ref2va")
        if selected:
            available = {name.replace("\\", "/") for name in await comfy.list_loras()}
            missing = [item["name"] for item in selected if item["name"] not in available]
            if missing:
                raise ScriptwritingError("影片引擎缺少已選 LoRA：" + "、".join(missing) + "。請重新掃描或停用後再啟動自動影片。")
        result = await self.readiness(video_model)
        if not result["ready"]:
            raise ScriptwritingError(result["error"], 503)

    @staticmethod
    def _public_payload(project, scene, shot, pipeline_id):
        validate_shot_references(project, scene, shot)
        selected_ids = resolve_shot_asset_ids(project, scene, shot)
        selected = [item for item in project["assets"] if item["id"] in selected_ids]
        missing = [item["alias"] for item in selected if item["type"] in {"character", "creature"} and not item.get("image_asset_ids")]
        if missing:
            raise ScriptwritingError("分鏡角色尚未對應參考圖片：" + "、".join(missing) + "。請在編劇角色卡補圖後開始新的自動流程。")
        raw, _ = compile_shot_payload(project, scene["id"], shot["id"])
        if raw["mode"] != "r2v" or not any(item.get("image_asset_ids") for item in raw.get("references", [])):
            raise ScriptwritingError(f"「{shot['title']}」沒有可用的多模態參考圖片，請補齊素材後開始新的流程。")
        if shot.get("continue_previous"):
            raise ScriptwritingError("自動流程使用固定圖片參考；沿用上一鏡尾幀的分鏡請在短片工作室操作。")
        raw.update(shortfilm_project_id=project["id"], shortfilm_scene_id=scene["id"], shortfilm_shot_id=shot["id"],
                   shortfilm_scene_title=scene["title"], shortfilm_shot_title=shot["title"], script_video_pipeline_id=pipeline_id)
        # The screenplay profile carries ambience/dialogue and has no music track.
        raw["shortfilm_music"] = "N/A"
        return raw

    async def compile_all(self, shortfilm_id, pipeline_id):
        project = self.app["shortfilms"].get(shortfilm_id)
        pipelines = getattr(self.app.get("script_video"), "pipelines", {})
        pipeline = pipelines.get(pipeline_id, {}) if isinstance(pipelines, dict) else {}
        self._validate_frozen_format(project, pipeline)
        shots = flatten_shots(project)
        if not 1 <= len(shots) <= 50 or sum(shot["duration"] for _, shot in shots) > 600:
            raise ScriptwritingError("自動影片最多 50 個片段、合計最多 600 秒，請縮短劇本後重試。")
        manifests = []
        inventory = await self._local_engine().model_inventory(refresh=True)
        for scene, shot in shots:
            raw = self._public_payload(project, scene, shot, pipeline_id)
            prepared = await self._prepare_payload(raw)
            compiled = compile_request(prepared)
            for asset_id in required_asset_ids(compiled):
                self.app["assets"].path_for(asset_id)
            validate_runtime_inventory(compiled, inventory)
            manifest = {"scene_id": scene["id"], "shot_id": shot["id"], "payload": raw}
            manifest["compiled"] = asdict(compiled)
            manifest["prepared_payload"] = prepared
            manifest["prepared_source_digest"] = payload_digest(raw)
            manifests.append(manifest)
        return manifests

    @staticmethod
    def _video_settings(pipeline):
        snapshot = pipeline.get("snapshot") or {}
        value = pipeline.get("video_settings") if "video_settings" in pipeline else snapshot.get("video_settings")
        try:
            return normalize_video_settings(value)
        except ScriptVideoFormatError as error:
            raise ScriptwritingError(str(error)) from error

    @classmethod
    def _validate_frozen_format(cls, project, pipeline):
        snapshot = pipeline.get("snapshot") or {}
        if "video_settings" not in pipeline and "video_settings" not in snapshot:
            if normalize_video_model(project.get("video_model")) != VIDEO_MODEL_H3:
                raise ScriptwritingError("這筆舊流程使用原版 H3；影片模型已修改，請開始新的流程。", 409)
            return
        settings = cls._video_settings(pipeline)
        if (project.get("aspect_ratio", "16:9") != settings["aspect_ratio"] or project.get("megapixels", 0.4) != settings["megapixels"]
                or normalize_video_model(project.get("video_model")) != normalize_video_model(settings.get("video_model"))):
            raise ScriptwritingError("影片模型、比例或生成解析度已被修改，請開始新的流程；已完成的片段仍保留。", 409)

    @staticmethod
    def _generation_source(project):
        """Compare frozen source, excluding only known non-generation runtime fields."""
        if not isinstance(project, dict) or not isinstance(project.get("scenes"), list):
            return None
        source = copy.deepcopy(project)
        for field in ("created_at", "updated_at", "segment_draft"):
            source.pop(field, None)
        for scene in source["scenes"]:
            if not isinstance(scene, dict) or not isinstance(scene.get("shots"), list):
                return None
            for shot in scene["shots"]:
                if not isinstance(shot, dict):
                    return None
                for field in ("job_id", "status"):
                    shot.pop(field, None)
        # Unknown fields remain part of the comparison rather than being normalized away.
        return source

    def _unchanged_frozen_payload(self, project, scene_id, shot_id, pipeline_id, raw, expected):
        """Allow only compiler-template drift backed by this pipeline's frozen source."""
        if not isinstance(expected, dict) or raw.keys() != expected.keys():
            return None
        changed = {key for key in raw.keys() | expected.keys() if raw.get(key) != expected.get(key)}
        # A saved Native beta recipe predates the default alignment. After the
        # source/manifest checks below, resume its original request unchanged.
        scheduler_default_drift = (raw.get("scheduler") == "simple" and expected.get("scheduler") == "beta"
                                   and raw.get("quality_mode") == expected.get("quality_mode") == "native")
        allowed = {"prompt", "shortfilm_summary"} | ({"scheduler"} if scheduler_default_drift else set())
        if not changed or not changed <= allowed:
            return None
        manager = self.app.get("script_video")
        pipelines = getattr(manager, "pipelines", None)
        pipeline = pipelines.get(pipeline_id) if isinstance(pipelines, dict) else None
        if not isinstance(pipeline, dict) or pipeline.get("id") != pipeline_id or pipeline.get("shortfilm_id") != project["id"]:
            return None
        lineage = {"shortfilm_project_id": project["id"], "shortfilm_scene_id": scene_id,
                   "shortfilm_shot_id": shot_id, "script_video_pipeline_id": pipeline_id}
        if any(expected.get(key) != value for key, value in lineage.items()):
            return None
        plan = pipeline.get("render_plan")
        if not isinstance(plan, list):
            return None
        matches = [item for item in plan if isinstance(item, dict)
                   and item.get("scene_id") == scene_id and item.get("shot_id") == shot_id]
        if len(matches) != 1 or matches[0].get("payload") != expected:
            return None
        frozen = pipeline.get("shortfilm_snapshot")
        if not isinstance(frozen, dict) or frozen.get("id") != project["id"]:
            return None
        frozen_source = self._generation_source(frozen)
        if frozen_source is None or frozen_source != self._generation_source(project):
            return None
        return copy.deepcopy(expected)

    async def render_shot(self, shortfilm_id, scene_id, shot_id, pipeline_id, *, expected_payload=None):
        project = self.app["shortfilms"].get(shortfilm_id)
        pipelines = getattr(self.app.get("script_video"), "pipelines", {})
        pipeline = pipelines.get(pipeline_id, {}) if isinstance(pipelines, dict) else {}
        self._validate_frozen_format(project, pipeline)
        found = next(((scene, shot) for scene, shot in flatten_shots(project) if scene["id"] == scene_id and shot["id"] == shot_id), None)
        if found is None:
            raise ScriptwritingError("自動分鏡已被修改或刪除；請開始新的流程，避免混用不同版本。", 409)
        scene, shot = found
        try:
            raw = self._public_payload(project, scene, shot, pipeline_id)
        except ValueError as error:
            if expected_payload is not None:
                raise ScriptwritingError("自動分鏡或參考素材已被修改，請開始新的流程；已完成的片段仍保留。", 409) from error
            raise
        if expected_payload is not None and raw != expected_payload:
            frozen_raw = self._unchanged_frozen_payload(project, scene_id, shot_id, pipeline_id, raw, expected_payload)
            if frozen_raw is None:
                raise ScriptwritingError("自動分鏡或參考素材已被修改，請開始新的流程；已完成的片段仍保留。", 409)
            raw = frozen_raw
        # Keep source comparison strict, while reusing the validated English preparation
        # recorded at preflight rather than translating every resumed shot again.
        prepared = None
        pipelines = getattr(self.app.get("script_video"), "pipelines", {})
        pipeline = pipelines.get(pipeline_id, {}) if isinstance(pipelines, dict) else {}
        for item in pipeline.get("render_plan") or []:
            candidate = item.get("prepared_payload") if isinstance(item, dict) else None
            if (isinstance(candidate, dict) and item.get("payload") == raw
                    and item.get("prepared_source_digest") == payload_digest(raw)):
                prepared = copy.deepcopy(candidate)
                break
        if prepared is None:
            if pipeline.get("require_prompt_review"):
                raise ScriptwritingError("已確認的生成稿資料不完整，請重新建立流程。", 409)
            prepared = await self._prepare_payload(raw)
        if pipeline.get("require_prompt_review"):
            review = pipeline.get("review") or {}
            if (not review.get("id") or pipeline.get("approved_review_id") != review["id"]
                    or review.get("plan_digest") != review_digest(pipeline.get("render_plan"))):
                raise ScriptwritingError("請先檢查並確認生成稿，再生成影片。", 409)
            manifest = next((item for item in pipeline.get("render_plan", [])
                             if item.get("scene_id") == scene_id and item.get("shot_id") == shot_id and item.get("payload") == raw), None)
            if not manifest or not isinstance(manifest.get("compiled"), dict) or prepared != manifest.get("prepared_payload"):
                raise ScriptwritingError("已確認的生成稿資料不完整，請重新建立流程。", 409)
            compiled = compiled_from_review(manifest["compiled"])
        else:
            compiled = compile_request(prepared)
        for asset_id in required_asset_ids(compiled):
            self.app["assets"].path_for(asset_id)
        video = self.app["jobs"].create(compiled, prepared)
        shot.update(job_id=video["id"], status=video["status"])
        try:
            self.app["shortfilms"].update(shortfilm_id, project)
        except (OSError, ValueError):
            await self.app["jobs"].cancel(video["id"])
            raise
        return copy.deepcopy(video)

    async def assemble(self, shortfilm_id, video_job_ids, pipeline_id, progress_callback):
        async with self._assemble_lock:
            editor = self.app["editor"]
            pipeline = self.app["script_video"].pipelines[pipeline_id]
            dimensions = video_dimensions(self._video_settings(pipeline))
            if pipeline.get("export_id"):
                saved_export = editor.exports.get(pipeline["export_id"])
                if saved_export and saved_export["status"] == "completed":
                    editor.export_path(saved_export["id"])
                    return {"video_url": saved_export["url"], "export_id": saved_export["id"],
                            "editor_project_id": saved_export["project_id"]}
            project = pipeline.get("shortfilm_snapshot") or self.app["shortfilms"].get(shortfilm_id)
            plans = pipeline.get("render_plan") or [{"payload": {"duration": shot["duration"]}} for _, shot in flatten_shots(project)]
            if len(plans) != len(video_job_ids) or not plans:
                raise ScriptwritingError("生成片段尚未完整，不能合成影片。")
            clips = []
            for index, (plan, job_id) in enumerate(zip(plans, video_job_ids)):
                media = await editor.import_media({"job_id": job_id})
                requested = float(plan["payload"]["duration"])
                if media["duration"] + 1 / 24 < requested:
                    raise ScriptwritingError(f"第 {index + 1} 段影片短於分鏡時長，請重新生成該段。")
                clips.append({"id": f"clip-{index + 1}", "media_id": media["id"], "in": 0,
                              "out": min(requested, media["duration"]), "volume": 1, "speed": 1,
                              "fade_in": 0, "fade_out": 0})
            edited = editor.create_project({"name": f"{project['title']} · 自動影片", "width": dimensions["output_width"],
                                            "height": dimensions["output_height"], "fps": dimensions["fps"],
                                            "clips": clips, "audio_clips": [], "overlays": []})
            pipeline["editor_project_id"] = edited["id"]
            await progress_callback({"editor_project_id": edited["id"]})
            while editor.export_task is not None and not editor.export_task.done():
                await asyncio.sleep(0.25)
            export = editor.start_export(edited["id"])
            pipeline["export_id"] = export["id"]
            await progress_callback({"export_id": export["id"]})
            while True:
                export = editor.exports[export["id"]]
                if export["status"] == "completed":
                    editor.export_path(export["id"])
                    return {"video_url": export["url"], "export_id": export["id"], "editor_project_id": edited["id"]}
                if export["status"] in {"failed", "cancelled", "interrupted"}:
                    raise ScriptwritingError(export.get("error") or "影片合成已中斷；生成片段仍保留。")
                await progress_callback({"progress": 90 + float(export.get("progress", 0)) * 0.09})
                await asyncio.sleep(0.25)

    async def cancel_export(self, pipeline):
        export_id = pipeline.get("export_id")
        editor = self.app.get("editor")
        if export_id and editor and export_id in editor.exports:
            await editor.cancel_export(export_id)
