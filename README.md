# ComfyUI-RunPodSpeed

Custom nodes for **RunPod-style** setups: fast **container NVMe** for models and runtime, slow **network volumes** (for example `/workspace`) for persistence. Errors and progress are printed to the ComfyUI process stdout so they appear in pod logs.

## Install

From your ComfyUI Python environment:

```bash
pip install -r custom_nodes/ComfyUI-RunPodSpeed/requirements.txt
```

That installs `huggingface_hub` and **`hf_transfer`** (parallel Rust downloader). The HF Downloader node sets `HF_HUB_ENABLE_HF_TRANSFER=1` when `hf_transfer` imports successfully. You can also set `HF_HUB_ENABLE_HF_TRANSFER=1` in RunPod’s environment so other Hub usage in the same process picks it up from startup.

## Machine startup (`start_remote.sh`)

Optional pod/container entrypoint: pulls **`master.tar.zst`** (or `RUNPODSPEED_HF_FILENAME`) from Hugging Face, extracts to **`/tmp/comfyui_nvme`**, creates **`/tmp/fast_models`** trees, writes **`extra_model_paths.yaml`** next to ComfyUI, then starts ComfyUI from the extracted tree. **No `/workspace` network volume** is required.

**System image:** `python3` with **`huggingface_hub`** installed (`pip install huggingface_hub` in the image or bootstrap layer).

| Variable | Required | Description |
|----------|----------|-------------|
| `RUNPODSPEED_HF_REPO_ID` | Yes | Hub repo id (e.g. `org/WorkingImage`) |
| `HF_TOKEN` | For private repos | Read token is enough for download |
| `RUNPODSPEED_HF_FILENAME` | No | Default `master.tar.zst` |
| `RUNPODSPEED_HF_REPO_TYPE` | No | Default `dataset` (use `model` if applicable) |
| `RUNPODSPEED_HF_REVISION` | No | Default `main` |

**RunPod template (`dockerArgs` under 4000 chars):** set the Hub env vars in the template environment, then use a one-liner that fetches and runs this script from GitHub `main` (pin a commit SHA in the URL if you want a fixed revision):

```bash
bash -c 'curl -fsSL "https://raw.githubusercontent.com/Tr1dae/ComfyUI-RunPodSpeed/main/start_remote.sh" -o /tmp/start_remote.sh && bash /tmp/start_remote.sh'
```

Debug: `huggingface-cli download "$RUNPODSPEED_HF_REPO_ID" master.tar.zst --local-dir /tmp/hf_test --repo-type dataset`

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

Set a token in the environment:

```bash
export HF_TOKEN=hf_...
```

- **Downloads** (HF Downloader): a **read** token is enough for private/gated content you can access.
- **Uploads** (HF State Uploader): use a **write** token (`write` role on [Hugging Face token settings](https://huggingface.co/settings/tokens)); otherwise uploads return 401/403.

Anonymous access is used when `HF_TOKEN` is unset (downloads only).

## Nodes

### RunPodSpeed HF Downloader

- **Inputs:** Hugging Face file URL (`https://huggingface.co/{org}/{repo}/resolve/{revision}/path/to/file.safetensors`), optional query string on the URL is ignored; target directory (default `/tmp/fast_models/checkpoints`).
- **Output:** `filename` string relative to `nvme_target_path`, with `/` separators, for wiring into standard loaders.
- Runs **synchronously** before downstream nodes execute. Creates the target directory if needed. Handles HTTP **401** / **429** with explicit log lines.

### RunPodSpeed State Packager

- **Inputs:** destination archive path (default `/workspace/master.tar.zst`); **`trigger_package`** (default off) so normal runs do not archive by accident.
- **Outputs:** `status` (short string) and **`archive_path`** (absolute path to the archive when packaging **succeeds**; empty string when skipped or on error).
- Resolves the ComfyUI root from **`folder_paths.base_path`**, then runs `tar` with **zstd** compression. If the archive file already exists, it is **moved** to `{dirname(archive)}/backups/{stem}_YYYY-MM-DD_HHMMSS.tar.zst` before writing a new one (local backups only; nothing is versioned on the Hub by this node).

**Excluded from the archive** (relative to the ComfyUI folder name inside the tarball): `models/`, `output/`, `outputs/`, `temp/`, `.git/`, and `__pycache__` trees (via wildcard excludes).

**Requirements:** GNU `tar` with **zstd** support (`tar --zstd`). Typical on Ubuntu RunPod images; Windows portable setups may not support `--zstd`—this node is aimed at Linux containers.

### RunPodSpeed HF State Uploader

Uploads a local `.tar.zst` to a **fixed path** in a Hugging Face **model** or **dataset** repo. Each successful run creates a new Hub commit that **replaces** the file at `path_in_repo` (for example `master.tar.zst`). There are **no extra backup copies on Hugging Face**—only the latest blob at that path matters for your “pull on cold boot” workflow.

- **Inputs:** `archive_path` (wire from packager **`archive_path`** or set the same path manually); `hf_repo_id` (e.g. `org/private-state`); `path_in_repo` (default `master.tar.zst`); **`repo_type`** `model` or `dataset`; **`trigger_upload`** (default off); optional **`packager_status`** — if connected and non-empty, upload runs **only** when the value starts with `success:` (match the State Packager success prefix). If left disconnected/empty, upload depends only on `trigger_upload` and file existence (use the optional wire to avoid pushing after a failed or skipped packager).

- **Output:** `status` string.

**Hub setup:** Create an empty **private** model or dataset repository. Set **`HF_TOKEN`** to a token with **write** access. Large archives use **Git LFS** on the Hub; first push can take a long time depending on size and link speed.

**Chain example:** State Packager `status` → HF State Uploader `packager_status`; State Packager `archive_path` → HF State Uploader `archive_path`. Enable **`trigger_package`** then **`trigger_upload`** when you intend to publish.

**Pull on another machine** (read token or public URL as appropriate):

```bash
huggingface-cli download <hf_repo_id> <path_in_repo> --local-dir /path/to/dir --repo-type dataset
```

(`--repo-type model` if you used a model repo.) Or use `hf_hub_download(repo_id=..., filename=..., local_dir=..., repo_type=...)` in Python.

## Logs

Failures use `print()` and `traceback.print_exc()` so messages show in the ComfyUI terminal without failing silently.
