import pytest

from aci_agent.standard_engine.geometry import Box, compass, point_in_polygon, segments_intersect, validate_polygon
from aci_agent.standard_engine.motion import MotionRegion
from aci_agent.standard_engine.rules import CrossingLine, LineCrossingCounter, Zone, ZoneMonitor
from aci_agent.tracking.motion_tracker import MotionTracker

W, H = 640, 360


def region(x, y, w=40, h=60):
    return MotionRegion(Box(x, y, w, h), w * h)


def test_geometry_basics():
    square = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    assert point_in_polygon((0.5, 0.5), square) and not point_in_polygon((1.5, 0.5), square)
    assert point_in_polygon((1.0, 0.5), square)  # boundary counts as inside
    assert segments_intersect((0, 0), (1, 1), (0, 1), (1, 0))
    assert not segments_intersect((0, 0), (1, 0), (0, 1), (1, 1))
    assert compass(1, 0) == "E" and compass(0, -1) == "N" and compass(-1, 1) == "SW" and compass(0, 0) is None
    with pytest.raises(ValueError):
        validate_polygon([(0, 0), (0.5, 0.5)])
    with pytest.raises(ValueError):
        validate_polygon([(0, 0), (2, 0), (1, 1)])


def test_track_id_is_stable_while_object_moves():
    tr = MotionTracker("cam123456", max_distance=0.15, max_missed=3)
    ids = set()
    for i in range(20):
        upd = tr.update([region(50 + i * 10, 100)], W, H, now=i * 0.1)
        ids |= {t.track_id for t in upd.active}
    assert len(ids) == 1
    track = tr.tracks[0]
    assert track.track_id.startswith("m-cam123-")  # temporary, camera-scoped
    assert track.direction(W, H) == "E"


def test_two_objects_get_two_ids_and_ids_are_not_reused():
    tr = MotionTracker("cam", max_missed=2)
    for i in range(6):
        upd = tr.update([region(50 + i * 8, 50), region(500 - i * 8, 250)], W, H, now=i * 0.1)
    assert len({t.track_id for t in upd.active}) == 2
    first_ids = {t.track_id for t in upd.active}
    for i in range(4):  # both disappear
        upd = tr.update([], W, H, now=1 + i * 0.1)
    assert {t.track_id for t in upd.ended} <= first_ids
    for i in range(3):
        upd = tr.update([region(300, 150)], W, H, now=2 + i * 0.1)
    assert not ({t.track_id for t in upd.active} & first_ids)  # a re-appearing object is a NEW track


def test_single_frame_flicker_is_not_confirmed():
    tr = MotionTracker("cam", min_hits=2)
    upd = tr.update([region(100, 100)], W, H, now=0)
    assert upd.active == [] and upd.started == []


def test_missed_frames_keep_identity():
    tr = MotionTracker("cam", max_missed=3)
    for i in range(3):
        tr.update([region(100 + i * 10, 100)], W, H, now=i)
    tid = tr.tracks[0].track_id
    tr.update([], W, H, now=4)  # occluded for one frame
    upd = tr.update([region(140, 100)], W, H, now=5)
    assert [t.track_id for t in upd.active] == [tid]


def _walk(tr, positions, zones=None, lines=None, zm=None, lc=None):
    events = []
    for i, (x, y) in enumerate(positions):
        upd = tr.update([region(x, y)], W, H, now=i * 0.2)
        if zm is not None:
            events += zm.update(zones, upd.active, upd.ended, W, H)
        if lc is not None:
            events += lc.update(lines, upd.active, upd.ended, W, H)
    return events


def test_line_crossing_counts_and_direction():
    line = CrossingLine("L1", "Door", (0.5, 0.0), (0.5, 1.0))  # vertical line drawn top→bottom
    lc = LineCrossingCounter(cooldown_s=0)
    tr = MotionTracker("cam")
    left_to_right = [(100 + i * 25, 150) for i in range(20)]
    events = _walk(tr, left_to_right, lines=[line], lc=lc)
    crossed = [e for e in events if e.event_type == "line_crossed"]
    assert len(crossed) == 1
    # A→B points down; normal (dy, -dx) = (+, 0) → moving right is "forward"
    assert crossed[0].metadata["direction"] == "forward"

    tr2 = MotionTracker("cam")
    lc2 = LineCrossingCounter(cooldown_s=0)
    right_to_left = [(560 - i * 25, 150) for i in range(20)]
    crossed2 = [e for e in _walk(tr2, right_to_left, lines=[line], lc=lc2) if e.event_type == "line_crossed"]
    assert len(crossed2) == 1 and crossed2[0].metadata["direction"] == "backward"
    assert lc.counts["L1"] == {"forward": 1, "backward": 0}


def test_jitter_on_the_line_does_not_double_count():
    line = CrossingLine("L1", "Door", (0.5, 0.0), (0.5, 1.0))
    lc = LineCrossingCounter(cooldown_s=60)
    tr = MotionTracker("cam")
    jitter = [(280 + (i % 2) * 40, 150) for i in range(12)]  # hovering across the line
    crossed = [e for e in _walk(tr, jitter, lines=[line], lc=lc) if e.event_type == "line_crossed"]
    assert len(crossed) == 1


def test_zone_enter_exit_and_occupancy_estimate():
    zone = Zone("Z1", "Gate", "monitor", [(0.4, 0.0), (0.7, 0.0), (0.7, 1.0), (0.4, 1.0)])
    zm = ZoneMonitor()
    tr = MotionTracker("cam")
    path = [(60 + i * 30, 150) for i in range(20)]  # passes through the zone
    seen_inside = 0
    events = []
    for i, (x, y) in enumerate(path):
        upd = tr.update([region(x, y)], W, H, now=i * 0.2)
        events += zm.update([zone], upd.active, upd.ended, W, H)
        seen_inside = max(seen_inside, zm.occupancy().get("Z1", 0))
    types = [e.event_type for e in events]
    assert types.count("object_entered_zone") == 1 and types.count("object_exited_zone") == 1
    assert seen_inside == 1
    assert zm.occupancy()["Z1"] == 0


def test_object_lost_inside_zone_still_exits():
    zone = Zone("Z1", "Room", "monitor", [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)])
    zm = ZoneMonitor()
    tr = MotionTracker("cam", max_missed=2)
    events = []
    for i in range(4):
        upd = tr.update([region(300 + i * 5, 150)], W, H, now=i)
        events += zm.update([zone], upd.active, upd.ended, W, H)
    for i in range(5):  # stops moving → track ends inside the zone
        upd = tr.update([], W, H, now=10 + i)
        events += zm.update([zone], upd.active, upd.ended, W, H)
    exits = [e for e in events if e.event_type == "object_exited_zone"]
    assert len(exits) == 1 and exits[0].metadata["track_lost"] is True
    assert zm.occupancy()["Z1"] == 0


def test_exclude_zones_produce_no_events():
    zone = Zone("Z1", "Tree", "exclude", [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)])
    zm = ZoneMonitor()
    tr = MotionTracker("cam")
    events = []
    for i in range(6):
        upd = tr.update([region(100 + i * 10, 100)], W, H, now=i)
        events += zm.update([zone], upd.active, upd.ended, W, H)
    assert events == []
