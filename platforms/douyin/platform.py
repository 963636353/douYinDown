import time
from typing import Callable, List, Optional

import requests

from core.base_platform import BasePlatform, VideoInfo, VideoVariant
from platforms.douyin import api, url_parser


class DouyinPlatform(BasePlatform):

    @property
    def name(self) -> str:
        return 'douyin'

    @property
    def display_name(self) -> str:
        return '\u6296\u97f3'

    @property
    def cookie_domain(self) -> str:
        return '.douyin.com'

    @property
    def login_url(self) -> str:
        return 'https://www.douyin.com/'

    @property
    def session_cookie_names(self) -> list:
        return ['sessionid', 'sid_tt']

    def extract_video_id(self, url: str) -> Optional[str]:
        return url_parser.extract_aweme_id(url)

    def extract_profile_id(self, url: str) -> Optional[str]:
        return url_parser.extract_sec_user_id(url)

    def sanitize_filename(self, title: str) -> str:
        return url_parser.sanitize_filename(title)

    def resolve_short_url(self, url: str) -> Optional[str]:
        return url_parser.resolve_short_url(url)

    def build_download_headers(self, cookies: dict) -> dict:
        return api.build_headers(cookies)

    def get_video_info(self, video_id: str, cookies: dict) -> VideoInfo:
        params = api.build_params(video_id)
        data = self._signed_get_json(api.API_DETAIL, params, cookies)

        if data.get('status_code') != 0:
            raise Exception(f'API error: status_code={data.get("status_code")}')

        detail = data.get('aweme_detail')
        if not detail:
            reason = data.get('filter_detail', {}).get('filter_reason', 'unknown')
            raise Exception(f'Video unavailable: {reason}')

        return self._video_info_from_aweme(detail, video_id)

    def get_profile_videos(
        self,
        profile_id: str,
        cookies: dict,
        progress_callback: Optional[Callable[[int, int, bool], None]] = None,
        *,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
    ) -> List[VideoInfo]:
        videos = []
        seen_video_ids = set()
        seen_cursors = set()
        date_filter_enabled = start_time is not None or end_time is not None
        max_cursor = 0
        page = 0

        while page < 1000:
            if max_cursor in seen_cursors:
                break
            seen_cursors.add(max_cursor)
            page += 1

            params = api.build_user_post_params(profile_id, max_cursor=max_cursor, count=20)
            data = self._signed_get_json(api.API_USER_POST, params, cookies)

            status_code = data.get('status_code')
            if status_code not in (0, None):
                status_msg = data.get('status_msg') or data.get('msg') or 'unknown'
                raise Exception(f'Profile API error: status_code={status_code}, {status_msg}')

            should_stop_after_page = False
            for aweme in data.get('aweme_list') or []:
                aweme_id = str(aweme.get('aweme_id') or '')
                if not aweme_id or aweme_id in seen_video_ids:
                    continue

                info = None
                publish_time = self._extract_publish_time(aweme)
                if date_filter_enabled and publish_time is None:
                    try:
                        info = self.get_video_info(aweme_id, cookies)
                        publish_time = info.publish_time
                    except Exception:
                        seen_video_ids.add(aweme_id)
                        continue

                    if publish_time is None:
                        seen_video_ids.add(aweme_id)
                        continue

                if end_time is not None and publish_time is not None and publish_time > end_time:
                    seen_video_ids.add(aweme_id)
                    continue

                if start_time is not None and publish_time is not None and publish_time < start_time:
                    seen_video_ids.add(aweme_id)
                    should_stop_after_page = True
                    continue

                try:
                    if info is None:
                        info = self._video_info_from_aweme(aweme, aweme_id)
                except Exception:
                    try:
                        info = self.get_video_info(aweme_id, cookies)
                    except Exception:
                        continue

                if info.publish_time is None and publish_time is not None:
                    info.publish_time = publish_time
                if date_filter_enabled and info.publish_time is None:
                    seen_video_ids.add(aweme_id)
                    continue

                seen_video_ids.add(info.video_id)
                videos.append(info)

            has_more = bool(data.get('has_more')) and not should_stop_after_page
            if progress_callback:
                progress_callback(len(videos), page, has_more)
            if should_stop_after_page or not has_more:
                break

            next_cursor = data.get('max_cursor')
            if next_cursor is None:
                break
            try:
                next_cursor = int(next_cursor)
            except (TypeError, ValueError):
                break
            if next_cursor == max_cursor:
                break

            max_cursor = next_cursor
            time.sleep(0.25)

        return videos

    def _signed_get_json(self, endpoint: str, params: dict, cookies: dict) -> dict:
        params_str = '&'.join([f'{k}={v}' for k, v in params.items()])
        signed_params = api.generate_abogus(params_str)
        headers = api.build_headers(cookies)

        response = requests.get(
            f'{endpoint}?{signed_params}',
            headers=headers,
            cookies=cookies,
            timeout=15,
        )
        response.raise_for_status()
        return response.json()

    def _video_info_from_aweme(self, detail: dict, fallback_video_id: str = '') -> VideoInfo:
        video_id = str(detail.get('aweme_id') or fallback_video_id)
        if not video_id:
            raise Exception('Missing video ID')

        desc = detail.get('desc') or 'untitled'
        video_info = detail.get('video', {}) or {}
        video_variants = self._extract_video_variants(video_info)
        video_url = video_variants[0].url if video_variants else None
        if not video_url:
            raise Exception('No video download URL found')

        music_info = detail.get('music', {}) or {}
        audio_play = music_info.get('play_url', {}) or {}
        audio_url = self._first_url(audio_play.get('url_list', []))

        cover = video_info.get('cover') or video_info.get('origin_cover') or {}
        cover_url = self._first_url(cover.get('url_list', []))

        filename = f'{self.sanitize_filename(desc)}_{video_id}.mp4'
        publish_time = self._extract_publish_time(detail)

        return VideoInfo(
            video_id=video_id,
            title=desc,
            video_url=video_url,
            audio_url=audio_url,
            cover_url=cover_url,
            duration=video_info.get('duration'),
            video_variants=video_variants,
            extra={'filename': filename},
            publish_time=publish_time,
        )

    def _extract_video_variants(self, video_info: dict) -> List[VideoVariant]:
        variants: List[VideoVariant] = []
        seen_urls = set()

        for item in video_info.get('bit_rate', []) or []:
            item = item or {}
            play_addr = (item or {}).get('play_addr', {}) or {}
            self._append_video_variant(variants, seen_urls, play_addr, item)

        for key, label_hint in (
            ('play_addr_h264', 'H.264'),
            ('play_addr_bytevc1', 'H.265'),
            ('play_addr', 'default'),
        ):
            play_addr = video_info.get(key, {}) or {}
            self._append_video_variant(
                variants,
                seen_urls,
                play_addr,
                {'gear_name': label_hint},
            )

        variants.sort(
            key=lambda item: (
                item.height or 0,
                item.bitrate or 0,
                item.width or 0,
                item.size or 0,
            ),
            reverse=True,
        )
        return variants

    def _append_video_variant(
        self,
        variants: List[VideoVariant],
        seen_urls: set,
        play_addr: dict,
        item: dict,
    ) -> None:
        url = self._first_url((play_addr or {}).get('url_list', []))
        if not url or url in seen_urls:
            return

        width = self._to_int((play_addr or {}).get('width') or item.get('width'))
        height = self._to_int((play_addr or {}).get('height') or item.get('height'))
        bitrate = self._to_int(
            item.get('bit_rate')
            or item.get('bitrate')
            or (play_addr or {}).get('bit_rate')
            or (play_addr or {}).get('bitrate')
        )
        size = self._to_int((play_addr or {}).get('data_size') or item.get('data_size'))
        quality = str(
            item.get('gear_name')
            or item.get('quality_type')
            or item.get('video_quality')
            or ''
        )

        variants.append(
            VideoVariant(
                label=self._build_variant_label(width, height, bitrate, quality, size),
                url=url,
                width=width,
                height=height,
                bitrate=bitrate,
                quality=quality or None,
                size=size,
            )
        )
        seen_urls.add(url)

    @staticmethod
    def _build_variant_label(
        width: Optional[int],
        height: Optional[int],
        bitrate: Optional[int],
        quality: str,
        size: Optional[int],
    ) -> str:
        parts = []
        if height:
            parts.append(f'{height}p')
        elif width:
            parts.append(f'{width}px')
        elif quality:
            parts.append(quality)
        else:
            parts.append('default')

        if bitrate:
            parts.append(f'{round(bitrate / 1000)} kbps')
        if size:
            parts.append(f'{size / 1024 / 1024:.1f} MB')
        if quality and quality not in parts:
            parts.append(quality)

        return ' / '.join(parts)

    @staticmethod
    def _to_int(value) -> Optional[int]:
        try:
            if value is None or value == '':
                return None
            return int(value)
        except (TypeError, ValueError):
            return None

    @classmethod
    def _extract_publish_time(cls, detail: dict) -> Optional[int]:
        for key in ('create_time', 'createTime', 'publish_time', 'publishTime'):
            timestamp = cls._to_int((detail or {}).get(key))
            if timestamp is None:
                continue
            if timestamp > 10_000_000_000:
                timestamp = timestamp // 1000
            return timestamp
        return None

    @staticmethod
    def _first_url(urls):
        if isinstance(urls, list) and urls:
            return urls[0]
        if isinstance(urls, str) and urls:
            return urls
        return None
