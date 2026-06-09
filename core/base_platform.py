from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable, List, Optional


@dataclass
class VideoVariant:
    """A downloadable video stream candidate."""
    label: str
    url: str
    width: Optional[int] = None
    height: Optional[int] = None
    bitrate: Optional[int] = None
    quality: Optional[str] = None
    size: Optional[int] = None


@dataclass
class VideoInfo:
    """Platform-agnostic video metadata returned by get_video_info()."""
    video_id: str
    title: str
    video_url: str
    audio_url: Optional[str] = None
    cover_url: Optional[str] = None
    duration: Optional[int] = None
    video_variants: List[VideoVariant] = field(default_factory=list)
    extra: dict = field(default_factory=dict)
    publish_time: Optional[int] = None


class BasePlatform(ABC):
    """Interface that every platform module must implement."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique lowercase identifier, e.g. 'douyin'."""

    @property
    @abstractmethod
    def display_name(self) -> str:
        """Human-readable name, e.g. '抖音'."""

    @property
    @abstractmethod
    def cookie_domain(self) -> str:
        """Domain for cookie scoping, e.g. '.douyin.com'."""

    @property
    @abstractmethod
    def login_url(self) -> str:
        """URL to navigate to for login."""

    @property
    @abstractmethod
    def session_cookie_names(self) -> list:
        """Cookie names that indicate a valid session, e.g. ['sessionid', 'sid_tt']."""

    @abstractmethod
    def extract_video_id(self, url: str) -> Optional[str]:
        """Parse a video/note ID from any supported URL format. Return None if invalid."""

    @abstractmethod
    def get_video_info(self, video_id: str, cookies: dict) -> VideoInfo:
        """Fetch video metadata and direct download URL.
        The platform handles its own request signing here."""

    @abstractmethod
    def sanitize_filename(self, title: str) -> str:
        """Strip filesystem-unsafe characters from a title string."""

    @abstractmethod
    def build_download_headers(self, cookies: dict) -> dict:
        """HTTP headers needed for the video download request."""

    @abstractmethod
    def resolve_short_url(self, url: str) -> Optional[str]:
        """Follow redirects to get the final canonical URL."""

    def extract_profile_id(self, url: str) -> Optional[str]:
        """Parse a creator/profile ID from a supported homepage URL."""
        return None

    def get_profile_videos(
        self,
        profile_id: str,
        cookies: dict,
        progress_callback: Optional[Callable[[int, int, bool], None]] = None,
        *,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
    ) -> List[VideoInfo]:
        """Fetch all downloadable videos for a creator/profile."""
        raise NotImplementedError(f'{self.display_name} does not support profile parsing')
