"""Pinned Anima base and RGBA adapter files for native transparent illustration."""

BASE_REPO = "circlestone-labs/Anima"
BASE_REVISION = "f973fc41ec7545364ac9776c2440285f43ff2a30"
REPO = "localai0301/Anima-RGBA"
REVISION = "493fbdecc6f926de89542d444d8a07cdcc31809c"
LICENSE_URL = f"https://huggingface.co/{REPO}/blob/{REVISION}/README.md"
BASE_LICENSE_URL = f"https://huggingface.co/{BASE_REPO}/blob/{BASE_REVISION}/LICENSE.md"
MIN_COMFY_VERSION = "0.42.0"
STEPS = 30
CFG = 4.5
SAMPLER = "er_sde"
SCHEDULER = "simple"
LORA_STRENGTH = 1.0
MIN_DIMENSION = 512
MAX_DIMENSION = 1536
DIMENSION_STEP = 64
MAX_PIXELS = MAX_DIMENSION ** 2
QUALITY_PREFIX = "masterpiece, best quality, newest, safe, "
NEGATIVE_PROMPT = "worst quality, low quality, score_1, score_2, score_3, blurry, jpeg artifacts"
MODEL_FILES = (
    ("diffusion_models", "anima-base-v1.0.safetensors", 4182218328,
     "bd43b7cffe1ed1153d9c41e7beb2f18cb1273eafbaa3af3edd6a173dc90a006e"),
    ("text_encoders", "qwen_3_06b_base.safetensors", 1192135096,
     "cd2a512003e2f9f3cd3c32a9c3573f820bb28c940f73c57b1ddaa983d9223eba"),
    ("vae", "anima_rgba_vae.safetensors", 507612064,
     "d113c0f48b8fe2b64c11fe7cbaa2f005e16db48d2b8e59f9171dd6da05650c4e"),
    ("loras", "anima_rgba/anima_rgba_lora.safetensors", 183604992,
     "47dabf912dd0ea7913ee7b0cca3257469399c94ae1432f959ed3ab6047bdca0c"),
)
