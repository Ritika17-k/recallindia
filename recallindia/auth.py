import base64
import hashlib
import hmac
import json
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
AUTH_ENABLED = bool(GOOGLE_CLIENT_ID)
ADMIN_EMAILS = {e.strip().lower() for e in os.environ.get("ADMIN_EMAILS", "").split(",") if e.strip()}
SECRET_KEY = os.environ.get("SECRET_KEY") or secrets.token_hex(32)  # set it in production or sessions end on restart
SESSION_DAYS = int(os.environ.get("SESSION_DAYS", "30"))
TOKENINFO = "https://oauth2.googleapis.com/tokeninfo"
GOOGLE_ISSUERS = {"accounts.google.com", "https://accounts.google.com"}


class AuthError(Exception):
    pass


def _b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def make_session(user_id, days=None, now=None):
    payload = {"u": user_id, "exp": int((now or time.time()) + 86400 * (days or SESSION_DAYS))}
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    sig = _b64(hmac.new(SECRET_KEY.encode(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}"


def read_session(token, now=None):
    """The user id inside a valid, unexpired session token, else None."""
    try:
        body, sig = str(token).split(".", 1)
        good = _b64(hmac.new(SECRET_KEY.encode(), body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, good):
            return None
        payload = json.loads(_unb64(body))
        return payload["u"] if payload.get("exp", 0) > (now or time.time()) else None
    except (ValueError, KeyError, TypeError):
        return None


def bearer(authorization):
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


def check_google_claims(claims, client_id=None, now=None):
    """Validate the fields of a Google ID token (as returned by the tokeninfo endpoint)."""
    client_id = client_id or GOOGLE_CLIENT_ID
    if claims.get("aud") != client_id:
        raise AuthError("This sign-in was meant for a different app.")
    if claims.get("iss") not in GOOGLE_ISSUERS:
        raise AuthError("Not a Google sign-in.")
    if int(claims.get("exp", 0)) <= (now or time.time()):
        raise AuthError("Sign-in expired. Please try again.")
    if str(claims.get("email_verified", "")).lower() not in ("true", "1"):
        raise AuthError("Your Google email isn't verified.")
    if not claims.get("sub"):
        raise AuthError("Sign-in is missing an account id.")
    return claims


def verify_google_token(id_token, timeout=10):
    """Ask Google to verify the ID token from the sign-in button, then check it is for this app."""
    url = f"{TOKENINFO}?{urllib.parse.urlencode({'id_token': id_token})}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            claims = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise AuthError("Google didn't accept this sign-in.") from e
    except urllib.error.URLError as e:
        raise AuthError(f"Couldn't reach Google: {e.reason}") from e
    return check_google_claims(claims)


def google_user_id(claims):
    return f"g{claims['sub']}"
