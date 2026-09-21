"""Install pinned Real-ESRGAN release weights on the configured GPU host."""
import hashlib
import urllib.request
from pathlib import Path
from image_upscale import MODELS
from settings import SettingsStore


def verify(path, model):
    if path.stat().st_size != model['size']:
        return False
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest() == model['sha256']


def main():
    app_dir = Path(__file__).resolve().parent
    store = SettingsStore(app_dir / 'config.json', app_dir)
    if store.load_error or store.current.mode != 'local':
        raise RuntimeError(store.load_error or 'Run this installer on the local GPU host, not a remote client.')
    engine = Path(store.current.comfy_dir)
    if not (engine / 'main.py').is_file():
        raise RuntimeError('Install the local ComfyUI engine first.')
    root = engine / 'models/upscale_models'
    staging = app_dir / 'data/upscale_download'
    root.mkdir(parents=True, exist_ok=True)
    staging.mkdir(parents=True, exist_ok=True)
    for model in MODELS.values():
        target = root / model['filename']
        if target.exists():
            if not verify(target, model):
                raise RuntimeError(f'Existing model differs from official weights; preserved: {target.name}')
            print(f'Verified: {target.name}', flush=True)
            continue
        cached = staging / model['filename']
        if not cached.exists() or not verify(cached, model):
            partial = cached.with_suffix('.part')
            url = f"https://github.com/xinntao/Real-ESRGAN/releases/download/{model['tag']}/{model['filename']}"
            print(f'Downloading: {model["filename"]}', flush=True)
            with urllib.request.urlopen(url, timeout=60) as response, partial.open('wb') as out:
                while chunk := response.read(1024 * 1024):
                    out.write(chunk)
            if not verify(partial, model):
                raise RuntimeError(f'Checksum failed: {model["filename"]}')
            partial.replace(cached)
        if target.exists():
            raise RuntimeError('Target appeared during download; preserved.')
        cached.rename(target)
        print(f'Installed and verified: {target.name}', flush=True)
    print('Image upscale models ready. Refresh the model list in Studio.')


if __name__ == '__main__':
    main()
