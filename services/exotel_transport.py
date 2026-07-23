"""Exotel Voicebot WebSocket transport for bidirectional PSTN audio."""

from __future__ import annotations

import base64
import io
import json
import logging
import math
import struct
import wave
from typing import Any

from flask import Flask
from flask_sock import Sock

from models.conversation import ConversationState
from repositories.farmers import normalize_phone
from services.conversation import ConversationService


logger = logging.getLogger("grameen_seva.exotel")


class ExotelTransport:
    """Bridge Exotel's 8 kHz PCM stream to the shared conversation service."""

    def __init__(
        self,
        conversation_service: ConversationService,
        *,
        gemini_key: str,
        tavily_key: str,
        firecrawl_key: str,
        sarvam_key: str,
        transcribe_fn: Any,
        text_to_speech_fn: Any,
    ) -> None:
        self.conversation_service = conversation_service
        self.gemini_key = gemini_key
        self.tavily_key = tavily_key
        self.firecrawl_key = firecrawl_key
        self.sarvam_key = sarvam_key
        self.transcribe_fn = transcribe_fn
        self.text_to_speech_fn = text_to_speech_fn

    @staticmethod
    def _language_code(language: str) -> str:
        return {
            "te": "te-IN", "hi": "hi-IN", "ta": "ta-IN", "kn": "kn-IN",
            "mr": "mr-IN", "bn": "bn-IN", "gu": "gu-IN", "pa": "pa-IN",
        }.get((language or "").casefold(), language or "en-IN")

    @staticmethod
    def _thinking_message(language: str) -> str:
        code = (language or "").casefold()
        if code.startswith("te"):
            return "ఒక్క నిమిషం, ఆలోచిస్తున్నాను. దయచేసి వేచి ఉండండి."
        if code.startswith("hi"):
            return "एक मिनट, मैं जानकारी देख रहा हूँ। कृपया इंतज़ार कीजिए।"
        if code.startswith("ta"):
            return "ஒரு நிமிடம், தகவலைப் பார்க்கிறேன். தயவுசெய்து காத்திருக்கவும்."
        return "One moment, I am checking the official information. Please wait."

    @staticmethod
    def _greeting(language: str, returning: bool, name: str = "") -> str:
        if not returning:
            return "Namaskaram kaka. What farming scheme or subsidy do you need help with today?"
        code = (language or "").casefold()
        suffix = f" {name}" if name else ""
        if code.startswith("te"):
            return f"నమస్తే కాకా, ఏం సంగతులు{suffix}? మీ వివరాలు నాకు గుర్తున్నాయి."
        if code.startswith("hi"):
            return f"कैसे हो चाचा, क्या हाल-चाल{suffix}? आपकी जानकारी मुझे याद है।"
        return f"Namaste kaka, how are you{suffix}? I remember your farmer details."

    @staticmethod
    def _pcm_from_audio(audio: bytes) -> bytes:
        """Convert Sarvam WAV output to mono signed 16-bit PCM at 8 kHz."""
        try:
            with wave.open(io.BytesIO(audio), "rb") as source:
                channels = source.getnchannels()
                width = source.getsampwidth()
                rate = source.getframerate()
                frames = source.readframes(source.getnframes())
            if width != 2:
                return frames
            samples = struct.unpack(f"<{len(frames) // 2}h", frames)
            if channels > 1:
                samples = tuple(sum(samples[i:i + channels]) // channels for i in range(0, len(samples), channels))
            if rate != 8000 and samples:
                target_count = max(1, round(len(samples) * 8000 / rate))
                samples = tuple(samples[min(len(samples) - 1, round(i * rate / 8000))] for i in range(target_count))
            return struct.pack(f"<{len(samples)}h", *samples)
        except (wave.Error, EOFError, struct.error, ValueError):
            return audio

    def _send_audio(self, ws: Any, audio: bytes) -> None:
        pcm = self._pcm_from_audio(audio)
        for offset in range(0, len(pcm), 320):
            chunk = pcm[offset:offset + 320]
            if chunk:
                ws.send(json.dumps({"event": "media", "media": {"payload": base64.b64encode(chunk).decode("ascii")}}))

    def _say(self, ws: Any, text: str, language: str) -> None:
        audio = self.text_to_speech_fn(text, self._language_code(language), self.sarvam_key)
        self._send_audio(ws, audio)

    @staticmethod
    def _rms(pcm: bytes) -> float:
        if len(pcm) < 2:
            return 0.0
        values = struct.unpack(f"<{len(pcm) // 2}h", pcm[:len(pcm) - len(pcm) % 2])
        return math.sqrt(sum(value * value for value in values) / len(values))

    def handle(self, ws: Any) -> None:
        farmer_id = ""
        conversation: ConversationState | None = None
        speech = bytearray()
        speech_started = False
        silent_seconds = 0.0

        while True:
            raw = ws.receive()
            if raw is None:
                return
            event = json.loads(raw) if isinstance(raw, str) else raw
            event_type = event.get("event")
            if event_type == "start":
                start = event.get("start") or {}
                phone = normalize_phone(start.get("from") or start.get("caller") or "")
                if not phone:
                    return
                farmer, conversation, returning = self.conversation_service.load_or_create_farmer(phone)
                farmer_id = farmer.id
                self._say(ws, self._greeting(conversation.language_code, returning, farmer.name), conversation.language_code or "en-IN")
                continue
            if event_type == "stop":
                return
            if event_type != "media":
                continue
            media = event.get("media") or {}
            pcm = base64.b64decode(media.get("payload") or "")
            if not pcm:
                continue
            duration = len(pcm) / (8000 * 2)
            loud = self._rms(pcm) > 450
            if loud:
                speech_started = True
                silent_seconds = 0.0
            elif speech_started:
                silent_seconds += duration
            speech.extend(pcm)
            if speech_started and silent_seconds >= 1.0:
                utterance = bytes(speech)
                speech.clear()
                speech_started = False
                silent_seconds = 0.0
                if conversation is None:
                    continue
                wav = io.BytesIO()
                with wave.open(wav, "wb") as output:
                    output.setnchannels(1)
                    output.setsampwidth(2)
                    output.setframerate(8000)
                    output.writeframes(utterance)
                transcript, detected = self.transcribe_fn(wav.getvalue(), self.sarvam_key)
                if detected:
                    conversation.language_code = detected
                if not transcript:
                    continue
                self._say(ws, self._thinking_message(conversation.language_code), conversation.language_code or "en-IN")
                outcome = self.conversation_service.process_text(
                    transcript, conversation,
                    farmer_id=farmer_id,
                    gemini_key=self.gemini_key,
                    tavily_key=self.tavily_key,
                    firecrawl_key=self.firecrawl_key,
                    summarize=False,
                )
                self._say(ws, outcome.response_text or outcome.error_message, conversation.language_code or "en-IN")
                if conversation.result.conversation_complete:
                    return


def create_exotel_app(transport: ExotelTransport) -> Flask:
    app = Flask(__name__)
    sock = Sock(app)

    @sock.route("/exotel/media")
    def media(ws: Any) -> None:
        try:
            transport.handle(ws)
        except Exception:
            logger.exception("Exotel Voicebot stream failed")

    return app
