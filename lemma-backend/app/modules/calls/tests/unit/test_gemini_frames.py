"""Gemini Live's server frames, as the call's events.

Frame shapes are the ones a live 3.8 session sends: input transcription arrives
in pieces and keeps arriving after the voice starts answering, and the voice
answering is the only end-of-turn the caller's side ever gets.
"""

from __future__ import annotations

import base64

import pytest

from app.modules.calls.domain.voice import (
    AudioOut,
    Heard,
    Interrupted,
    Said,
    TurnDone,
    UserSpeaking,
    VoiceEvent,
)
from app.modules.calls.infrastructure.engines.gemini_live import GeminiLiveEngine

pytestmark = pytest.mark.unit


def _engine() -> GeminiLiveEngine:
    return GeminiLiveEngine(
        api_key="test", model="gemini-3.8-live", instructions="be brief"
    )


def _drain(engine: GeminiLiveEngine) -> list[VoiceEvent]:
    events: list[VoiceEvent] = []
    while not engine._events.empty():
        events.append(engine._events.get_nowait())
    return events


def _audio(pcm: bytes) -> dict[str, object]:
    return {
        "modelTurn": {
            "parts": [
                {
                    "inlineData": {
                        "mimeType": "audio/pcm;rate=24000",
                        "data": base64.b64encode(pcm).decode(),
                    }
                }
            ]
        }
    }


def test_a_turn_heard_answered_and_finished() -> None:
    engine = _engine()
    engine._handle_frame(
        {"serverContent": {"inputTranscription": {"text": "Find me "}}}
    )
    engine._handle_frame(
        {"serverContent": {"inputTranscription": {"text": "a flight."}}}
    )
    engine._handle_frame({"serverContent": _audio(b"\x01\x00" * 4)})
    engine._handle_frame({"serverContent": {"outputTranscription": {"text": "Sure."}}})
    engine._handle_frame({"serverContent": {"turnComplete": True}})
    assert _drain(engine) == [
        UserSpeaking(),
        Heard("Find me ", final=False),
        Heard("a flight.", final=False),
        Heard("Find me a flight.", final=True),
        AudioOut(b"\x01\x00" * 4, 24_000),
        Said("Sure.", final=False),
        Said("Sure.", final=True),
        TurnDone(),
    ]


def test_a_barge_in_ends_the_voices_turn_where_it_was() -> None:
    engine = _engine()
    engine._handle_frame(
        {"serverContent": {"outputTranscription": {"text": "The first venue is"}}}
    )
    engine._handle_frame({"serverContent": {"interrupted": True}})
    events = _drain(engine)
    assert events[-3:] == [
        Interrupted(),
        Said("The first venue is", final=True),
        TurnDone(),
    ]


def test_resumption_handles_are_kept_and_go_away_rotates_the_connection() -> None:
    engine = _engine()
    engine._handle_frame(
        {"sessionResumptionUpdate": {"newHandle": "h-1", "resumable": True}}
    )
    engine._handle_frame(
        {"sessionResumptionUpdate": {"newHandle": "h-2", "resumable": False}}
    )
    assert engine._handle == "h-1"
    setup = engine._setup()["setup"]
    assert isinstance(setup, dict) and setup["sessionResumption"] == {"handle": "h-1"}
    assert setup["contextWindowCompression"] == {"slidingWindow": {}}


def test_text_that_is_not_pcm_audio_is_not_played() -> None:
    engine = _engine()
    engine._handle_frame(
        {
            "serverContent": {
                "modelTurn": {
                    "parts": [
                        {"text": "thinking"},
                        {"inlineData": {"mimeType": "image/png", "data": ""}},
                    ]
                }
            }
        }
    )
    assert not any(isinstance(event, AudioOut) for event in _drain(engine))
