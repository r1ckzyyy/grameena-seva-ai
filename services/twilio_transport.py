"""Twilio voice adapter backed by the shared ConversationService."""

from __future__ import annotations

import time
import hashlib
import logging
from typing import TYPE_CHECKING, Callable
from uuid import uuid4

from flask import Flask, Response, abort, request
from twilio.request_validator import RequestValidator
from twilio.twiml.voice_response import Gather, VoiceResponse

from config.settings import secret
from models.conversation import ConversationState
if TYPE_CHECKING:
    from services.conversation import ConversationService


logger = logging.getLogger("grameen_seva.twilio")


class TwilioTransport:
    """Translate Twilio webhooks into the existing text conversation pipeline."""

    def __init__(
        self,
        conversation_service: ConversationService,
        *,
        gemini_key: str,
        tavily_key: str,
        firecrawl_key: str,
        sarvam_key: str = "",
        text_to_speech_fn: Callable[[str, str, str], bytes] | None = None,
        public_base_url: str = "",
    ) -> None:
        self.conversation_service = conversation_service
        self.gemini_key = gemini_key
        self.tavily_key = tavily_key
        self.firecrawl_key = firecrawl_key
        self.sarvam_key = sarvam_key
        self.text_to_speech_fn = text_to_speech_fn
        self.public_base_url = public_base_url.rstrip("/")
        self.sessions: dict[str, tuple[str, ConversationState]] = {}
        self.audio: dict[str, tuple[bytes, float]] = {}
        self.processed_requests: dict[str, tuple[str, float]] = {}

    def _base_url(self) -> str:
        return self.public_base_url or request.url_root.rstrip("/")

    @staticmethod
    def _language_code(language: str) -> str:
        return {
            "te": "te-IN", "hi": "hi-IN", "ta": "ta-IN", "kn": "kn-IN",
            "mr": "mr-IN", "bn": "bn-IN", "gu": "gu-IN", "pa": "pa-IN",
        }.get((language or "").casefold(), language or "en-IN")

    @staticmethod
    def _quirky_greeting(language: str, name: str = "") -> str:
        code = (language or "").casefold()
        if code.startswith("te"):
            return f"నమస్తే కాకా, ఏం సంగతులు{(' ' + name) if name else ''}? మీకు ఏ వ్యవసాయ పథకం గురించి తెలుసుకోవాలి?"
        if code.startswith("hi"):
            return f"कैसे हो चाचा, क्या हाल-चाल{(' ' + name) if name else ''}? आपको किस खेती योजना के बारे में जानना है?"
        if code.startswith("ta"):
            return f"வணக்கம் மாமா, எப்படி இருக்கீங்க{(' ' + name) if name else ''}? எந்த விவசாயத் திட்டம் பற்றி தெரிந்து கொள்ள வேண்டும்?"
        if code.startswith("kn"):
            return f"ನಮಸ್ಕಾರ ಕಾಕಾ, ಹೇಗಿದ್ದೀರಾ{(' ' + name) if name else ''}? ಯಾವ ಕೃಷಿ ಯೋಜನೆ ಬಗ್ಗೆ ತಿಳಿದುಕೊಳ್ಳಬೇಕು?"
        return f"Namaste kaka, how are you{(' ' + name) if name else ''}? Which farming scheme can I help you with today?"

    def _speak(self, response: VoiceResponse | Gather, text: str, language: str, base_url: str) -> None:
        """Use one voice only: Sarvam when available, Twilio Say otherwise."""
        if self.text_to_speech_fn and self.sarvam_key:
            try:
                audio = self.text_to_speech_fn(text, self._language_code(language), self.sarvam_key)
                token = str(uuid4())
                self.audio[token] = (audio, time.monotonic() + 300)
                if len(self.audio) > 100:
                    self.audio.pop(next(iter(self.audio)))
                response.play(f"{base_url}/twilio/audio/{token}")
                return
            except Exception:
                pass
        response.say(text, language=self._language_code(language), voice="alice")

    @staticmethod
    def _thinking_message(language: str) -> str:
        code = (language or "").casefold()
        if code.startswith("te"):
            return "ఒక్క నిమిషం, ఆలోచిస్తున్నాను. దయచేసి వేచి ఉండండి."
        if code.startswith("hi"):
            return "एक मिनट, मैं जानकारी देख रहा हूँ। कृपया इंतज़ार कीजिए।"
        if code.startswith("ta"):
            return "ஒரு நிமிடம், தகவலைப் பார்க்கிறேன். தயவுசெய்து காத்திருக்கவும்."
        if code.startswith("kn"):
            return "ಒಂದು ನಿಮಿಷ, ಮಾಹಿತಿಯನ್ನು ಪರಿಶೀಲಿಸುತ್ತಿದ್ದೇನೆ. ದಯವಿಟ್ಟು ಕಾಯಿರಿ."
        return "One moment, I am checking the official information. Please wait."

    def _gather(self, response: VoiceResponse, text: str, base_url: str, language: str = "en-IN") -> None:
        language = self._language_code(language)
        gather = response.gather(
            input="speech",
            action=f"{base_url}/twilio/speech",
            method="POST",
            speech_timeout="auto",
            language=language,
        )
        self._speak(gather, text, language, base_url)
        response.redirect(f"{base_url}/twilio/voice")

    def welcome_twiml(self, call_id: str, phone: str) -> str:
        response = VoiceResponse()
        if not phone:
            response.say("I could not identify this phone number. Please call again.", language="en-IN")
            response.hangup()
            return str(response)
        try:
            farmer, conversation, returning = self.conversation_service.load_or_create_farmer(phone)
        except ValueError:
            response.say("I could not read your phone number. Please call again.", language="en-IN")
            response.hangup()
            return str(response)
        self.sessions[call_id] = (farmer.id, conversation)
        greeting = self._quirky_greeting(conversation.language_code, farmer.name) if returning else "Namaskaram kaka. What farming scheme or subsidy do you need help with today?"
        self._gather(response, greeting, self._base_url(), conversation.language_code or "en-IN")
        return str(response)

    def speech_twiml(self, call_id: str, phone: str, text: str) -> str:
        request_key = f"{call_id}:{hashlib.sha256(text.strip().encode('utf-8')).hexdigest()}"
        cached = self.processed_requests.get(request_key)
        if cached and cached[1] > time.monotonic():
            return cached[0]
        session = self.sessions.get(call_id)
        if session is None:
            try:
                farmer, conversation, _ = self.conversation_service.load_or_create_farmer(phone)
            except ValueError:
                response = VoiceResponse()
                response.say("I could not identify your farmer profile. Please call again.", language="en-IN")
                response.hangup()
                return str(response)
            session = (farmer.id, conversation)
            self.sessions[call_id] = session
        farmer_id, conversation = session
        if not text.strip():
            response = VoiceResponse()
            self._gather(response, "I did not hear that. Please ask your question again.", self._base_url(), conversation.language_code or "en-IN")
            return str(response)

        outcome = self.conversation_service.process_text(
            text.strip(), conversation,
            farmer_id=farmer_id,
            gemini_key=self.gemini_key,
            tavily_key=self.tavily_key,
            firecrawl_key=self.firecrawl_key,
            summarize=False,
        )
        response = VoiceResponse()
        answer = outcome.response_text or outcome.error_message or "Please try your question again."
        response.say(
            self._thinking_message(conversation.language_code or "en-IN"),
            language=self._language_code(conversation.language_code or "en-IN"),
            voice="alice",
        )
        if conversation.result.goodbye_detected:
            self._speak(response, answer, conversation.language_code or "en-IN", self._base_url())
            try:
                self.conversation_service.persist_summary(farmer_id, conversation, self.gemini_key)
                self.conversation_service.conversations.save(conversation, farmer_id)
            except Exception:
                logger.exception("Unable to persist final Twilio conversation state")
            response.hangup()
            self.sessions.pop(call_id, None)
            result = str(response)
            self.processed_requests[request_key] = (result, time.monotonic() + 600)
            return result

        if conversation.result.conversation_complete:
            self._speak(response, answer, conversation.language_code or "en-IN", self._base_url())
            try:
                self.conversation_service.persist_summary(farmer_id, conversation, self.gemini_key)
                self.conversation_service.conversations.save(conversation, farmer_id)
            except Exception:
                logger.exception("Unable to persist completed Twilio conversation state")
            response.hangup()
            self.sessions.pop(call_id, None)
            result = str(response)
            self.processed_requests[request_key] = (result, time.monotonic() + 600)
            return result

        conversation.set_state("LISTENING")
        try:
            self.conversation_service.conversations.save(conversation, farmer_id)
        except Exception:
            logger.exception("Unable to persist Twilio listening state")
        self._gather(response, answer, self._base_url(), conversation.language_code or "en-IN")
        result = str(response)
        self.processed_requests[request_key] = (result, time.monotonic() + 600)
        if len(self.processed_requests) > 500:
            self.processed_requests.pop(next(iter(self.processed_requests)))
        return result

    def audio_response(self, token: str) -> Response:
        item = self.audio.pop(token, None)
        if item is None or item[1] < time.monotonic():
            # The parent TwiML contains a <Say> fallback. Returning an empty,
            # successful media response avoids turning an expired demo token
            # into a webhook error.
            return Response(b"", status=204, mimetype="audio/wav")
        return Response(item[0], mimetype="audio/wav")

    def authorized(self) -> bool:
        token = secret("TWILIO_AUTH_TOKEN")
        signature = request.headers.get("X-Twilio-Signature", "")
        if not token:
            return False
        try:
            validation_url = request.url
            if self.public_base_url:
                validation_url = f"{self.public_base_url}{request.full_path.rstrip('?')}"
            return RequestValidator(token).validate(validation_url, request.form, signature)
        except Exception:
            logger.exception("Twilio signature validation failed")
            return False


def create_twilio_app(transport: TwilioTransport) -> Flask:
    """Create the optional webhook application without contacting Twilio."""
    app = Flask(__name__)

    @app.before_request
    def validate_request() -> None:
        if not transport.authorized():
            abort(403)

    @app.post("/twilio/voice")
    def voice() -> Response:
        try:
            body = transport.welcome_twiml(request.form.get("CallSid", "anonymous"), request.form.get("From", ""))
        except Exception:
            logger.exception("Twilio voice webhook failed")
            fallback = VoiceResponse()
            fallback.say("I am sorry, the service is temporarily unavailable. Please call again later.", language="en-IN")
            fallback.hangup()
            body = str(fallback)
        return Response(body, mimetype="application/xml")

    @app.post("/twilio/speech")
    def speech() -> Response:
        try:
            body = transport.speech_twiml(
                request.form.get("CallSid", "anonymous"),
                request.form.get("From", ""),
                request.form.get("SpeechResult", ""),
            )
        except Exception:
            logger.exception("Twilio speech webhook failed")
            fallback = VoiceResponse()
            fallback.say("I am sorry, I could not process that. Please try again.", language="en-IN")
            fallback.hangup()
            body = str(fallback)
        return Response(body, mimetype="application/xml")

    @app.get("/twilio/audio/<token>")
    def audio(token: str) -> Response:
        return transport.audio_response(token)

    @app.post("/twilio/status")
    def status() -> Response:
        transport.sessions.pop(request.form.get("CallSid", ""), None)
        return Response("", status=204)

    return app
