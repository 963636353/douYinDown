import os
import time
import requests
from typing import Callable, Optional

ProgressCallback = Callable[[int, int], None]


def download_video(
    video_url: str,
    filepath: str,
    headers: dict,
    progress_callback: Optional[ProgressCallback] = None,
) -> int:
    """Stream-download a video file with optional progress reporting.

    Args:
        video_url: Direct download URL.
        filepath: Local path to save to.
        headers: HTTP headers for the request.
        progress_callback: Called as cb(downloaded_bytes, total_bytes).

    Returns:
        Total bytes downloaded.
    """
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    start = time.time()
    r = requests.get(video_url, headers=headers, stream=True, timeout=60)
    if r.status_code != 200:
        raise Exception(f'下载失败: HTTP {r.status_code}')

    total = int(r.headers.get('Content-Length', 0))
    downloaded = 0

    with open(filepath, 'wb') as f:
        for chunk in r.iter_content(chunk_size=65536):
            if chunk:
                f.write(chunk)
                downloaded += len(chunk)
                if progress_callback:
                    progress_callback(downloaded, total)

    elapsed = time.time() - start
    speed = downloaded / elapsed / 1024 / 1024 if elapsed > 0 else 0
    print(f'   完成 (速度: {speed:.1f} MB/s)')
    return downloaded
