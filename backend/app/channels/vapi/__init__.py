"""Vapi voice channel: Vapi is the voice; every spoken reply comes from the Sales Brain.

How it fits together (ADR 0012):

- ``assistant.py`` builds ONE Vapi assistant from ``client.config.yaml``; ``sync.py`` creates or
  updates it through the Vapi API. Nobody edits it by hand in the Vapi dashboard.
- The assistant uses Vapi's "custom LLM" model: each caller turn is sent to
  ``POST /api/v1/webhooks/vapi/llm/chat/completions`` (``llm.py``). The Sales Brain answers it
  with the same Truth Layer, number guard, language check and privacy rules as every channel.
- Vapi's server messages (``assistant-request`` for phone numbers, ``end-of-call-report``,
  ``status-update``) arrive at ``POST /api/v1/webhooks/vapi/events`` (``events.py``).
- Every request must carry our shared secret (``auth.py``).
"""
