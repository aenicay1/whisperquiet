"""Facial-gesture detection. Week 3-4 (DESIGN.md decision #8).

Planned shape:
- MediaPipe Face Landmarker blendshapes (eyeBlinkLeft/Right, jawOpen,
  mouthPucker, mouthSmile…) per frame.
- Wink = one eye's blink score over threshold while the other stays low —
  that asymmetry is what separates a deliberate wink from a natural blink.
- Hysteresis (separate on/off thresholds) + minimum hold time per gesture to
  debounce. All thresholds user-tunable via config (faces differ a lot).
- Mappings: left wink = left click, right wink = right click,
  open-mouth-hold = drag, pucker/smile = scroll mode.
"""
