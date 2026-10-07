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
    def test_all_pages_include_83_unique_videos_with_repeated_pinned_items(self):
        pinned = [make_aweme(i, 500, is_top=1) for i in range(1, 4)]
        platform = FakeDouyinPlatform({
            0: make_page(pinned + [make_aweme(i, 3000) for i in range(4, 24)], 1, 10),
            10: make_page(pinned + [make_aweme(i, 2500) for i in range(24, 44)], 1, 20),
            20: make_page([make_aweme(i, 2000) for i in range(44, 64)], 1, 30),
            30: make_page([make_aweme(i, 1500) for i in range(64, 84)], 0, 30),
        })
        progress = []

        with patch('platforms.douyin.platform.time.sleep', return_value=None):
            videos = platform.get_profile_videos(
                'sec-user-id', {},
                lambda found, page, more: progress.append((found, page, more)),
            )

        self.assertEqual([str(i) for i in range(1, 84)], [v.video_id for v in videos])
        self.assertEqual([0, 10, 20, 30], platform.cursor_calls)
        self.assertEqual([(23, 1, True), (43, 2, True), (63, 3, True), (83, 4, False)], progress)

    def test_status_only_response_retries_same_page_then_recovers(self):
        platform = DouyinPlatform()
        with patch.object(platform, '_signed_get_json', side_effect=[
            make_page([make_aweme(1, 2000)], 1, 10),
            {'status_code': 0},
            make_page([make_aweme(2, 1500)], 0, 10),
        ]) as request, patch('platforms.douyin.platform.time.sleep', return_value=None):
            videos = platform.get_profile_videos('sec-user-id', {})

        self.assertEqual(['1', '2'], [v.video_id for v in videos])
        self.assertEqual(['0', '10', '10'], [c.args[1]['max_cursor'] for c in request.call_args_list])

    def test_persistent_status_only_response_raises_instead_of_returning_first_page(self):
        platform = DouyinPlatform()
        with patch.object(platform, '_signed_get_json', side_effect=[
            make_page([make_aweme(1, 2000)], 1, 10),
            {'status_code': 0}, {'status_code': 0}, {'status_code': 0},
        ]) as request, patch('platforms.douyin.platform.time.sleep', return_value=None):
            with self.assertRaisesRegex(RuntimeError, '第 2 页接口返回不完整.*已找到 1 个视频'):
                platform.get_profile_videos('sec-user-id', {})
        self.assertEqual(4, request.call_count)

    def test_missing_or_invalid_cursor_does_not_report_completion(self):
        for cursor in (None, 'invalid', -1, 0):
            with self.subTest(cursor=cursor):
                platform = FakeDouyinPlatform({
                    0: make_page([make_aweme(1, 2000)], 1, cursor),
                })
                with self.assertRaisesRegex(RuntimeError, '分页游标.*解析未完成'):
                    platform.get_profile_videos('sec-user-id', {})

    def test_cyclic_cursor_does_not_report_completion(self):
        platform = FakeDouyinPlatform({
            0: make_page([make_aweme(1, 3000)], 1, 10),
            10: make_page([make_aweme(2, 2000)], 1, 20),
            20: make_page([make_aweme(3, 1000)], 1, 10),
        })
        with patch('platforms.douyin.platform.time.sleep', return_value=None):
            with self.assertRaisesRegex(RuntimeError, '重复分页游标'):
                platform.get_profile_videos('sec-user-id', {})
        self.assertEqual([0, 10, 20], platform.cursor_calls)

    def test_empty_intermediate_page_still_follows_cursor(self):
        platform = FakeDouyinPlatform({
            0: make_page([make_aweme(1, 2000)], 1, 10),
            10: make_page([], 1, 20),
            20: make_page([make_aweme(2, 1000)], 0, 20),
        })
        with patch('platforms.douyin.platform.time.sleep', return_value=None):
            videos = platform.get_profile_videos('sec-user-id', {})
        self.assertEqual(['1', '2'], [v.video_id for v in videos])
        self.assertEqual([0, 10, 20], platform.cursor_calls)

    def test_string_zero_has_more_and_null_list_are_valid_final_pages(self):
        for awemes in ([], None):
            with self.subTest(awemes=awemes):
                platform = FakeDouyinPlatform({0: make_page(awemes, '0', 10)})
                self.assertEqual([], platform.get_profile_videos('sec-user-id', {}))
                self.assertEqual([0], platform.cursor_calls)

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
