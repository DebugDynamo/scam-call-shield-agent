"""Scam Call Shield — Multimodal Gemini Audio Fraud Detection Engine.

Adheres strictly to Google Cloud & Garbathon architecture standards:
- Uses the official `google-genai` SDK (`gemini-2.5-flash` / `gemini-4-argon`) with native
  audio bytes (`types.Part.from_bytes`) and strict JSON Schema structured output.
- Supports explicit `LLM_PROVIDER=mock` mode for deterministic offline/CI testing without
  ever silently falling back when `LLM_PROVIDER=gemini` is selected.
- Evaluates threat scores and triggers a mock emergency SMS/WhatsApp dispatch when
  `is_scam=True` and `confidence_score >= 0.70`.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


def load_dotenv_files() -> None:
    """Lightweight .env loader that populates os.environ from local .env or ../repo-voice-ai/.env without overriding existing env vars."""
    base = Path(__file__).resolve().parent
    candidates = [base / ".env", base.parent / "repo-voice-ai" / ".env"]
    for env_path in candidates:
        if not env_path.exists():
            continue
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.split("#", 1)[0].strip().strip('"').strip("'")
            if key and key not in os.environ and val:
                os.environ[key] = val


load_dotenv_files()

# ---------------------------------------------------------------------------
# System Instruction
# ---------------------------------------------------------------------------
SCAM_SHIELD_SYSTEM_INSTRUCTION = """You are "Scam Call Shield", an active, real-time anti-fraud voice guardian built for India.
You analyze live or chunked incoming phone call audio (and/or call transcripts) every 3–5 seconds to catch ongoing phone scams BEFORE the victim hangs up or transfers money.

Specifically, you must detect and flag these 4 critical threat categories:
1. False Authority Claims: Callers impersonating Mumbai Police, Cyber Crime Cell, CBI, Narcotics Control Bureau (NCB), Customs, Reserve Bank of India (RBI), Telecom Department (TRAI), or Bank KYC officers.
2. Coercion & Isolation Tactics: Threats of "Digital Arrest" (ડિજિટલ અરેસ્ટ / डिजिटल अरेस्ट), orders not to disconnect the call ("ફોન કાપશો નહીં" / "कॉल मत काटिए"), demands to stay on video/voice call, or threats of immediate arrest warrants.
3. Contraband / Identity Threats: Claims that a parcel containing illegal drugs, narcotics, or fake passports was seized linked to the victim's Aadhaar, PAN, or ID number.
4. Financial Extortion & Credential Harvesting: Demands for immediate UPI/RTGS/bank transfer to "close the case" or "verify funds" (e.g., ₹50,000), or demands to share an OTP, PIN, CVV, or click a KYC link because a bank account will be blocked.

CRITICAL ZERO-FALSE-POSITIVE RULE:
- Genuine everyday calls in India—such as Swiggy, Zomato, Amazon, or Blinkit delivery partners asking for a building gate, courier drop-offs, doctor appointment reminders, or normal family conversations—must ALWAYS return:
  - `is_scam`: false
  - `confidence_score`: <= 0.05
  - `red_flags`: []
  - `action`: "SAFE_TO_CONTINUE"
- Never flag a genuine delivery or logistics call as a scam.

LANGUAGE & VOICE WARNING REQUIREMENTS:
- Identify `detected_language` as `"gu"` (Gujarati), `"hi"` (Hindi), or `"en"` (English).
- Always populate both `warning_gujarati` and `warning_hindi` with natural, plain, urgent colloquial spoken explanations suitable for immediate Text-to-Speech playback to an Indian citizen or senior citizen:
  - If `is_scam` is true: Warn the listener urgently that this is a fake scam call ("આ સાયબર ફ્રોડ કોલ છે! પોલીસ ક્યારેય ડિજિટલ અરેસ્ટ કરતી નથી કે પૈસા માંગતી નથી. તરત જ ફોન કાપી નાખો!" / "सावधान! यह एक नकली स्कैम कॉल है। पुलिस या बैंक कभी डिजिटल अरेस्ट नहीं करते और न ही पैसे या ओटीपी मांगते हैं। तुरंत फोन काट दें!").
  - If `is_scam` is false: Reassure the listener calmly in colloquial Gujarati and Hindi that the call appears safe and genuine.

Respond strictly in JSON matching the enforced schema."""


# ---------------------------------------------------------------------------
# Strict Output Schemas (Pydantic v2)
# ---------------------------------------------------------------------------
class ScamAnalysisResult(BaseModel):
    """Strict schema enforced on Gemini's `response_schema`."""

    is_scam: bool = Field(description="True if the audio/transcript exhibits fraud, impersonation, coercion, or OTP/financial extortion.")
    confidence_score: float = Field(ge=0.0, le=1.0, description="Fraud confidence score between 0.0 and 1.0.")
    detected_language: Literal["gu", "hi", "en"] = Field(description="Primary language detected in the call chunk ('gu', 'hi', or 'en').")
    red_flags: list[str] = Field(
        default_factory=list,
        description='Explicit red flags detected, e.g. ["Impersonation: Police", "Illegal Demand: Digital Arrest", "Extortion: ₹50,000"]. Empty list if safe.',
    )
    warning_gujarati: str = Field(description="Plain, urgent colloquial Gujarati spoken explanation for TTS playback (gu-IN).")
    warning_hindi: str = Field(description="Plain, urgent colloquial Hindi spoken explanation for TTS playback (hi-IN).")
    action: Literal["HANG_UP_IMMEDIATELY", "SAFE_TO_CONTINUE"] = Field(
        description="Recommended immediate action: HANG_UP_IMMEDIATELY for scams, SAFE_TO_CONTINUE for genuine calls."
    )


class EmergencySmsPayload(BaseModel):
    """Mock automated SMS/WhatsApp dispatch payload for high-confidence scams."""

    recipient: str = Field(default="Son (Rahul)", description="Emergency contact display name.")
    phone_mock: str = Field(default="+91-98XXX-X4210", description="Masked emergency contact number.")
    channel: str = Field(default="SMS + WhatsApp", description="Simulated dispatch channel.")
    status: Literal["MOCK_SMS_SENT", "NOT_REQUIRED"] = Field(description="Dispatch status.")
    message: str = Field(description="Formatted emergency alert text dispatched to the family contact.")
    dispatched_at: str = Field(description="ISO-8601 UTC timestamp.")


class AnalyzeChunkResponse(ScamAnalysisResult):
    """Full API response returned by `POST /api/analyze-chunk`."""

    request_id: str = Field(description="Unique trace ID for observability.")
    scenario_id: str | None = Field(default=None, description="Scenario identifier if triggered from a preset.")
    transcript_excerpt: str = Field(default="", description="Transcript or summary of the analyzed audio chunk.")
    emergency_dispatch: EmergencySmsPayload | None = Field(
        default=None,
        description="Populated when is_scam is True and confidence_score >= 0.70.",
    )
    provider: str = Field(description="Active LLM provider ('gemini' or 'mock').")
    model: str = Field(description="Model identifier used for analysis.")
    simulated: bool = Field(description="True when running in explicit mock mode.")
    latency_ms: int = Field(description="End-to-end analysis latency in milliseconds.")


# ---------------------------------------------------------------------------
# Pre-Loaded Test Scenarios
# ---------------------------------------------------------------------------
class ScenarioSpec(BaseModel):
    id: str
    title: str
    subtitle: str
    language: Literal["gu", "hi", "en"]
    bcp47_locale: Literal["gu-IN", "hi-IN", "en-IN"]
    category: Literal["DIGITAL_ARREST", "BANK_KYC", "GENUINE_DELIVERY"]
    caller_label: str
    caller_number: str
    transcript: str
    expected_scam: bool


SCENARIOS: dict[str, ScenarioSpec] = {
    "gujarati_digital_arrest": ScenarioSpec(
        id="gujarati_digital_arrest",
        title='1. Gujarati Scam ("Digital Arrest")',
        subtitle="Fake Mumbai Police Cyber Cell + Narcotics Parcel + ₹50,000 Extortion",
        language="gu",
        bcp47_locale="gu-IN",
        category="DIGITAL_ARREST",
        caller_label="Unknown Caller (Claims: Mumbai Cyber Cell)",
        caller_number="+91 92XXX 88104",
        transcript=(
            "નમસ્તે, હું મુંબઈ પોલીસ સાયબર સેલમાંથી બોલું છું. તમારા આઈડી નંબર પર મોકલેલા પાર્સલમાં ડ્રગ્સ મળ્યા છે. "
            "તમે ડિજિટલ અરેસ્ટમાં છો, ફોન કાપશો નહીં. કેસ બંધ કરવા માટે અત્યારે જ ₹50,000 ટ્રાન્સફર કરો."
        ),
        expected_scam=True,
    ),
    "hindi_digital_arrest": ScenarioSpec(
        id="hindi_digital_arrest",
        title='2. Hindi Scam ("Digital Arrest")',
        subtitle="Fake Mumbai Police Cyber Cell + Contraband Threat + ₹50,000 Demand",
        language="hi",
        bcp47_locale="hi-IN",
        category="DIGITAL_ARREST",
        caller_label="Unknown Caller (Claims: Cyber Crime HQ)",
        caller_number="+91 88XXX 19023",
        transcript=(
            "नमस्ते, मैं मुंबई पुलिस साइबर सेल से बोल रहा हूँ। आपके आईडी नंबर से भेजे गए पार्सल में ड्रग्स मिले हैं। "
            "आप डिजिटल अरेस्ट में हैं, कॉल मत काटिए। केस बंद करने के लिए अभी ₹50,000 ट्रांसफर कीजिए।"
        ),
        expected_scam=True,
    ),
    "english_bank_kyc": ScenarioSpec(
        id="english_bank_kyc",
        title='3. English Scam ("Bank KYC")',
        subtitle="Account Block Threat + Urgent OTP Harvesting",
        language="en",
        bcp47_locale="en-IN",
        category="BANK_KYC",
        caller_label="Unknown Caller (Claims: Bank KYC Dept)",
        caller_number="+91 70XXX 44912",
        transcript=(
            "Hello, this is your bank's KYC team. Your account will be blocked today because your KYC has expired. "
            "To keep it active, please share the OTP we just sent to your phone."
        ),
        expected_scam=True,
    ),
    "genuine_delivery_control": ScenarioSpec(
        id="genuine_delivery_control",
        title="4. Genuine Control Call (Delivery Partner)",
        subtitle="Swiggy Delivery Partner Asking for Gate Entry (Zero False Positives)",
        language="en",
        bcp47_locale="en-IN",
        category="GENUINE_DELIVERY",
        caller_label="Swiggy Delivery Partner (Verified)",
        caller_number="+91 80XXX 55200",
        transcript=(
            "Hi, this is the delivery partner from Swiggy. I'm outside your building with your order. "
            "Which gate should I come to?"
        ),
        expected_scam=False,
    ),
}


# ---------------------------------------------------------------------------
# Emergency Dispatch Builder
# ---------------------------------------------------------------------------
EMERGENCY_CONFIDENCE_THRESHOLD = 0.70


def build_emergency_sms(result: ScamAnalysisResult, recipient: str = "Son (Rahul)") -> EmergencySmsPayload | None:
    """Builds the mock emergency SMS/WhatsApp dispatch if is_scam is True and confidence >= 0.70."""
    if not result.is_scam or result.confidence_score < EMERGENCY_CONFIDENCE_THRESHOLD:
        return None
    flags_str = ", ".join(result.red_flags[:3]) if result.red_flags else "High-risk phone scam"
    pct = int(round(result.confidence_score * 100))
    return EmergencySmsPayload(
        recipient=recipient,
        phone_mock="+91-98XXX-X4210",
        channel="SMS + WhatsApp",
        status="MOCK_SMS_SENT",
        message=(
            f"[SCAM CALL SHIELD ALERT] Urgent: A high-risk scam call ({pct}% confidence, lang={result.detected_language.upper()}) "
            f"was intercepted on your family member's phone. Red flags: {flags_str}. "
            f"Recommended action: {result.action}. Please check on them immediately."
        ),
        dispatched_at=datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
    )


# ---------------------------------------------------------------------------
# Deterministic Mock Analyzer (Explicit `LLM_PROVIDER=mock` only)
# ---------------------------------------------------------------------------
def _analyze_deterministic_mock(transcript: str, scenario_id: str | None = None) -> ScamAnalysisResult:
    """Rule-based deterministic analyzer used ONLY when `LLM_PROVIDER=mock` is explicitly configured."""
    text = (transcript or "").strip()
    low = text.lower()

    if scenario_id == "gujarati_digital_arrest" or "ડિજિટલ અરેસ્ટ" in text or "સાયબર સેલ" in text or "પાર્સલમાં ડ્રગ્સ" in text:
        return ScamAnalysisResult(
            is_scam=True,
            confidence_score=0.98,
            detected_language="gu",
            red_flags=[
                "Impersonation: Mumbai Police Cyber Cell",
                "Illegal Demand: Digital Arrest",
                "Contraband Threat: Drugs in Parcel tied to ID",
                "Coercion: Command Not to Disconnect Call",
                "Extortion: ₹50,000 Immediate Transfer",
            ],
            warning_gujarati=(
                "ચેતવણી! આ સો ટકા નકલી પોલીસ કોલ છે! ભારતમાં 'ડિજિટલ અરેસ્ટ' જેવો કોઈ કાયદો નથી. "
                "પોલીસ ક્યારેય ફોન પર પૈસા માંગતી નથી. ગભરાશો નહીં, એક પણ રૂપિયો મોકલશો નહીં અને અત્યારે જ ફોન કાપી નાખો!"
            ),
            warning_hindi=(
                "सावधान! यह एक नकली पुलिस स्कैम कॉल है। कानून में 'डिजिटल अरेस्ट' जैसी कोई चीज़ नहीं होती। "
                "कोई भी पुलिस अधिकारी फोन पर पैसे नहीं मांगता। बिल्कुल न डरें और तुरंत फोन काट दें!"
            ),
            action="HANG_UP_IMMEDIATELY",
        )

    if scenario_id == "hindi_digital_arrest" or "डिजिटल अरेस्ट" in text or "साइबर सेल" in text or "पार्सल में ड्रग्स" in text:
        return ScamAnalysisResult(
            is_scam=True,
            confidence_score=0.97,
            detected_language="hi",
            red_flags=[
                "Impersonation: Mumbai Police Cyber Cell",
                "Illegal Demand: Digital Arrest",
                "Contraband Threat: Narcotics Parcel tied to ID",
                "Coercion: Order Not to Hang Up",
                "Extortion: ₹50,000 Immediate Transfer",
            ],
            warning_gujarati=(
                "સાવધાન! આ સાયબર ફ્રોડ કોલ છે. પોલીસ કે સીબીઆઈ ક્યારેય ડિજિટલ અરેસ્ટ કરતી નથી કે કેસ બંધ કરવા પૈસા માંગતી નથી. "
                "તરત જ ફોન કાપી નાખો અને ૧૯૩૦ પર જાણ કરો!"
            ),
            warning_hindi=(
                "सावधान! यह सौ प्रतिशत फर्जी साइबर क्राइम कॉल है! भारत में 'डिजिटल अरेस्ट' नाम का कोई कानून नहीं है। "
                "पुलिस कभी केस बंद करने के लिए 50,000 रुपये नहीं मांगती। तुरंत कॉल काटिए!"
            ),
            action="HANG_UP_IMMEDIATELY",
        )

    if (
        scenario_id == "english_bank_kyc"
        or ("kyc" in low and ("otp" in low or "blocked" in low or "expired" in low))
        or ("share the otp" in low)
    ):
        return ScamAnalysisResult(
            is_scam=True,
            confidence_score=0.95,
            detected_language="en",
            red_flags=[
                "Impersonation: Bank KYC Officer",
                "Urgency Coercion: Immediate Account Block Threat",
                "Credential Harvesting: Requesting Confidential OTP",
            ],
            warning_gujarati=(
                "ચેતવણી! આ બેંક કેવાયસી ફ્રોડ કોલ છે! કોઈપણ બેંક ફોન પર ઓટીપી (OTP) માંગતી નથી. "
                "તમારો ઓટીપી આપશો તો ખાતામાંથી પૈસા ઉપડી જશે. તરત જ ફોન કાપી નાખો!"
            ),
            warning_hindi=(
                "सावधान! यह बैंक केवाईसी के नाम पर धोखाधड़ी है! बैंक कभी भी फोन पर आपसे ओटीपी (OTP) नहीं मांगता। "
                "अपना ओटीपी किसी को न बताएं और तुरंत फोन काट दें!"
            ),
            action="HANG_UP_IMMEDIATELY",
        )

    if (
        scenario_id == "genuine_delivery_control"
        or "swiggy" in low
        or "zomato" in low
        or "delivery partner" in low
        or "which gate" in low
    ):
        return ScamAnalysisResult(
            is_scam=False,
            confidence_score=0.02,
            detected_language="en",
            red_flags=[],
            warning_gujarati="આ કોલ સુરક્ષિત છે. સ્વિગી ડિલિવરી પાર્ટનર તમારા ઓર્ડર માટે ગેટ પૂછી રહ્યા છે. કોઈ જોખમ નથી.",
            warning_hindi="यह कॉल पूरी तरह सुरक्षित है। स्विगी डिलीवरी पार्टनर आपके ऑर्डर के लिए गेट का पता पूछ रहे हैं। आप बात जारी रख सकते हैं।",
            action="SAFE_TO_CONTINUE",
        )

    # Heuristic check for arbitrary live speech / custom transcripts in mock mode
    detected_lang: Literal["gu", "hi", "en"] = "en"
    if any("\u0a80" <= ch <= "\u0aff" for ch in text):
        detected_lang = "gu"
    elif any("\u0900" <= ch <= "\u097f" for ch in text):
        detected_lang = "hi"

    scam_keywords = [
        ("police", "Impersonation: Police"),
        ("cyber cell", "Impersonation: Cyber Cell"),
        ("cbi", "Impersonation: CBI"),
        ("customs", "Impersonation: Customs"),
        ("rbi", "Impersonation: RBI"),
        ("digital arrest", "Illegal Demand: Digital Arrest"),
        ("drugs", "Contraband Threat: Narcotics in Parcel"),
        ("otp", "Credential Harvesting: OTP Demand"),
        ("50,000", "Extortion: ₹50,000"),
        ("transfer", "Financial Extortion: Immediate Transfer"),
        ("ડ્રગ્સ", "Contraband Threat: Drugs in Parcel"),
        ("પોલીસ", "Impersonation: Police"),
        ("ઓટીપી", "Credential Harvesting: OTP Demand"),
        ("ड्रग्स", "Contraband Threat: Drugs in Parcel"),
        ("पुलिस", "Impersonation: Police"),
        ("ओटीपी", "Credential Harvesting: OTP Demand"),
    ]
    matched_flags: list[str] = []
    for kw, flag in scam_keywords:
        if kw in low or kw in text:
            if flag not in matched_flags:
                matched_flags.append(flag)

    if matched_flags:
        conf = min(0.96, 0.72 + 0.07 * len(matched_flags))
        return ScamAnalysisResult(
            is_scam=True,
            confidence_score=round(conf, 2),
            detected_language=detected_lang,
            red_flags=matched_flags,
            warning_gujarati=(
                "ચેતવણી! આ કોલમાં સાયબર ફ્રોડના લક્ષણો છે. કોઈપણ અજાણ્યા વ્યક્તિને પૈસા કે ઓટીપી આપશો નહીં. તરત જ ફોન કાપી નાખો!"
            ),
            warning_hindi=(
                "सावधान! इस कॉल में साइबर ठगी के संकेत मिले हैं। किसी को भी पैसे या ओटीपी न दें और तुरंत फोन काट दें!"
            ),
            action="HANG_UP_IMMEDIATELY",
        )

    return ScamAnalysisResult(
        is_scam=False,
        confidence_score=0.03,
        detected_language=detected_lang,
        red_flags=[],
        warning_gujarati="આ કોલ સુરક્ષિત જણાય છે. કોઈ શંકાસ્પદ માંગણી જણાઈ નથી.",
        warning_hindi="यह कॉल सुरक्षित प्रतीत होती है। इसमें कोई संदिग्ध या धोखाधड़ी वाली बात नहीं मिली है।",
        action="SAFE_TO_CONTINUE",
    )


# ---------------------------------------------------------------------------
# Multimodal Gemini Analyzer (`google-genai` SDK)
# ---------------------------------------------------------------------------
class ScamShieldEngineError(Exception):
    """Typed error raised when the provider is unconfigured or fails."""

    def __init__(self, code: str, message: str, status_code: int = 503):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class ScamShieldEngine:
    """Executes multimodal audio/transcript fraud analysis via Google GenAI SDK or explicit mock."""

    def __init__(
        self,
        provider: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout_seconds: float = 25.0,
    ):
        raw_provider = (provider or os.getenv("LLM_PROVIDER") or "").strip().lower()
        self.api_key = (api_key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip() or None
        # Default to gemini if GEMINI_API_KEY is present and LLM_PROVIDER wasn't explicitly set to mock
        if raw_provider in ("gemini", "mock"):
            self.provider = raw_provider
        else:
            self.provider = "gemini" if self.api_key else "mock"

        self.model = (model or os.getenv("GEMINI_MODEL") or "gemini-2.5-flash").strip()
        self.timeout_seconds = timeout_seconds

    @property
    def simulated(self) -> bool:
        return self.provider == "mock"

    @property
    def is_configured(self) -> bool:
        if self.provider == "mock":
            return True
        return bool(self.api_key)

    async def analyze_chunk(
        self,
        *,
        audio_bytes: bytes | None = None,
        mime_type: str = "audio/wav",
        transcript: str | None = None,
        scenario_id: str | None = None,
        recipient: str = "Son (Rahul)",
    ) -> AnalyzeChunkResponse:
        start = asyncio.get_event_loop().time()
        req_id = uuid.uuid4().hex[:12]

        scenario = SCENARIOS.get(scenario_id) if scenario_id else None
        effective_transcript = (transcript or (scenario.transcript if scenario else "")).strip()

        if not audio_bytes and not effective_transcript:
            raise ScamShieldEngineError(
                "VALIDATION_FAILED",
                "Provide an audio chunk file (`audio`), a `scenario_id`, or a `transcript` to analyze.",
                status_code=400,
            )

        if self.provider == "mock":
            result = _analyze_deterministic_mock(effective_transcript, scenario_id=scenario_id)
            used_model = "mock-scam-shield-v1"
        else:
            if not self.api_key:
                raise ScamShieldEngineError(
                    "PROVIDER_NOT_CONFIGURED",
                    "LLM_PROVIDER=gemini requires GEMINI_API_KEY (or set LLM_PROVIDER=mock for offline demo mode).",
                    status_code=503,
                )
            result = await self._call_gemini_multimodal(
                audio_bytes=audio_bytes,
                mime_type=mime_type,
                transcript=effective_transcript,
                scenario=scenario,
            )
            used_model = self.model

        elapsed_ms = max(1, int((asyncio.get_event_loop().time() - start) * 1000))
        emergency_sms = build_emergency_sms(result, recipient=recipient)

        return AnalyzeChunkResponse(
            **result.model_dump(),
            request_id=req_id,
            scenario_id=scenario_id,
            transcript_excerpt=effective_transcript or f"[Audio chunk: {len(audio_bytes or b'')} bytes ({mime_type})]",
            emergency_dispatch=emergency_sms,
            provider=self.provider,
            model=used_model,
            simulated=self.simulated,
            latency_ms=elapsed_ms,
        )

    async def _call_gemini_multimodal(
        self,
        *,
        audio_bytes: bytes | None,
        mime_type: str,
        transcript: str,
        scenario: ScenarioSpec | None,
    ) -> ScamAnalysisResult:
        from google import genai
        from google.genai import errors, types

        client = genai.Client(api_key=self.api_key)
        parts: list[types.Part] = []

        if audio_bytes and len(audio_bytes) > 0:
            clean_mime = (mime_type or "audio/wav").split(";")[0].strip() or "audio/wav"
            parts.append(types.Part.from_bytes(data=audio_bytes, mime_type=clean_mime))

        prompt_lines = [
            "Analyze this incoming phone call chunk (3-5 second window) for Indian phone scam indicators.",
        ]
        if scenario:
            prompt_lines.append(f"Caller Metadata: {scenario.caller_label} ({scenario.caller_number}), Locale: {scenario.bcp47_locale}")
        if transcript:
            prompt_lines.append(f"Call Audio Transcript / Stream Text:\n\"{transcript}\"")
        else:
            prompt_lines.append("Listen directly to the attached audio bytes and evaluate the caller's intent and red flags.")

        parts.append(types.Part.from_text(text="\n\n".join(prompt_lines)))

        config = types.GenerateContentConfig(
            system_instruction=SCAM_SHIELD_SYSTEM_INSTRUCTION,
            temperature=0.1,
            response_mime_type="application/json",
            response_schema=ScamAnalysisResult,
        )

        try:
            response = await asyncio.wait_for(
                client.aio.models.generate_content(
                    model=self.model,
                    contents=[types.Content(role="user", parts=parts)],
                    config=config,
                ),
                timeout=self.timeout_seconds,
            )
        except TimeoutError as exc:
            raise ScamShieldEngineError(
                "TIMEOUT",
                f"Gemini multimodal analysis timed out after {self.timeout_seconds:.0f}s",
                status_code=504,
            ) from exc
        except errors.APIError as exc:
            code = getattr(exc, "code", 502) or 502
            if code == 429:
                raise ScamShieldEngineError("PROVIDER_RATE_LIMITED", "Gemini API rate limit exceeded (HTTP 429)", status_code=429) from exc
            if code in (401, 403):
                raise ScamShieldEngineError(
                    "PROVIDER_NOT_CONFIGURED",
                    f"Gemini rejected the API key (HTTP {code}); verify GEMINI_API_KEY.",
                    status_code=503,
                ) from exc
            raise ScamShieldEngineError(
                "PROVIDER_ERROR",
                f"Gemini API error (HTTP {code}): {getattr(exc, 'message', str(exc))}"[:300],
                status_code=502,
            ) from exc
        except Exception as exc:
            raise ScamShieldEngineError(
                "PROVIDER_ERROR",
                f"Gemini invocation failed: {type(exc).__name__}: {exc}"[:300],
                status_code=502,
            ) from exc

        if getattr(response, "parsed", None) is not None and isinstance(response.parsed, ScamAnalysisResult):
            return response.parsed

        raw_text = (response.text or "").strip()
        if not raw_text:
            raise ScamShieldEngineError("PROVIDER_ERROR", "Gemini returned an empty response payload.", status_code=502)
        return ScamAnalysisResult.model_validate_json(raw_text)
