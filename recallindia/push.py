"""Phone notifications for recall alerts (Web Push).

Needs VAPID keys (`python -m recallindia vapid-keys` prints a pair for .env) and the pywebpush package. Browsers allow
push only on https (or localhost). Without keys, push is simply off and alerts still arrive in the app and by email.
"""
import base64
import json
import os

VAPID_PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY", "").strip()
VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY", "").strip()
VAPID_SUBJECT = os.environ.get("VAPID_SUBJECT", "mailto:alerts@recallindia.local").strip()
PUSH_ENABLED = bool(VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY)


def _b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def generate_vapid_keys():
    """A new P-256 key pair: (public key for the browser, raw private key for the server), both base64url."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    key = ec.generate_private_key(ec.SECP256R1())
    private_raw = key.private_numbers().private_value.to_bytes(32, "big")
    public_raw = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return _b64url(public_raw), _b64url(private_raw)


def notification_for(alert):
    """The short notification for one alert row from db.queued_alerts()."""
    subject = alert.get("subject") or "Recall alert"
    title = subject.split(" (")[0].replace("Recall alert: ", "Recall: ")
    first = next((line[2:] for line in (alert.get("body") or "").splitlines() if line.startswith("- ")), "")
    return {"title": title[:80], "body": (first or "Something you bought matches a recall notice.")[:160],
            "url": "/app/#/alerts", "tag": f"alert-{alert.get('id')}"}


def send_to_user(engine, user_id, payload):
    """Push `payload` to every device the user turned notifications on for. Returns how many succeeded."""
    if not PUSH_ENABLED or not user_id:
        return 0
    try:
        from pywebpush import WebPushException, webpush
    except ImportError:
        return 0
    from .db import delete_push_subscription, push_subscriptions
    sent = 0
    for sub in push_subscriptions(engine, user_id):
        info = {"endpoint": sub["endpoint"], "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]}}
        try:
            webpush(subscription_info=info, data=json.dumps(payload), vapid_private_key=VAPID_PRIVATE_KEY,
                    vapid_claims={"sub": VAPID_SUBJECT}, ttl=86400)
            sent += 1
        except WebPushException as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            if status in (404, 410):  # the browser unsubscribed or the subscription expired
                delete_push_subscription(engine, user_id, sub["endpoint"])
        except Exception:  # one bad device must not stop the others
            continue
    return sent
