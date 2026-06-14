import unittest
from unittest.mock import patch

from platforms.douyin.platform import DouyinPlatform


def make_aweme(aweme_id, create_time, is_top=0):
    return {
        'aweme_id': str(aweme_id),
        'desc': f'video {aweme_id}',
        'create_time': create_time,
        'is_top': is_top,
        'video': {
            'play_addr': {
                'url_list': [f'https://example.com/{aweme_id}.mp4'],
            },
        },
    }


def make_page(awemes, has_more=0, max_cursor=0):
    return {
        'status_code': 0,
        'aweme_list': awemes,
        'has_more': has_more,
        'max_cursor': max_cursor,
    }


class FakeDouyinPlatform(DouyinPlatform):
    def __init__(self, pages):
        self.pages = pages
        self.cursor_calls = []

    def _signed_get_json(self, endpoint, params, cookies):
        cursor = int(params.get('max_cursor', 0))
        self.cursor_calls.append(cursor)
        return self.pages[cursor]


class DouyinProfilePaginationTest(unittest.TestCase):
    def test_old_top_aweme_does_not_stop_pagination(self):
        platform = FakeDouyinPlatform({
            0: make_page(
                [
                    make_aweme(1, 500, is_top=1),
                    make_aweme(2, 2000),
                ],
                has_more=1,
                max_cursor=10,
            ),
            10: make_page([make_aweme(3, 1500)], has_more=0, max_cursor=10),
        })
        progress = []

        with patch('platforms.douyin.platform.time.sleep', return_value=None):
            videos = platform.get_profile_videos(
                'sec-user-id',
                {},
                lambda found, page, has_more: progress.append((found, page, has_more)),
                start_time=1000,
                end_time=3000,
            )

        self.assertEqual(['2', '3'], [video.video_id for video in videos])
        self.assertEqual([0, 10], platform.cursor_calls)
        self.assertEqual([(1, 1, True), (2, 2, False)], progress)

    def test_old_regular_aweme_stops_after_current_page(self):
        platform = FakeDouyinPlatform({
            0: make_page(
                [
                    make_aweme(1, 2000),
                    make_aweme(2, 500),
                ],
                has_more=1,
                max_cursor=10,
            ),
            10: make_page([make_aweme(3, 1500)], has_more=0, max_cursor=10),
        })
        progress = []

        with patch('platforms.douyin.platform.time.sleep', return_value=None):
            videos = platform.get_profile_videos(
                'sec-user-id',
                {},
                lambda found, page, has_more: progress.append((found, page, has_more)),
                start_time=1000,
                end_time=3000,
            )

        self.assertEqual(['1'], [video.video_id for video in videos])
        self.assertEqual([0], platform.cursor_calls)
        self.assertEqual([(1, 1, False)], progress)

    def test_in_range_top_aweme_is_included(self):
        platform = FakeDouyinPlatform({
            0: make_page([make_aweme(1, 2000, is_top=1)], has_more=0, max_cursor=0),
        })

        videos = platform.get_profile_videos(
            'sec-user-id',
            {},
            start_time=1000,
            end_time=3000,
        )

        self.assertEqual(['1'], [video.video_id for video in videos])


if __name__ == '__main__':
    unittest.main()
