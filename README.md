# ComfyUI-RunPodSpeed

Custom nodes for **RunPod-style** setups: fast **container NVMe** for models and runtime, slow **network volumes** (for example `/workspace`) for persistence. Errors and progress are printed to the ComfyUI process stdout so they appear in pod logs.

## Install

From your ComfyUI Python environment:

```bash
pip install -r custom_nodes/ComfyUI-RunPodSpeed/requirements.txt
```

That installs `huggingface_hub` and **`hf_transfer`** (parallel Rust downloader). The HF Downloader node sets `HF_HUB_ENABLE_HF_TRANSFER=1` when `hf_transfer` imports successfully. You can also set `HF_HUB_ENABLE_HF_TRANSFER=1` in RunPod’s environment so other Hub usage in the same process picks it up from startup.

## Configure `extra_model_paths.yaml`

So loaders resolve files under `/tmp/fast_models/...`, add a block to **`ComfyUI/extra_model_paths.yaml`** (next to ComfyUI’s main folder), or pass the file with `--extra-model-paths-config`.

Use directory names that match where **RunPodSpeed HF Downloader** saves files (its `nvme_target_path` per asset type). Example layout on NVMe:

- `/tmp/fast_models/checkpoints`
- `/tmp/fast_models/unet`
- `/tmp/fast_models/clip`
- `/tmp/fast_models/loras`

Example YAML (keys `unet` and `clip` are ComfyUI **legacy** names; they map to diffusion and text encoder paths):

```yaml
runpod_fast_models:
  base_path: /tmp/fast_models
  checkpoints: |
    checkpoints
  unet: |
    unet
  clip: |
    clip
  loras: |
    loras
```

Restart ComfyUI after editing paths. Point **RunPodSpeed HF Downloader** `nvme_target_path` at the folder that matches the loader (for example checkpoints → `.../checkpoints`). Connect the node’s **`filename`** output to a loader input via **Convert widget to input** so the loader uses the downloaded name (including any subpath, e.g. `fp16/model.safetensors`).

### Private or gated repos

Set a read token in the environment:

```bash
export HF_TOKEN=hf_...
```

Anonymous access is used when `HF_TOKEN` is unset.

## Nodes

### RunPodSpeed HF Downloader

- **Inputs:** Hugging Face file URL (`https://huggingface.co/{org}/{repo}/resolve/{revision}/path/to/file.safetensors`), optional query string on the URL is ignored; target directory (default `/tmp/fast_models/checkpoints`).
- **Output:** `filename` string relative to `nvme_target_path`, with `/` separators, for wiring into standard loaders.
- Runs **synchronously** before downstream nodes execute. Creates the target directory if needed. Handles HTTP **401** / **429** with explicit log lines.

### RunPodSpeed State Packager

- **Inputs:** destination archive path (default `/workspace/master.tar.zst`); **`trigger_package`** (default off) so normal runs do not archive by accident.
- **Output:** short `status` string.
- Resolves the ComfyUI root from **`folder_paths.base_path`**, then runs `tar` with **zstd** compression. If the archive file already exists, it is **moved** to `{dirname(archive)}/backups/{stem}_YYYY-MM-DD_HHMMSS.tar.zst` before writing a new one.

**Excluded from the archive** (relative to the ComfyUI folder name inside the tarball): `models/`, `output/`, `outputs/`, `temp/`, `.git/`, and `__pycache__` trees (via wildcard excludes).

**Requirements:** GNU `tar` with **zstd** support (`tar --zstd`). Typical on Ubuntu RunPod images; Windows portable setups may not support `--zstd`—this node is aimed at Linux containers.

## Logs

Failures use `print()` and `traceback.print_exc()` so messages show in the ComfyUI terminal without failing silently.
