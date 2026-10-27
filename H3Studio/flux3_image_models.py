"""Pinned official FLUX 3 Image Klein files for the tested 16 GB edit workflow."""

REPO = "black-forest-labs/flux-3-image-klein-comfy"
REVISION = "feef60b0aaf63a4773ace96636ebbcf43b881312"
LICENSE_URL = "https://huggingface.co/black-forest-labs/flux-3-image-klein/blob/main/LICENSE.md"
MIN_COMFY_VERSION = "0.41.3"
COMFY_REVISION = "8713616fcc321624e306bbd36be5906203dcb0dc"
STEPS = 16
REFERENCE_LIMIT = 1
REFERENCE_MEGAPIXELS = 1.0
MODEL_FILES = (
    ("diffusion_models", "flux3-image-klein-int8-convrot.safetensors", 5827874779,
     "847d8933548afd93df4af2cac2498e70891bf28d9dae06d695f536992e168fe2"),
    ("text_encoders", "qwen3.5_9b_flux3_prompt_upsampler_w4a8.safetensors", 6724090780,
     "e5d85a7421d197bf5a12474511936ecabce19a833252f16e9310a983194a4458"),
    ("text_encoders", "qwen3vl_4b_int8_convrot.safetensors", 4858559240,
     "32de113cbf8282b02cbff0794b637b9d6676da09314a1db5a30bcea9bf4ee01c"),
    ("vae", "flux2-vae.safetensors", 336211292,
     "868fe7b343cc8f3a19dbcfcafbc3d5f888802be3f89bd81b65b3621a066ce8f3"),
)
