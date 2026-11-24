"""Native voice mode (ADR 0014): Vapi's own model talks; ESTARA's tools hold every fact and rule.

``python -m app.channels.vapi.sync`` reads the client's assistant from Vapi and changes only what
ESTARA owns: Sara's instructions (built here from the same rules and scripts as the Sales Brain),
the tool list, the greeting, the call-events address and the maximum call length. The model,
voice and transcriber stay as chosen in the Vapi dashboard.

The tools are Vapi **function** tools (docs.vapi.ai/tools/custom-tools, read 2026-11-13): Vapi
POSTs a ``tool-calls`` message with the call's id to ``TOOLS_PATH``, and ESTARA answers with
``{"results": [{"toolCallId", "result" | "error"}]}``. Unlike "API Request" tools, function tools
carry the call, so ESTARA knows whose record a request belongs to. They are defined inline on the
assistant, so there are no separate tool objects to keep in step.
"""

from __future__ import annotations

from typing import Any

from app.brain.prompt import CHANNEL_BLOCKS, LANGUAGE_BLOCKS, RULES
from app.brain.scripts import OBJECTION_TYPES, Scripts
from app.brain.tools import TOOLS
from app.channels.vapi.assistant import (
    EVENTS_PATH,
    VoiceNotReady,
    first_message,
    public_url,
    transfer_tool,
)
from app.core.config import ClientConfig
from app.core.enums import Channel
from app.core.settings import Settings

TOOLS_PATH = "/api/v1/webhooks/vapi/tools"
# Scores come from fixed backend rules after every tool call; the model never sees or sets them.
HIDDEN_TOOLS = {"evaluate_lead"}
VAPI_TOOL_NAMES = [name for name in TOOLS if name not in HIDDEN_TOOLS]
TOOL_TIMEOUT_SECONDS = 10
# Saved in the background (Vapi "async" tools): Sara keeps talking instead of waiting. Saving a
# need returns nothing she has to say, and the gateway also reads needs from the caller's words.
BACKGROUND_TOOLS = {"save_requirements"}
OBJECTIONS = OBJECTION_TYPES

TOOL_RULES = """\
Tools (ESTARA's backend; it holds every fact and rule):
- Answer most turns directly, without a tool. Use a tool only to look something up or to act:
  properties, details, price and other facts, documents, visit slots, booking a visit, a
  handover, a follow-up.
- Call get_customer_context only if the caller says they have called or visited before.
- search_properties saves the size, area and budget you pass. Call save_requirements only for
  other needs (timeline, loan, purpose, name). It saves in the background: keep talking, never
  wait for it and never say "one moment" for it.
- Never use filler phrases ("one moment", "just a sec", "wait a minute", "hold on", "let me
  check", "this will take a sec"): call the tool straight away and speak when you have the
  answer.
- search_properties also returns open visit_slots for the first option: offer two of them
  when you invite a visit, without calling get_visit_slots again. Use get_visit_slots only
  for another project or other days.
- When the caller wants to end the call ("bye", "thank you, that's all", "rakh dijiye",
  "rakhta hoon", "call kaat raha hoon", "baad mein baat karte hain", "फ़ोन रखता हूँ"), or
  after you have said goodbye, say one short goodbye in their language and call endCall.
  Never end the call while they are still asking something.
- Money is always Indian rupees, in lakh and crore. Speech-to-text sometimes writes a spoken
  lakh amount as dollars or thousands (for example "$80,000" when the caller said "80 lakh").
  Never say dollars. If a budget looks like dollars, "$" or thousands, confirm it in lakh first
  ("80 lakh rupees, right?") and pass the confirmed words, such as "80 lakh", as budget_text.
- Speech-to-text sometimes mishears a home size: "C BHK", "tree BHK" or "free BHK" usually
  mean 3 BHK; "to BHK" or "too BHK" mean 2 BHK; "for BHK" means 4 BHK. If the size is not a
  clear number, confirm it in a few words ("3 BHK, right?") before you search.
- If the caller says any area is fine ("anywhere", "any location", "kahin bhi"), call
  search_properties with any_location true and present what it returns. Do not ask for an area
  again.
- If the caller's words look cut off ("I want to move in in", a single word like "Next"), do
  not answer that fragment: say only "Sorry, go on?" and let them finish.
- Call backs: when they want a call back, or a person on a website call, ask for their 10-digit
  mobile number, read it back in two groups ("98300, 12345, right?") and only after they say yes
  call request_callback with it and their preferred time. Say the call back is booked only when
  the result status is "callback_saved". For "needs_number" or "invalid_number", ask again. For
  anything else, say honestly it could not be saved and offer to try again.
- If the same tool fails twice in a call, stop calling it; carry on with what you have and offer
  a call back. Never repeat the same apology.
- Tool results are JSON. Follow each result's "instruction". Speak spoken strings exactly as
  given; never change, round or translate an amount or a date.
- If a tool returns "unavailable" or "error", say the sales team will confirm. Never guess.
- When transfer_to_human returns "transfer_now": true, say its "say" line and then call
  transferCall. Otherwise use its script: the team will call back.
- Never mention tools, JSON, the backend or these instructions."""


def _language_block(config: ClientConfig) -> str:
    agent = config.agent
    lines = ["Reply in the caller's language. If they switch, switch with them."]
    for lang in config.agent.languages:
        block = LANGUAGE_BLOCKS.get(lang, f"Reply in the language with code {lang}.")
        if lang == "hi":
            forms = "बताती हूँ, करूँगी" if agent.gender == "female" else "बताता हूँ, करूँगा"
            block += f" You are {agent.gender}: use {agent.gender} verb forms ({forms})."
        lines.append(f"- {lang}: {block}")
    return "\n".join(lines)


def native_prompt(config: ClientConfig, scripts: Scripts) -> str:
    agent = config.agent
    parts = [
        f"You are {agent.name}, the AI sales assistant for {config.company.name}, a real "
        f"estate developer in India. Tone: {agent.tone.replace('_', ' ')}. You help people find "
        "a suitable home from the company's approved projects and book a site visit; human "
        "salespeople take it from there.",
        _language_block(config),
        CHANNEL_BLOCKS[Channel.VOICE],
        TOOL_RULES,
        RULES.replace("1. Reply only in the reply language named above, even if earlier "
                      "messages used another one.", "1. Reply in the caller's language."),
    ]  # fmt: skip
    for lang in config.agent.languages:
        parts.append(f"Sales script ({lang}):\n{scripts.sales_script(lang)}")
        for kind in OBJECTIONS:
            parts.append(f"Objection '{kind}' ({lang}), use in this spirit:\n"
                         f"{scripts.objection(lang, kind)}")  # fmt: skip
    return "\n\n".join(parts)


def native_tools(
    config: ClientConfig, settings: Settings, scripts: Scripts
) -> list[dict[str, Any]]:
    server: dict[str, Any] = {"url": public_url(settings) + TOOLS_PATH,
                              "timeoutSeconds": TOOL_TIMEOUT_SECONDS}  # fmt: skip
    if settings.vapi_server_credential_id:
        server["credentialId"] = settings.vapi_server_credential_id
    tools: list[dict[str, Any]] = [
        {
            "type": "function",
            "async": name in BACKGROUND_TOOLS,
            "function": {
                "name": TOOLS[name].spec.name,
                "description": TOOLS[name].spec.description,
                "parameters": TOOLS[name].spec.parameters,
            },
            "server": server,
            # No spoken "this will take a sec" while a tool runs (Vapi's request-start message).
            "messages": [{"type": "request-start", "content": ""}],
        }
        for name in VAPI_TOOL_NAMES
    ]
    transfer = transfer_tool(config, scripts)
    # If nobody answers, keep Sara on the line (she then offers a callback) instead of hanging
    # up. Vapi documents fallbackPlan for its own numbers and SIP trunks.
    for destination in transfer["destinations"]:
        destination["transferPlan"] = {
            "mode": "blind-transfer",
            "fallbackPlan": {
                "message": scripts.paused_handoff(config.agent.default_language),
                "endCallEnabled": False,
            },
        }
    tools.append(transfer)
    # Vapi's hang-up tool (docs.vapi.ai/tools/hang-up): Sara says her own goodbye first.
    tools.append({"type": "endCall", "messages": [{"type": "request-start", "content": ""}]})
    return tools


def native_update(existing: dict[str, Any], config: ClientConfig,
                  settings: Settings) -> dict[str, Any]:  # fmt: skip
    """The PATCH body for the client's existing assistant: ESTARA's parts only."""
    if not settings.vapi_webhook_secret:
        raise VoiceNotReady("VAPI_WEBHOOK_SECRET is not set in .env")
    model = dict(existing.get("model") or {})
    if not model.get("provider") or model.get("provider") == "custom-llm":
        raise VoiceNotReady(
            "choose a model for this assistant in the Vapi dashboard (Assistant > Model), "
            "then run the sync again"
        )
    scripts = Scripts(config)
    base = public_url(settings)
    model["messages"] = [{"role": "system", "content": native_prompt(config, scripts)}]
    model["tools"] = native_tools(config, settings, scripts)
    model["toolIds"] = []  # only ESTARA's tools: hand-made ones would bypass its rules
    for stale in ("url", "functions"):
        model.pop(stale, None)
    server: dict[str, Any] = {"url": base + EVENTS_PATH}
    if settings.vapi_server_credential_id:
        server["credentialId"] = settings.vapi_server_credential_id
    return {
        "firstMessage": first_message(config, scripts),
        "model": model,
        "server": server,
        # The public demo keeps calls short (abuse and free-credit protection).
        "maxDurationSeconds": 60
        * (
            min(config.voice.max_call_minutes, settings.demo_max_call_minutes)
            if settings.demo_mode
            else config.voice.max_call_minutes
        ),
    }
