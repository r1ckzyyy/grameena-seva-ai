"""Exotel Voicebot WebSocket transport for bidirectional PSTN audio."""

from __future__ import annotations

import base64
import io
import json
import logging
import math
import struct
import time
import wave
from typing import Any

from flask import Flask
from flask_sock import Sock

from models.conversation import ConversationState
from repositories.farmers import normalize_phone
from repositories.models import FarmerRecord
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
        self.audio_cache: dict[tuple[str, str], bytes] = {}
        self.last_audio_duration = 0.0

    def preload_audio(self, text: str, language: str, audio: bytes) -> None:
        """Cache audio generated before a call so the first response is immediate."""
        if audio:
            self.audio_cache[(text, self._language_code(language))] = audio

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
            return "Hello, Namaskaram. Which government subsidy or farming scheme would you like help with today?"
        code = (language or "").casefold()
        suffix = f" {name}" if name else ""
        if code.startswith("te"):
            return f"నమస్తే కాకా, ఏం సంగతులు{suffix}? మీ వివరాలు నాకు గుర్తున్నాయి."
        if code.startswith("hi"):
            return f"कैसे हो चाचा, क्या हाल-चाल{suffix}? आपकी जानकारी मुझे याद है।"
        return f"Hello, Namaste{suffix}. Which government subsidy or farming scheme would you like help with today?"

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

    def _send_audio(self, ws: Any, audio: bytes, stream_sid: str, sequence_number: int, chunk_number: int) -> tuple[int, int]:
        pcm = self._pcm_from_audio(audio)
        if not pcm:
            logger.warning("Exotel TTS returned empty audio")
            self.last_audio_duration = 0.0
            return sequence_number, chunk_number
        self.last_audio_duration = len(pcm) / (8000 * 2)
        logger.info("Sending Exotel audio bytes=%d stream_sid=%s", len(pcm), stream_sid)
        # Exotel expects bidirectional media packets to carry stream identity
        # and sequencing metadata. Keep packets at 100–200 ms and multiples of
        # 320 bytes to avoid jitter/under-sized-packet playback failures.
        for offset in range(0, len(pcm), 3200):
            chunk = pcm[offset:offset + 3200]
            if len(chunk) < 3200:
                # Exotel may wait for more data when the final packet is below
                # its 100 ms minimum. Pad only the last packet with silence.
                chunk += b"\x00" * (3200 - len(chunk))
            if chunk:
                ws.send(json.dumps({
                    "event": "media",
                    "stream_sid": stream_sid,
                    "media": {
                        "chunk": str(chunk_number),
                        "timestamp": str(round(offset / 16)),
                        "payload": base64.b64encode(chunk).decode("ascii"),
                    },
                }))
                # Exotel's bidirectional stream is a real-time media channel.
                # Do not burst the whole greeting into the socket at once;
                # pace each packet to its duration so the platform can play it.
                time.sleep(0.1)
                sequence_number += 1
                chunk_number += 1
        return sequence_number, chunk_number

    def _say(self, ws: Any, text: str, language: str, stream_sid: str, sequence_number: int, chunk_number: int) -> tuple[int, int]:
        if not self.text_to_speech_fn or not self.sarvam_key:
            logger.error("Cannot speak Exotel response: Sarvam TTS is not configured")
            return sequence_number, chunk_number
        language_code = self._language_code(language)
        cache_key = (text, language_code)
        audio = self.audio_cache.get(cache_key)
        if audio is None:
            try:
                audio = self.text_to_speech_fn(text, language_code, self.sarvam_key)
                if audio:
                    self.audio_cache[cache_key] = audio
            except Exception:
                logger.exception("Exotel TTS failed while speaking: %s", text[:80])
                return sequence_number, chunk_number
        return self._send_audio(ws, audio, stream_sid, sequence_number, chunk_number)

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
        stream_sid = ""
        outbound_sequence = 1
        outbound_chunk = 1
        ignore_input_until = 0.0

        while True:
            raw = ws.receive()
            if raw is None:
                return
            event = json.loads(raw) if isinstance(raw, str) else raw
            event_type = event.get("event")
            logger.info("Exotel stream event=%s", event_type)
            if event_type == "connected":
                # Exotel sends no caller number or stream_sid in this packet.
                # The greeting is sent on the immediately-following start
                # packet, once the bidirectional media stream is identified.
                logger.info("Exotel websocket connected; waiting for start event before greeting")
                continue
            if event_type == "start":
                start = event.get("start") or {}
                stream_sid = str(event.get("stream_sid") or start.get("stream_sid") or "")
                phone = normalize_phone(start.get("from") or start.get("caller") or "")
                try:
                    if not phone:
                        # Some Exotel flows expose a SIP/agent identifier instead
                        # of the caller's mobile number. Keep the live stream
                        # usable with a temporary session instead of hanging up.
                        logger.warning("Exotel stream had no usable caller number; using temporary session: %s", start)
                        farmer, conversation, returning = self.conversation_service.load_or_create_anonymous_farmer()
                    else:
                        farmer, conversation, returning = self.conversation_service.load_or_create_farmer(phone)
                except Exception:
                    # Voice must remain usable even when the shared database is
                    # unavailable. Persistence is best-effort for Exotel.
                    logger.exception("Exotel farmer lookup failed; using in-memory session")
                    temporary_id = f"exotel-{stream_sid or 'session'}"
                    farmer = FarmerRecord(id=temporary_id)
                    conversation = ConversationState(farmer_id=temporary_id)
                    returning = False
                farmer_id = farmer.id
                logger.info("Exotel stream started call_sid=%s stream_sid=%s farmer_id=%s", start.get("call_sid"), stream_sid, farmer_id)
                outbound_sequence, outbound_chunk = self._say(
                    ws,
                    self._greeting(conversation.language_code, returning, farmer.name),
                    conversation.language_code or "en-IN",
                    stream_sid,
                    outbound_sequence,
                    outbound_chunk,
                )
                ignore_input_until = time.monotonic() + self.last_audio_duration + 0.5
                continue
            if event_type == "stop":
                return
            if event_type != "media":
                continue
            media = event.get("media") or {}
            pcm = base64.b64decode(media.get("payload") or "")
            if not pcm:
                continue
            if time.monotonic() < ignore_input_until:
                # Exotel can loop the bot's outbound audio into the inbound
                # stream. Do not mistake the assistant's voice for the caller.
                speech.clear()
                speech_started = False
                silent_seconds = 0.0
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
                try:
                    transcript, detected = self.transcribe_fn(wav.getvalue(), self.sarvam_key)
                except Exception:
                    logger.exception("Exotel speech transcription failed")
                    outbound_sequence, outbound_chunk = self._say(
                        ws, "I could not hear that clearly. Please say it again.",
                        conversation.language_code or "en-IN", stream_sid,
                        outbound_sequence, outbound_chunk,
                    )
                    ignore_input_until = time.monotonic() + self.last_audio_duration + 0.5
                    continue
                if detected:
                    conversation.language_code = detected
                if not transcript:
                    continue
                thinking_text = "One moment, I am checking the official information. Please wait."
                outbound_sequence, outbound_chunk = self._say(
                    ws,
                    thinking_text,
                    "en-IN",
                    stream_sid,
                    outbound_sequence,
                    outbound_chunk,
                )
                ignore_input_until = time.monotonic() + self.last_audio_duration + 0.5
                try:
                    outcome = self.conversation_service.process_text(
                        transcript, conversation,
                        farmer_id=farmer_id,
                        gemini_key=self.gemini_key,
                        tavily_key=self.tavily_key,
                        firecrawl_key=self.firecrawl_key,
                        summarize=False,
                    )
                except Exception:
                    logger.exception("Exotel conversation processing failed")
                    outcome = None
                outbound_sequence, outbound_chunk = self._say(
                    ws,
                    (outcome.response_text or outcome.error_message) if outcome else "I am having trouble checking that right now. Please say it again.",
                    conversation.language_code or "en-IN",
                    stream_sid,
                    outbound_sequence,
                    outbound_chunk,
                )
                ignore_input_until = time.monotonic() + self.last_audio_duration + 0.5
                if conversation.result.conversation_complete:
                    return


def create_exotel_app(transport: ExotelTransport) -> Flask:
    app = Flask(__name__)
    sock = Sock(app)

    @app.get("/health")
    def health() -> tuple[str, int]:
        return "ok", 200

    @app.get("/")
    def root() -> tuple[str, int]:
        return "ok", 200

    @sock.route("/media")
    @sock.route("/exotel/media")
    def media(ws: Any) -> None:
        try:
            transport.handle(ws)
        except Exception:
            logger.exception("Exotel Voicebot stream failed")

    return app
