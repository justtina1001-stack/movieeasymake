"""Pinned official Comfy-Org weights for Qwen-Image-2.1."""
REVISION = "ace0edeb3791a594ddfa36ed5f41a178a394e921"
REPO = "Comfy-Org/Qwen-Image-2.1"
LICENSE_URL = "https://huggingface.co/Qwen/Qwen-Image-2.1/blob/main/LICENSE"
MODEL_FILES = (
    ("diffusion_models", "qwen_image_2.1_int8_convrot.safetensors", 7256783064,
     "cb74113cb03faecd79611b01fd7fd642f0aa60d6f0b95086abee214d75eaa57d"),
    ("text_encoders", "qwen3vl_8b_int8_convrot.safetensors", 9350798360,
     "8bfd0f6e12abf2d2d697ecc888e5e90b0d6741d6708f05799f53afa560452e8f"),
    ("vae", "qwen_image_2.1_vae_bf16.safetensors", 675509688,
     "bb21f7473051e1ac368515dd3f2e15cd44d7a11748ee8823e1ddca3e4876b7c9"),
)

# Turbo replaces only the visual transformer; the encoder and VAE stay shared.
TURBO_REVISION = "72a49122613a4f9afbcc443426d6c765c0c383cf"
TURBO_MODEL_FILE = (
    "diffusion_models", "qwen_image_2.1_turbo_int8_convrot.safetensors", 7256787829,
    "8144a0182964aa8403ad6dbc011db837304167d235161b9aabaa8532e967314c",
)
TURBO_STEPS = 8
# Official final sigmas: no additional resolution shift or KSampler scheduler.
# ManualSigmas does not append the terminal zero required by SamplerCustom.
TURBO_SIGMAS = (1.0, 0.978453, 0.95418, 0.926626, 0.89508, 0.845148, 0.704534, 0.414568, 0.0)
