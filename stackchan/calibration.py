"""Reviewed opt-in calibration for the measured K151/CoreS3 device."""

from __future__ import annotations

from stackchan.adapter import HeadMove, StackChanCalibration


def measured_k151_cores3_calibration() -> StackChanCalibration:
    """Build measured motions and mappings for verified native avatars."""

    return StackChanCalibration(
        faces={
            "neutral": "idle",
            "attentive": "thinking",
            "happy": "happy",
            "concerned": "sad",
        },
        motions={
            "relaxed_center": (HeadMove(yaw=0, pitch=43, speed=30),),
            "restrained_side_glance": (
                HeadMove(yaw=12, pitch=50, speed=30),
            ),
        },
        hold_seconds={"restrained_side_glance": 10.0},
        verified_faces=frozenset({"idle", "thinking", "happy", "sad"}),
    )
