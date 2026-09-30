"""Exact source/timeline mapping for source-anchored video speed curves.

Speed is linear between source-time anchors. Integrating 1 / speed gives
the presentation time, so trims and splits keep the same original ramp.
"""
import math

MAX_SPEED_POINTS = 50
MAX_SPEED_CURVE_POINTS = MAX_SPEED_POINTS
MIN_SPEED = .25
MAX_SPEED = 4.
TIME_EPSILON = 1e-9


def speed_curve(clip):
    return clip.get("speed_curve", [])


def canonical_speed_curve(clip):
    return [{"time": point["time"], "speed": point["speed"]}
            for point in speed_curve(clip)]


def _finite_number(value):
    try:
        return (not isinstance(value, bool) and isinstance(value, (int, float))
                and math.isfinite(value))
    except OverflowError:
        return False


def validate_speed_curve(clip, source_duration=None):
    # Imported only for validation: video_editor imports the mapping helpers.
    from video_editor import EditorError
    points = clip.get("speed_curve", [])
    if not isinstance(points, list) or len(points) == 1 or len(points) > MAX_SPEED_POINTS:
        raise EditorError("變速曲線需有 2–50 個控制點；清除曲線後可使用固定速度。")
    previous, clean = -math.inf, []
    for point in points:
        if not isinstance(point, dict) or set(point) != {"time", "speed"}:
            raise EditorError("變速曲線控制點格式錯誤。")
        time, speed = point["time"], point["speed"]
        if (not _finite_number(time) or not _finite_number(speed)
                or time < 0 or time <= previous + TIME_EPSILON
                or speed < MIN_SPEED or speed > MAX_SPEED
                or (source_duration is not None and time > source_duration + 1e-6)):
            raise EditorError("曲線時間需依序排列且不可重複，速度為 0.25–4 倍，控制點須位於來源素材內。")
        clean.append({"time": float(time), "speed": float(speed)})
        previous = time
    return clean


def speed_at_source(clip, source_time):
    points = speed_curve(clip)
    if not points:
        return clip.get("speed", 1)
    left = points[0]
    if source_time <= left["time"]:
        return left["speed"]
    for right in points[1:]:
        if source_time < right["time"]:
            ratio = (source_time - left["time"]) / (right["time"] - left["time"])
            return left["speed"] + (right["speed"] - left["speed"]) * ratio
        left = right
    return left["speed"]


def _segment_time(length, start_speed, end_speed):
    if length == 0:
        return 0.
    delta = end_speed - start_speed
    if abs(delta) <= abs(start_speed) * 1e-12:
        return length / start_speed
    return length * math.log1p(delta / start_speed) / delta


def _segment_source(elapsed, length, start_speed, end_speed):
    delta = end_speed - start_speed
    if abs(delta) <= abs(start_speed) * 1e-12:
        return elapsed * start_speed
    slope = delta / length
    return start_speed * math.expm1(slope * elapsed) / slope


def curve_duration(clip, source_start=None, source_end=None):
    """Signed presentation seconds between two absolute source seconds."""
    start = clip["in"] if source_start is None else source_start
    end = clip["out"] if source_end is None else source_end
    if end < start:
        return -curve_duration(clip, end, start)
    points = speed_curve(clip)
    if not points:
        return (end - start) / clip.get("speed", 1)
    boundaries = [start] + [p["time"] for p in points if start < p["time"] < end] + [end]
    return math.fsum(_segment_time(right - left, speed_at_source(clip, left),
                                  speed_at_source(clip, right))
                     for left, right in zip(boundaries, boundaries[1:]))


def clip_duration(clip):
    return curve_duration(clip)


def timeline_at(clip, source_time):
    return curve_duration(clip, clip["in"], source_time)


def source_at_extended(clip, local_time, source_duration=None):
    """Invert presentation time, permitting source before/after the trim.

    Source never goes before zero. Supplying the full media duration also
    clamps its end; use this helper when dragging a trim edge.
    """
    points = speed_curve(clip)
    if not points:
        source = clip["in"] + local_time * clip.get("speed", 1)
    else:
        elapsed = curve_duration(clip, 0, clip["in"]) + local_time
        source = 0.
        if elapsed > 0:
            left = 0.
            for point in points:
                right = point["time"]
                if right <= left:
                    continue
                start_speed, end_speed = speed_at_source(clip, left), point["speed"]
                length = right - left
                segment = _segment_time(length, start_speed, end_speed)
                if elapsed <= segment:
                    source = left + _segment_source(elapsed, length, start_speed, end_speed)
                    break
                elapsed -= segment
                left = right
            else:
                source = left + elapsed * points[-1]["speed"]
    source = max(0., source)
    return min(source_duration, source) if source_duration is not None else source


def source_at(clip, local_time):
    """Absolute source time for a presentation offset within this trim."""
    if local_time <= 0:
        return clip["in"]
    if local_time >= clip_duration(clip):
        return clip["out"]
    return min(clip["out"], max(clip["in"], source_at_extended(clip, local_time)))
