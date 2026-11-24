"""Deterministic signals in a customer message: objections, escalation and "are you a bot?".

Keyword rules in English, Hindi (Devanagari) and Romanised Hinglish. They are deliberately
simple and explainable; the LLM may also call ``transfer_to_human`` on its own judgement. What a
rule found is stored on the message (``meta.signals``) so staff can see why the agent acted.

Escalation kinds (ADR 0007 "handed_off"): ``human_requested`` and ``legal`` always hand the
lead to a person (the backend enforces it even if the model forgets). ``angry`` makes the agent
apologise and offer a person; a second angry message hands off. ``out_of_scope`` makes the
agent steer back to property; the model transfers if the customer insists.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.brain.quantities import nfc

OBJECTIONS: dict[str, tuple[str, ...]] = {
    "price": (
        "too expensive", "expensive", "costly", "too high", "over my budget", "out of my budget",
        "beyond my budget", "can't afford", "cannot afford", "cheaper", "price is high",
        "mehenga", "mahenga", "mehnga", "mehngaa", "budget se bahar", "budget se zyada", "sasta",
        "kam karo", "kam kar do", "price zyada", "महंगा", "महँगा", "मेहंगा", "बजट से बाहर",
        "बजट से ज़्यादा", "सस्ता", "कम करो",
    ),
    "location": (
        "too far", "far from", "very far", "don't like the area", "do not like the area",
        "location is not", "location isn't", "bad location", "too much traffic",
        "bahut door", "door hai", "location pasand nahi", "location achha nahi", "बहुत दूर",
        "दूर है", "लोकेशन पसंद नहीं", "location पसंद नहीं",
    ),
    "think_about_it": (
        "think about it", "let me think", "need some time", "need time", "will discuss",
        "discuss with", "talk to my wife", "talk to my husband", "talk to my family",
        "not now", "maybe later", "get back to you", "sochna", "soch ke", "sochke", "sochenge",
        "sochta hoon", "sochti hoon", "baad mein", "family se baat", "ghar pe baat",
        "सोचना", "सोच के", "सोचकर", "सोचेंगे", "बाद में", "घर पर बात", "फैमिली से बात",
    ),
    "competitor": (
        "other builder", "another builder", "other developer", "another developer",
        "other project", "another project", "competitor", "better deal", "better offer",
        "cheaper elsewhere", "dusre builder", "doosre builder", "dusra project", "aur builder",
        "दूसरे बिल्डर", "दूसरा प्रोजेक्ट", "दूसरे प्रोजेक्ट",
    ),
    "loan": (
        "home loan", "bank loan", "need a loan", "get a loan", "loan facility", "emi",
        "loan milega", "loan mil", "loan chahiye", "loan lena", "होम लोन", "लोन", "ईएमआई",
    ),
    "trust": (
        "track record", "reputation", "construction quality", "build quality", "quality of",
        "can i trust", "trust you", "delayed", "delay in possession", "is it safe",
        "bharosa", "quality kaisi", "quality kaisa", "time pe milega", "भरोसा", "क्वालिटी",
        "समय पर मिलेगा",
    ),
}  # fmt: skip

ESCALATIONS: dict[str, tuple[str, ...]] = {
    "human_requested": (
        "talk to a human", "speak to a human", "real person", "talk to a person",
        "speak to a person", "talk to someone", "speak to someone", "human agent", "salesperson",
        "sales person", "sales team", "your manager", "customer care", "representative",
        "call me back", "callback", "kisi insaan", "insaan se", "kisi se baat", "kisi aadmi",
        "manager se", "sales team se", "call karo", "call kariye", "इंसान से", "किसी इंसान",
        "किसी से बात", "मैनेजर से", "सेल्स टीम", "कॉल करो", "कॉल कीजिए", "call कीजिए",
    ),
    "legal": (
        "lawyer", "advocate", "legal advice", "legal notice", "legal action", "court", "sue ",
        "lawsuit", "consumer forum", "rera complaint", "police complaint", "title dispute",
        "vakeel", "wakeel", "kanooni", "kanuni", "वकील", "कानूनी", "कोर्ट", "अदालत", "मुकदमा",
        "पुलिस",
    ),
    "angry": (
        "worst", "useless", "stupid", "nonsense", "pathetic", "ridiculous", "waste of time",
        "fraud", "cheat", "scam", "angry", "frustrated", "fed up", "bakwas", "bekaar", "bekar",
        "dhokha", "pagal", "बकवास", "बेकार", "धोखा", "फ्रॉड", "पागल", "गुस्सा",
    ),
    "out_of_scope": (
        "cricket", "weather", "recipe", "write a poem", "tell me a joke", "stock market",
        "bitcoin", "crypto", "horoscope", "homework", "movie", "मौसम", "क्रिकेट", "जोक",
    ),
}  # fmt: skip

AI_QUESTION = (
    "are you a bot", "are you a robot", "are you ai", "are you an ai", "are you human",
    "are you a human", "are you real", "are you a real person", "is this a bot", "is this ai",
    "am i talking to a bot",
    "am i talking to a machine", "r u a bot", "kya aap robot", "kya aap bot", "aap robot",
    "aap bot ho", "aap ai ho", "kya aap ai", "kya aap insaan", "aap insaan ho", "machine ho",
    "क्या आप रोबोट", "क्या आप इंसान", "आप रोबोट हैं", "आप बॉट", "क्या आप AI", "आप AI हैं",
)  # fmt: skip

REQUEST_VERB = re.compile(r"(?<![a-z])(talk|speak|connect|baat|call)(?![a-z])|बात|कॉल")

# Escalations that always hand the lead to a person.
ALWAYS_HAND_OFF = frozenset({"human_requested", "legal"})


@dataclass(frozen=True)
class Signals:
    objection: str | None = None
    escalation: str | None = None
    asked_if_ai: bool = False
    matched: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {
            "objection": self.objection,
            "escalation": self.escalation,
            "asked_if_ai": self.asked_if_ai,
            "matched": dict(self.matched),
        }


def _normalise(text: str) -> str:
    text = nfc(text.lower()).replace("’", "'")
    return " " + re.sub(r"\s+", " ", text) + " "


def _first(text: str, phrases: tuple[str, ...]) -> str | None:
    for phrase in phrases:
        p = nfc(phrase.lower())
        # Word boundary on Latin letters only; Devanagari phrases match as substrings.
        if re.search(rf"(?<![a-z]){re.escape(p)}(?![a-z])", text):
            return phrase
    return None


def detect_signals(text: str) -> Signals:
    t = _normalise(text)
    matched: dict[str, str] = {}
    asked = _first(t, AI_QUESTION)
    if asked:
        matched["asked_if_ai"] = asked
    escalation = None
    for kind, phrases in ESCALATIONS.items():
        hit = _first(t, phrases)
        # "Are you a real person?" asks about the agent; it is not a request for one.
        if hit and kind == "human_requested" and asked and not REQUEST_VERB.search(t):
            continue
        if hit:
            escalation = kind
            matched["escalation"] = hit
            break
    objection = None
    for kind, phrases in OBJECTIONS.items():
        hit = _first(t, phrases)
        if hit:
            objection = kind
            matched["objection"] = hit
            break
    return Signals(objection, escalation, bool(asked), matched)
