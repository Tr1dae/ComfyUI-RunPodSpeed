"""
ComfyUI-RunPodSpeed: NVMe-first HF downloads and network-volume state archives for RunPod.
"""

from .nodes import RunPodSpeed_HFDownloader, RunPodSpeed_StatePackager

NODE_CLASS_MAPPINGS = {
    "RunPodSpeed_HFDownloader": RunPodSpeed_HFDownloader,
    "RunPodSpeed_StatePackager": RunPodSpeed_StatePackager,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "RunPodSpeed_HFDownloader": "RunPodSpeed HF Downloader",
    "RunPodSpeed_StatePackager": "RunPodSpeed State Packager",
}

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
