"""
RunPodSpeed nodes: HF download to NVMe, state packager, and Hub state archive upload.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import traceback
from datetime import datetime
from urllib.parse import unquote, urlparse

import folder_paths

try:
    from huggingface_hub import HfApi, hf_hub_download
    from huggingface_hub.errors import GatedRepoError, HfHubHTTPError, RepositoryNotFoundError
except ImportError:
    HfApi = None  # type: ignore[misc, assignment]
    hf_hub_download = None  # type: ignore[misc, assignment]
    HfHubHTTPError = Exception  # type: ignore[misc, assignment]
    RepositoryNotFoundError = Exception  # type: ignore[misc, assignment]
    GatedRepoError = Exception  # type: ignore[misc, assignment]


_HF_RESOLVE_HOST = re.compile(
    r"^(?:www\.)?huggingface\.co$",
    re.IGNORECASE,
)


def _parse_hf_resolve_url(direct_url: str) -> tuple[str, str, str]:
    """
    Parse a Hugging Face file URL into (repo_id, revision, filename_in_repo).

    Expected path shape: /{org}/{repo}/resolve/{revision}/{file...}
    Query strings on the URL are ignored (path-only).
    """
    raw = (direct_url or "").strip()
    if not raw:
        raise ValueError("direct_url is empty")

    parsed = urlparse(raw)
    host = (parsed.netloc or "").lower()
    if not _HF_RESOLVE_HOST.match(host):
        raise ValueError(f"URL host is not huggingface.co: {parsed.netloc!r}")

    path = parsed.path or ""
    segments = [unquote(s) for s in path.split("/") if s]
    try:
        ridx = segments.index("resolve")
    except ValueError as e:
        raise ValueError("URL path must contain /resolve/<revision>/<filename>") from e

    if ridx < 2 or ridx + 2 >= len(segments):
        raise ValueError("Malformed Hugging Face resolve URL (missing org, repo, revision, or file)")

    org, repo = segments[ridx - 2], segments[ridx - 1]
    revision = segments[ridx + 1]
    file_parts = segments[ridx + 2 :]
    if not file_parts:
        raise ValueError("No file path after revision segment")
    filename = "/".join(file_parts)
    repo_id = f"{org}/{repo}"
    return repo_id, revision, filename


def _configure_hf_transfer_env() -> None:
    try:
        import hf_transfer  # noqa: F401

        os.environ["HF_HUB_ENABLE_HF_TRANSFER"] = "1"
    except ImportError:
        os.environ["HF_HUB_ENABLE_HF_TRANSFER"] = "0"


def _print_hf_http_error(err: HfHubHTTPError) -> None:
    _print_hf_hub_http_error("RunPodSpeed_HFDownloader", err)


def _print_hf_hub_http_error(component: str, err: HfHubHTTPError) -> None:
    msg = str(err)
    status = getattr(getattr(err, "response", None), "status_code", None)
    if status == 401:
        print(f"[{component}] HTTP 401 (unauthorized). Check HF_TOKEN (read for downloads, write for uploads). {msg}")
    elif status == 403:
        print(f"[{component}] HTTP 403 (forbidden). Token may lack write access or repo is restricted. {msg}")
    elif status == 429:
        print(f"[{component}] HTTP 429 (rate limited). Retry later or reduce parallel requests. {msg}")
    else:
        print(f"[{component}] Hub HTTP error (status={status}): {msg}")


class RunPodSpeed_HFDownloader:
    """Download a single repo file to NVMe via huggingface_hub (optional hf_transfer)."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "direct_url": ("STRING", {"default": "", "multiline": True}),
                "nvme_target_path": ("STRING", {"default": "/tmp/fast_models/checkpoints"}),
            },
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("filename",)
    FUNCTION = "download"
    CATEGORY = "RunPodSpeed"

    def download(self, direct_url: str, nvme_target_path: str):
        if hf_hub_download is None:
            err = "huggingface_hub is not installed. pip install -r requirements.txt in ComfyUI-RunPodSpeed"
            print(f"[RunPodSpeed_HFDownloader] {err}")
            raise RuntimeError(err)

        try:
            repo_id, revision, filename = _parse_hf_resolve_url(direct_url)
        except ValueError as e:
            print(f"[RunPodSpeed_HFDownloader] URL parse failed: {e}")
            raise RuntimeError(f"RunPodSpeed_HFDownloader: {e}") from e

        target = os.path.abspath(os.path.expanduser(nvme_target_path.strip()))
        os.makedirs(target, exist_ok=True)

        _configure_hf_transfer_env()
        token = os.environ.get("HF_TOKEN")

        try:
            path = hf_hub_download(
                repo_id=repo_id,
                filename=filename,
                revision=revision,
                local_dir=target,
                token=token,
                local_files_only=False,
            )
        except HfHubHTTPError as e:
            _print_hf_http_error(e)
            traceback.print_exc()
            raise
        except RepositoryNotFoundError as e:
            print(f"[RunPodSpeed_HFDownloader] Repository not found: {e}")
            traceback.print_exc()
            raise
        except GatedRepoError as e:
            print(f"[RunPodSpeed_HFDownloader] Gated repo — accept terms on the Hub and set HF_TOKEN: {e}")
            traceback.print_exc()
            raise
        except Exception as e:
            print(f"[RunPodSpeed_HFDownloader] Download failed: {e}")
            traceback.print_exc()
            raise

        rel = os.path.relpath(os.path.abspath(path), target)
        rel_posix = rel.replace(os.sep, "/")
        print(f"[RunPodSpeed_HFDownloader] OK repo={repo_id} file={rel_posix} -> {path}")
        return (rel_posix,)


class RunPodSpeed_StatePackager:
    """Tar.zst the ComfyUI install tree to a network path, excluding heavy/volatile dirs."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "network_archive_path": ("STRING", {"default": "/workspace/master.tar.zst"}),
                "trigger_package": ("BOOLEAN", {"default": False}),
            },
        }

    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("status", "archive_path")
    FUNCTION = "package"
    CATEGORY = "RunPodSpeed"

    def package(self, network_archive_path: str, trigger_package: bool):
        archive = os.path.abspath(os.path.expanduser(network_archive_path.strip()))
        if not trigger_package:
            msg = "skipped (trigger_package=False)"
            print(f"[RunPodSpeed_StatePackager] {msg}")
            return (msg, "")

        archive_dir = os.path.dirname(archive)
        if archive_dir:
            os.makedirs(archive_dir, exist_ok=True)

        comfy_root = os.path.abspath(folder_paths.base_path)
        parent = os.path.dirname(comfy_root)
        folder_name = os.path.basename(comfy_root)
        if not folder_name or parent == comfy_root:
            err = f"Could not split ComfyUI root into parent/folder: {comfy_root!r}"
            print(f"[RunPodSpeed_StatePackager] {err}")
            return (f"error: {err}", "")

        backup_note = ""
        if os.path.isfile(archive):
            backup_root = os.path.join(archive_dir, "backups")
            os.makedirs(backup_root, exist_ok=True)
            stem, suffix = os.path.splitext(os.path.basename(archive))
            if stem.endswith(".tar") and suffix == ".zst":
                stem, suffix = stem[:-4], ".tar.zst"
            ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
            backup_name = f"{stem}_{ts}{suffix}"
            backup_path = os.path.join(backup_root, backup_name)
            shutil.move(archive, backup_path)
            backup_note = f" moved previous archive to {backup_path}"

        p = folder_name
        exclude_args = [
            f"--exclude={p}/models",
            f"--exclude={p}/output",
            f"--exclude={p}/outputs",
            f"--exclude={p}/temp",
            f"--exclude={p}/.git",
            f"--exclude={p}/__pycache__",
            "--wildcards-match-slash",
            "--exclude=*/__pycache__/*",
        ]

        cmd = [
            "tar",
            "--zstd",
            *exclude_args,
            "-cf",
            archive,
            "-C",
            parent,
            folder_name,
        ]

        print(f"[RunPodSpeed_StatePackager] Running: tar --zstd ... -cf {archive} -C {parent} {folder_name}")
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.stdout:
            print(proc.stdout.rstrip())
        if proc.stderr:
            print(f"[RunPodSpeed_StatePackager] tar stderr:\n{proc.stderr.rstrip()}")

        if proc.returncode != 0:
            err = f"tar failed with exit code {proc.returncode}"
            print(f"[RunPodSpeed_StatePackager] {err}")
            return (f"error: {err}{backup_note}", "")

        ok = f"success: wrote {archive}{backup_note}"
        print(f"[RunPodSpeed_StatePackager] {ok}")
        return (ok, archive)


class RunPodSpeed_HFStateUploader:
    """Upload a local state archive to a Hugging Face repo path (overwrite remote on each push)."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "archive_path": ("STRING", {"default": "/workspace/master.tar.zst"}),
                "hf_repo_id": ("STRING", {"default": ""}),
                "path_in_repo": ("STRING", {"default": "master.tar.zst"}),
                "repo_type": (["model", "dataset"], {"default": "dataset"}),
                "trigger_upload": ("BOOLEAN", {"default": False}),
            },
            "optional": {
                "packager_status": ("STRING", {"default": ""}),
            },
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("status",)
    FUNCTION = "upload"
    CATEGORY = "RunPodSpeed"

    def upload(
        self,
        archive_path: str,
        hf_repo_id: str,
        path_in_repo: str,
        repo_type: str,
        trigger_upload: bool,
        packager_status: str = "",
    ):
        if HfApi is None:
            err = "huggingface_hub is not installed. pip install -r requirements.txt in ComfyUI-RunPodSpeed"
            print(f"[RunPodSpeed_HFStateUploader] {err}")
            return (f"error: {err}",)

        if not trigger_upload:
            msg = "skipped (trigger_upload=False)"
            print(f"[RunPodSpeed_HFStateUploader] {msg}")
            return (msg,)

        ps = (packager_status or "").strip()
        if ps and not ps.startswith("success:"):
            msg = "skipped (packager_status does not start with success: — not uploading)"
            print(f"[RunPodSpeed_HFStateUploader] {msg}")
            return (msg,)

        token = os.environ.get("HF_TOKEN")
        if not token:
            msg = "error: HF_TOKEN is not set (write token required for uploads)"
            print(f"[RunPodSpeed_HFStateUploader] {msg}")
            return (msg,)

        rid = (hf_repo_id or "").strip()
        if not rid:
            msg = "error: hf_repo_id is empty"
            print(f"[RunPodSpeed_HFStateUploader] {msg}")
            return (msg,)

        local = os.path.abspath(os.path.expanduser(archive_path.strip()))
        if not os.path.isfile(local):
            msg = f"error: archive file not found: {local}"
            print(f"[RunPodSpeed_HFStateUploader] {msg}")
            return (msg,)

        remote_path = (path_in_repo or "master.tar.zst").strip().lstrip("/")
        if not remote_path:
            msg = "error: path_in_repo is empty"
            print(f"[RunPodSpeed_HFStateUploader] {msg}")
            return (msg,)

        api = HfApi(token=token)
        commit_message = "RunPodSpeed state archive (overwrite)"
        try:
            result = api.upload_file(
                path_or_fileobj=local,
                path_in_repo=remote_path,
                repo_id=rid,
                repo_type=repo_type,
                token=token,
                commit_message=commit_message,
            )
        except HfHubHTTPError as e:
            _print_hf_hub_http_error("RunPodSpeed_HFStateUploader", e)
            traceback.print_exc()
            return (f"error: Hub HTTP: {e}",)
        except RepositoryNotFoundError as e:
            print(f"[RunPodSpeed_HFStateUploader] Repository not found: {e}")
            traceback.print_exc()
            return (f"error: {e}",)
        except Exception as e:
            print(f"[RunPodSpeed_HFStateUploader] Upload failed: {e}")
            traceback.print_exc()
            return (f"error: {e}",)

        extra = ""
        commit_url = getattr(result, "commit_url", None)
        if commit_url:
            extra = f" commit_url={commit_url}"
        ok = f"success: uploaded to {rid} ({repo_type}) at {remote_path}{extra}"
        print(f"[RunPodSpeed_HFStateUploader] {ok}")
        return (ok,)
