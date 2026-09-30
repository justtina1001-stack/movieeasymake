"""Position animation shared by validated projects and the CPU exporter."""
from video_editor import EditorError, number

MAX_POSITION_KEYFRAMES = 100
EASINGS = ("linear", "ease_in", "ease_out", "ease_in_out")


def validate_position_keyframes(layer):
    points = layer.get("position_keyframes", [])
    if not isinstance(points, list) or len(points) > MAX_POSITION_KEYFRAMES:
        raise EditorError("每個文字／圖片圖層最多 100 個位置關鍵幀。")
    if points and layer.get("kind") not in ("text", "image"):
        raise EditorError("位置關鍵幀目前支援文字與圖片。")
    clean, previous = [], -float("inf")
    for point in points:
        if not isinstance(point, dict) or set(point) - {"time", "x", "y", "easing"}:
            raise EditorError("位置關鍵幀格式錯誤。")
        # Keep trimmed-away points so extending an edge restores the original
        # motion, including the exact easing curve. Time is relative to start.
        time = number(point.get("time"), "關鍵幀時間", -600, 600)
        if time <= previous + 1e-9:
            raise EditorError("位置關鍵幀時間必須遞增且不可重複。")
        easing = point.get("easing", "linear")
        if easing not in EASINGS:
            raise EditorError("位置關鍵幀的移動曲線無效。")
        clean.append({"time": time, "x": number(point.get("x"), "關鍵幀水平位置", 0, 1),
                      "y": number(point.get("y"), "關鍵幀垂直位置", 0, 1), "easing": easing})
        previous = time
    return clean


def position_at(layer, local_time):
    points = layer.get("position_keyframes", [])
    if not points:
        return {"x": layer["x"], "y": layer["y"]}
    left = points[0]
    if local_time <= left["time"]:
        return {"x": left["x"], "y": left["y"]}
    for right in points[1:]:
        if local_time < right["time"]:
            ratio = (local_time - left["time"]) / (right["time"] - left["time"])
            easing = left.get("easing", "linear")
            if easing == "ease_in":
                ratio *= ratio
            elif easing == "ease_out":
                ratio = 1 - (1 - ratio) ** 2
            elif easing == "ease_in_out":
                ratio = 2 * ratio ** 2 if ratio < .5 else 1 - 2 * (1 - ratio) ** 2
            return {axis: left[axis] + (right[axis] - left[axis]) * ratio for axis in ("x", "y")}
        left = right
    return {"x": left["x"], "y": left["y"]}
