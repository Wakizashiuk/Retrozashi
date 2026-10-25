from retrozashi.desktop.screen import (API_MAX_LONG_EDGE, API_MAX_VISUAL_TOKENS, Frame, Rect,
                                       compute_scale, visual_tokens)


def test_4k_scales_to_1080p_class():
    s = compute_scale(3840, 2160)
    assert int(3840 * s) == 1920
    assert visual_tokens(int(3840 * s), int(2160 * s)) <= API_MAX_VISUAL_TOKENS


def test_small_screen_not_upscaled():
    assert compute_scale(1280, 720) == 1.0


def test_full_api_limits_respected_for_ultrawide_and_tall():
    for w, h in [(5120, 1440), (7680, 2160), (2160, 3840), (3440, 1440)]:
        s = compute_scale(w, h, max_long_edge=API_MAX_LONG_EDGE)
        ow, oh = int(w * s), int(h * s)
        assert max(ow, oh) <= API_MAX_LONG_EDGE
        assert visual_tokens(ow, oh) <= API_MAX_VISUAL_TOKENS


def test_frame_maps_to_secondary_monitor_left_of_primary():
    # 4K monitor positioned left of the primary: virtual-desktop x is negative.
    f = Frame(b"", 1920, 1080, Rect(-3840, 0, 3840, 2160), 0.5)
    assert f.to_screen(0, 0) == (-3840, 0)
    assert f.to_screen(960, 540) == (-1920, 1080)
    assert f.to_screen(5000, 5000) == (-1, 2159)  # clamped inside the monitor
    assert f.to_image(-1920, 1080) == (960, 540)
