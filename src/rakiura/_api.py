"""Calls to the notebook API with the session's key, connecting first when there is none yet."""

from http import HTTPStatus

from ._credentials import forget_saved_key
from ._login import SAVED_SOURCES, login
from ._session import RakiuraError, session
from ._transport import FIRST_ERROR_STATUS, Payload, error_code, error_message, keyed_request


def call(method: str, path: str, body: Payload | None = None) -> Payload:
    """The reply to one API call, or a RakiuraError with the line to print.

    A 401 means the key was revoked or has expired: it is forgotten here and,
    when it came from the credentials file, on this computer too.
    """
    if not session.key and not login(quiet=session.quiet):
        msg = "rakiura: not connected."
        raise RakiuraError(msg)
    status, payload = keyed_request(method, path, body)
    if status < FIRST_ERROR_STATUS and payload.get("ok"):
        return payload
    message = error_message(payload, status)
    if status == HTTPStatus.UNAUTHORIZED:
        if session.key_source in SAVED_SOURCES:
            forget_saved_key(session.url)
            message = "this connection was revoked or has expired. Run rakiura.login() to connect again."
        session.forget_key()
    msg = f"rakiura: {message}"
    raise RakiuraError(msg, error_code(payload), status=status)
