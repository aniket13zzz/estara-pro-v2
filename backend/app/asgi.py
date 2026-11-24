"""ASGI entry point for uvicorn: ``uvicorn app.asgi:app``.

Importing this module validates config and settings; an invalid config stops the process with
exit code 1 and a list of problems.
"""

from app.main import build_app_or_exit

app = build_app_or_exit()
