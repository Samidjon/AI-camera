"""Optional AI Engine (MVP 04).

A pluggable layer on top of the Standard Vision Engine. It is never required: if it is disabled,
missing a model, out of memory, timing out or crashing, capture, motion detection and local
event storage keep running unchanged.

Layout (the ``edge/ai-engine`` structure of the spec, placed inside the edge agent package as the
MVP 05 layout prescribes):

    providers/      AIProvider interface + OpenCV-DNN YOLOX implementation
    models/         Model registry, versions, validation (sha256), local files only
    detection/      Person detection results and filtering
    tracking/       ByteTrack multi-object tracker (temporary, camera-scoped IDs)
    attributes/     Visible appearance attributes (local colour analysis, optional Claude provider)
    monitoring/     Inference metrics, GPU probe, status history
    configuration/  AI settings
    engine.py       AI supervisor: modes, timeouts, recovery with backoff, fallback to Standard
"""
