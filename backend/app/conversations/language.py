"""Deterministic language detection for inbound messages and reply checks.

The backend owns the conversation language (ADR 0004); the LLM never decides it. Detection is a
small rule set, not a model, so it is fast, testable and explainable:

- Devanagari text is Hindi.
- Roman-script text with several common Hindi words ("mujhe", "chahiye", "kitne", ...) is
  Hinglish, which the product treats as Hindi: replies are spoken Hinglish in Devanagari.
- Other Roman-script text is English.
- Short or neutral messages ("ok", "2 BHK", "yes") are inconclusive: the conversation keeps its
  current language.

Only languages listed in ``agent.languages`` are ever returned; a detector exists here for each
language the template can speak. Detected languages outside the config fall back to the
conversation's current language (or ``agent.default_language``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

WORD = re.compile(r"[A-Za-z]+|[ऀ-ॿ]+")
DEVANAGARI_LETTER = re.compile(r"[ऀ-ॿ]")
LATIN_LETTER = re.compile(r"[A-Za-z]")

# Romanised Hindi function words and very common verbs/nouns in property enquiries. Ambiguous
# English words ("do", "main", "the", "to", "par", "hi") are deliberately left out.
HINGLISH_MARKERS: frozenset[str] = frozenset(
    """
    mujhe mujhko humein hume hamein aapko aapka aapki aapke aap apna apni apne mera meri mere
    hamara hamari chahiye chahie chahta chahti chahte chaahiye hai hain hoga hogi honge tha thi
    kya kyun kyon kaise kaisa kaisi kitna kitni kitne kab kahan kaha kaun kaunsa konsa kidhar
    nahi nahin nai haan han ji accha acha achha theek thik bilkul sahi
    aur ya lekin magar par bhi sirf abhi baad pehle phir wahan yahan udhar idhar
    ke ka ki ko se mein mai tak wala wali wale liye saath
    batao bataiye bataye dikhao dikhaiye dikhaye karo kariye karna karenge karunga karungi
    dekhna dekhenge dekhte lena lenge rehna rehne rahne khud ghar makaan mahina mahine saal
    dedh dhai sawa paune saadhe sadhe bees tees chalis pachas pachaas saath sattar assi nabbe
    ek teen char chaar paanch panch chhe saat aath nau das bajat zyada jyada kam thoda bahut
    bhai bhaiya didi sahab samajh pata matlab
    ne isse usse isko usko unko diya diye dena raha rahi rahe karni karta karti karte hoon hun
    gaya gayi gaye kuch koi sab baat chalega padega milega jayega jaayega aaj kal jeeta
    chhodo yaar dusre doosre dusra sasta mehenga mehnga
    """.split()
)

# Share of Devanagari letters that makes a message Hindi. Hinglish in Devanagari keeps project
# names and loanwords in Latin letters ("Sunrise Lakeview में कौन से payment plan हैं?" is about
# 30% Devanagari), and English speakers do not write Devanagari, so the bar is low.
DEVANAGARI_MIN_RATIO = 0.15

# Words that, alone, carry no language signal.
NEUTRAL: frozenset[str] = frozenset({"ok", "okay", "bhk", "yes", "no", "hi", "hello", "thanks"})


@dataclass(frozen=True)
class Detection:
    language: str
    confident: bool
    reason: str


def devanagari_ratio(text: str) -> float:
    dev = len(DEVANAGARI_LETTER.findall(text))
    lat = len(LATIN_LETTER.findall(text))
    return dev / (dev + lat) if dev + lat else 0.0


def detect(text: str) -> Detection:
    """Detect en or hi (Hinglish counts as hi). ``confident`` is False for neutral text."""
    ratio = devanagari_ratio(text)
    if ratio >= DEVANAGARI_MIN_RATIO:
        return Detection("hi", True, "devanagari")
    words = [w.lower() for w in WORD.findall(text) if LATIN_LETTER.match(w)]
    content = [w for w in words if w not in NEUTRAL]
    markers = sum(1 for w in content if w in HINGLISH_MARKERS)
    if markers >= 2 or (markers == 1 and len(content) <= 3):
        return Detection("hi", True, f"hinglish:{markers}")
    if len(content) >= 3:
        return Detection("en", True, "latin")
    return Detection("en", False, "too_short")


def choose_language(
    text: str, *, current: str | None, languages: list[str], default_language: str
) -> tuple[str, bool]:
    """Language for this message and whether it is a switch from ``current``.

    Inconclusive messages keep ``current``. A detected language that the client has not
    configured is ignored.
    """
    fallback = current if current in languages else default_language
    result = detect(text)
    if not result.confident or result.language not in languages:
        return fallback, False
    switched = current is not None and result.language != current
    return result.language, switched


def reply_language(text: str) -> str:
    """Language of an agent reply: Hindi replies are Hinglish written in Devanagari."""
    return "hi" if devanagari_ratio(text) >= DEVANAGARI_MIN_RATIO else "en"
