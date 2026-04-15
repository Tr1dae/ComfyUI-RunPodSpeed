#!/bin/bash
# Machine startup: pull ComfyUI state archive from Hugging Face Hub, extract to NVMe,
# configure /tmp/fast_models + extra_model_paths.yaml, start ComfyUI.
#
# Required:
#   RUNPODSPEED_HF_REPO_ID   e.g. org/WorkingImage (dataset or model repo)
#
# Optional:
#   RUNPODSPEED_HF_FILENAME   default: master.tar.zst (must match Hub path_in_repo)
#   RUNPODSPEED_HF_REPO_TYPE  default: dataset  (or: model)
#   RUNPODSPEED_HF_REVISION   default: main
#   HF_TOKEN                  required for private repos (read token is enough for download)
#
# System Python needs: pip install huggingface_hub
# Debug download: huggingface-cli download "$RUNPODSPEED_HF_REPO_ID" master.tar.zst --local-dir /tmp/hf_test --repo-type dataset
#
set -e

readonly NVME_ROOT="/tmp/comfyui_nvme"
readonly COMFYUI_DIR="$NVME_ROOT/ComfyUI"
readonly FAST_MODELS_DIR="/tmp/fast_models"
readonly RUNPODSPEED_DL_DIR="/tmp/runpodspeed_hf_dl"

# Execute script if exists
execute_script() {
    local script_path=$1
    local script_msg=$2
    if [[ -f ${script_path} ]]; then
        echo "${script_msg}"
        bash ${script_path}
    fi
}

require_hf_repo_id() {
    if [[ -z "${RUNPODSPEED_HF_REPO_ID:-}" ]]; then
        echo "ERROR: RUNPODSPEED_HF_REPO_ID is not set." >&2
        echo "Example: export RUNPODSPEED_HF_REPO_ID=Tridae/WorkingImage" >&2
        exit 1
    fi
}

# Download state archive using system Python (works before ComfyUI venv exists).
download_hf_archive() {
    local filename="${RUNPODSPEED_HF_FILENAME:-master.tar.zst}"
    local repo_type="${RUNPODSPEED_HF_REPO_TYPE:-dataset}"
    local revision="${RUNPODSPEED_HF_REVISION:-main}"

    echo "Downloading ${filename} from ${RUNPODSPEED_HF_REPO_ID} (repo_type=${repo_type}, revision=${revision})..." >&2

    if ! command -v python3 >/dev/null 2>&1; then
        echo "ERROR: python3 not found; install Python 3 to bootstrap from Hugging Face." >&2
        exit 1
    fi

    rm -rf "$RUNPODSPEED_DL_DIR"
    mkdir -p "$RUNPODSPEED_DL_DIR"

    export RUNPODSPEED_DL_DIR
    export RUNPODSPEED_HF_REPO_ID
    export RUNPODSPEED_HF_FILENAME="$filename"
    export RUNPODSPEED_HF_REPO_TYPE="$repo_type"
    export RUNPODSPEED_HF_REVISION="$revision"
    # HF_TOKEN optional in env for public repos

    local archive_path
    archive_path="$(python3 <<'PY'
import os
import sys
from huggingface_hub import hf_hub_download

repo_id = os.environ["RUNPODSPEED_HF_REPO_ID"]
filename = os.environ.get("RUNPODSPEED_HF_FILENAME", "master.tar.zst")
repo_type = os.environ.get("RUNPODSPEED_HF_REPO_TYPE", "dataset")
revision = os.environ.get("RUNPODSPEED_HF_REVISION", "main")
local_dir = os.environ["RUNPODSPEED_DL_DIR"]
token = os.environ.get("HF_TOKEN") or None

try:
    path = hf_hub_download(
        repo_id=repo_id,
        filename=filename,
        local_dir=local_dir,
        repo_type=repo_type,
        revision=revision,
        token=token,
        local_files_only=False,
    )
except Exception as e:
    print(f"hf_hub_download failed: {e}", file=sys.stderr)
    sys.exit(1)
print(path)
PY
)"
    if [[ ! -f "$archive_path" ]]; then
        echo "ERROR: Download did not produce a file at: $archive_path" >&2
        exit 1
    fi
    echo "Download complete: $archive_path" >&2
    printf '%s\n' "$archive_path"
}

# Setup ssh — PUBLIC_KEY only (no /workspace key file).
setup_ssh() {
    if [[ ${PUBLIC_KEY:-} ]]; then
        echo "Setting up SSH..."
        mkdir -p ~/.ssh
        local auth_keys_file="/root/.ssh/authorized_keys"
        echo "$PUBLIC_KEY" > "$auth_keys_file"
        chmod 700 ~/.ssh
        chmod 600 "$auth_keys_file"

        if [ ! -f /etc/ssh/ssh_host_rsa_key ]; then
            ssh-keygen -t rsa -f /etc/ssh/ssh_host_rsa_key -q -N ''
        fi
        if [ ! -f /etc/ssh/ssh_host_dsa_key ]; then
            ssh-keygen -t dsa -f /etc/ssh/ssh_host_dsa_key -q -N ''
        fi
        if [ ! -f /etc/ssh/ssh_host_ecdsa_key ]; then
            ssh-keygen -t ecdsa -f /etc/ssh/ssh_host_ecdsa_key -q -N ''
        fi
        if [ ! -f /etc/ssh/ssh_host_ed25519_key ]; then
            ssh-keygen -t ed25519 -f /etc/ssh/ssh_host_ed25519_key -q -N ''
        fi

        service ssh start
    fi
}

export_env_vars() {
    echo "Exporting environment variables..."
    printenv | grep -E '^RUNPOD_|^PATH=|^_=' | awk -F = '{ print "export " $1 "=\"" $2 "\"" }' >> /etc/rp_environment
    echo 'source /etc/rp_environment' >> ~/.bashrc
}

bootstrap_comfyui_from_hf() {
    echo "Initiating HF-backed NVMe deployment..."

    require_hf_repo_id

    if [[ -z "${HF_TOKEN:-}" ]]; then
        echo "Note: HF_TOKEN is unset (OK for public repos only)." >&2
    fi

    local archive_path
    archive_path="$(download_hf_archive)"

    echo "Extracting snapshot to $NVME_ROOT..."
    mkdir -p "$NVME_ROOT"
    tar --zstd -xf "$archive_path" -C "$NVME_ROOT"
    echo "Extraction complete."

    rm -rf "$RUNPODSPEED_DL_DIR"
    echo "Removed staging directory $RUNPODSPEED_DL_DIR"

    if [[ ! -d "$COMFYUI_DIR" ]]; then
        echo "ERROR: After extract, ComfyUI directory not found: $COMFYUI_DIR"
        echo "Ensure the archive contains a ComfyUI/ folder at the top level."
        exit 1
    fi

    echo "Setting up NVMe high-speed model paths..."
    mkdir -p "$FAST_MODELS_DIR/checkpoints"
    mkdir -p "$FAST_MODELS_DIR/unet"
    mkdir -p "$FAST_MODELS_DIR/clip"
    mkdir -p "$FAST_MODELS_DIR/loras"
    mkdir -p "$FAST_MODELS_DIR/gguf"
    mkdir -p "$FAST_MODELS_DIR/text_encoders"
    mkdir -p "$FAST_MODELS_DIR/vae"
    mkdir -p "$FAST_MODELS_DIR/upscale_models"
    mkdir -p "$FAST_MODELS_DIR/audio_encoders"
    mkdir -p "$FAST_MODELS_DIR/model_patches"
    mkdir -p "$FAST_MODELS_DIR/hypernetworks"
    mkdir -p "$FAST_MODELS_DIR/controlnet"
    mkdir -p "$FAST_MODELS_DIR/style_models"
    mkdir -p "$FAST_MODELS_DIR/embeddings"
    mkdir -p "$FAST_MODELS_DIR/diffusers"
    mkdir -p "$FAST_MODELS_DIR/vae_approx"

    echo "Configuring extra_model_paths.yaml for NVMe speeds..."
    cat <<EOF > "$COMFYUI_DIR/extra_model_paths.yaml"
runpod_speed_nvme:
    base_path: $FAST_MODELS_DIR
    checkpoints: checkpoints
    unet: unet
    clip: clip
    loras: loras
    diffusion_models: gguf
    text_encoders: text_encoders
    vae: vae
    upscale_models: upscale_models
    audio_encoders: audio_encoders
    model_patches: model_patches
    hypernetworks: hypernetworks
    controlnet: controlnet
    style_models: style_models
    embeddings: embeddings
    diffusers: diffusers
    vae_approx: vae_approx


EOF

    echo "Bootstrap complete (no /workspace symlinks; models and I/O live on NVMe from archive)."
}

start_comfyui() {
    echo "Starting ComfyUI from NVMe..."
    cd "$COMFYUI_DIR"

    export LD_LIBRARY_PATH=/usr/lib/x86_64-linux-gnu:/usr/local/cuda/lib64:$LD_LIBRARY_PATH

    local pod_id="${RUNPOD_POD_ID:-local}"

    mkdir -p "$COMFYUI_DIR/temp/${pod_id}"
    mkdir -p "$COMFYUI_DIR/logs"

    local log_file="$COMFYUI_DIR/logs/comfyui_${pod_id}.log"
    touch "$log_file"
    echo "Starting ComfyUI at $(date)" >> "$log_file"

    local frontend_url
    if [[ -n "${RUNPOD_POD_ID:-}" ]]; then
        frontend_url="https://${RUNPOD_POD_ID}-8188.proxy.runpod.net"
    else
        frontend_url="*"
    fi
    echo "Setting CORS header to allow origin: $frontend_url"

    "$COMFYUI_DIR/venv/bin/python" main.py \
        --listen --port 8188 \
        --log-stdout \
        --temp-directory "$COMFYUI_DIR/temp/${pod_id}" \
        --enable-cors-header "$frontend_url" \
        2>&1 | tee "$log_file" &

    echo "ComfyUI started successfully on NVMe. Logging to $log_file"
}

# --- EXECUTION PIPELINE ---
execute_script "/pre_start.sh" "Running pre-start script..."

setup_ssh
bootstrap_comfyui_from_hf
start_comfyui
export_env_vars

execute_script "/post_start.sh" "Running post_start script..."

echo ""
echo "RunPodSpeed HF bootstrap complete (NVMe + fast_models)."
echo "======================================================="
echo "ComfyUI: http://localhost:8188"
echo "Main access (if proxied): http://localhost:8080"
echo ""

sleep infinity
