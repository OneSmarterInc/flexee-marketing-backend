"""Global DRF exception handler.

DRF's default handler already turns APIException/ValidationError/PermissionDenied/etc.
into clean JSON. Anything it doesn't recognize — a plain ValueError from the engine's
own input/state validation (e.g. "history must be nonempty" when a student hits Board
Defense before any round has closed), or a genuine bug — falls through to it. Left alone,
Django then renders its HTML debug/500 page, which a JSON API client can't parse; the
frontend ends up dumping that raw markup into an error box. Converting everything to
JSON here means the API never leaks an HTML page to a JSON caller, no matter what raises.
"""
import logging

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

log = logging.getLogger("flexee.exceptions")


def api_exception_handler(exc, context):
    response = drf_exception_handler(exc, context)
    if response is not None:
        return response

    if isinstance(exc, ValueError):
        # Engine-layer input/state validation — the caller did something the current
        # game state doesn't allow yet. A 400, with the engine's own message.
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    # Anything else is unexpected — log the full traceback server-side but don't leak
    # internals (stack traces, file paths) to the client.
    log.exception("Unhandled exception in %s", context.get("view"))
    return Response({"detail": "Internal server error."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
