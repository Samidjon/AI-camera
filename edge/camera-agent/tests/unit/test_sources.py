import pytest

from aci_agent.camera.models import CameraConfig, CameraSecret, SourceType
from aci_agent.camera.sources import inject_credentials, resolve_target, split_credentials, validate_source


@pytest.mark.parametrize(
    ("source_type", "value", "expected"),
    [
        (SourceType.RTSP, "rtsp://10.0.0.5:554/Streaming/101", "rtsp://10.0.0.5:554/Streaming/101"),
        (SourceType.RTSP, " rtsps://cam.local/live ", "rtsps://cam.local/live"),
        (SourceType.FFMPEG, "http://10.0.0.9/video.mjpg", "http://10.0.0.9/video.mjpg"),
        (SourceType.ONVIF, "192.168.1.64:8080", "192.168.1.64:8080"),
        (SourceType.ONVIF, "cam-01.lan", "cam-01.lan"),
        (SourceType.USB, "01", "1"),
        (SourceType.FILE, "C:/videos/entrance.mp4", "C:/videos/entrance.mp4"),
    ],
)
def test_valid_sources(source_type, value, expected):
    assert validate_source(source_type, value).replace("\\", "/") == expected


@pytest.mark.parametrize(
    ("source_type", "value"),
    [
        (SourceType.RTSP, "http://10.0.0.5/stream"),  # wrong scheme
        (SourceType.RTSP, "rtsp:///nohost"),
        (SourceType.RTSP, "not a url"),
        (SourceType.FFMPEG, "file:///etc/passwd"),  # local file access via "stream" is refused
        (SourceType.FFMPEG, "javascript:alert(1)"),
        (SourceType.ONVIF, "http://cam/onvif"),
        (SourceType.ONVIF, "host:99999"),
        (SourceType.USB, "-1"),
        (SourceType.USB, "cam"),
        (SourceType.USB, "64"),
        (SourceType.FILE, "C:/Windows/system32/config.sys"),
        (SourceType.RTSP, ""),
        (SourceType.RTSP, "rtsp://host/\x00"),
    ],
)
def test_invalid_sources_rejected(source_type, value):
    with pytest.raises(ValueError):
        validate_source(source_type, value)


def test_credentials_are_split_out_of_urls():
    clean, user, pw = split_credentials("rtsp://admin:p%40ss%2Fw0rd@10.0.0.5:554/live?ch=1")
    assert clean == "rtsp://10.0.0.5:554/live?ch=1"
    assert (user, pw) == ("admin", "p@ss/w0rd")
    assert split_credentials("rtsp://10.0.0.5/live") == ("rtsp://10.0.0.5/live", "", "")


def test_credentials_injected_with_encoding():
    url = inject_credentials("rtsp://10.0.0.5:554/live", CameraSecret("ad min", "p@ss:/?#"))
    assert url == "rtsp://ad%20min:p%40ss%3A%2F%3F%23@10.0.0.5:554/live"
    assert inject_credentials("rtsp://h/x", None) == "rtsp://h/x"


def test_resolve_target_keeps_secrets_out_of_display():
    cfg = CameraConfig(id="c", name="n", source_type=SourceType.RTSP, source_uri="rtsp://10.0.0.5/live")
    target = resolve_target(cfg, CameraSecret("admin", "s3cret"))
    assert "s3cret" in target.uri
    assert "s3cret" not in target.display
    assert target.probe_url == "rtsp://10.0.0.5/live"


def test_secret_repr_never_leaks():
    assert "hunter2" not in repr(CameraSecret("admin", "hunter2"))
