"""
ComfyUI-RunPodSpeed: NVMe-first HF downloads and network-volume state archives for RunPod.
"""

from .nodes import RunPodSpeed_HFDownloader, RunPodSpeed_HFStateUploader, RunPodSpeed_StatePackager

NODE_CLASS_MAPPINGS = {
    "RunPodSpeed_HFDownloader": RunPodSpeed_HFDownloader,
    "RunPodSpeed_StatePackager": RunPodSpeed_StatePackager,
    "RunPodSpeed_HFStateUploader": RunPodSpeed_HFStateUploader,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "RunPodSpeed_HFDownloader": "RunPodSpeed HF Downloader",
    "RunPodSpeed_StatePackager": "RunPodSpeed State Packager",
    "RunPodSpeed_HFStateUploader": "RunPodSpeed HF State Uploader",
}

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
