"""Head-pose → cursor mapping. Week 3-4 (DESIGN.md decision #9).

Planned shape:
- MediaPipe Face Landmarker feeds head-pose deltas per frame.
- Relative displacement: cursor += delta * gain, with a deadzone to kill
  jitter at rest and a OneEuroFilter per axis.
- Precision mode (squint-hold or auto-slowdown near small targets) drops gain
  for pixel work.
- Cursor moves posted via Quartz CGEventCreateMouseEvent.
"""
