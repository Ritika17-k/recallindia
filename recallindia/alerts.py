import smtplib
from datetime import date, datetime
from email.message import EmailMessage
from pathlib import Path
from . import config
from .clean import is_placeholder_reason
from .matching import normalize_batch

NOTICE_URL = {"FSSAI": "https://foscos.fssai.gov.in/food-recall"}
FOOTER = ("RecallIndia passes on official recall notices. It does not test products, and a recall applies only to "
          "the batches named in the notice.")


def _s(v):
    if v is None:
        return ""
    if isinstance(v, float) and v != v:
        return ""
    if isinstance(v, (date, datetime)):
        return v.strftime("%d %b %Y")
    return str(v).strip()

def reason_text(reason):
    """The reason line, or a plain statement that the notice gives none."""
    r = _s(reason)
    if not r or is_placeholder_reason(r):
        return "The notice does not give a usable reason. Open it on the regulator's portal for details."
    return r

def batch_advice(purchase_batch, recall_batch):
    pb, rb = normalize_batch(purchase_batch), normalize_batch(recall_batch)
    if pb and rb and (pb == rb or (min(len(pb), len(rb)) >= 4 and (pb in rb or rb in pb))):
        return f"The batch number you recorded ({_s(purchase_batch)}) matches the recalled batch."
    if rb:
        return f"Check the batch or lot number on your pack. The recall covers batch {_s(recall_batch)}."
    return "The notice does not list a batch number, so check the notice before using the product."

def build_message(row):
    """Subject and body for one visible match. `row` comes from db.alertable()."""
    source = _s(row.get("recall_source")) or "FSSAI"
    recalled = " - ".join(x for x in (_s(row.get("recall_brand")), _s(row.get("recall_product"))) if x)
    bought = _s(row.get("purchase_product"))
    if _s(row.get("purchase_brand")) and _s(row.get("purchase_brand")).lower() not in bought.lower():
        bought = f"{_s(row.get('purchase_brand'))} {bought}"
    where = _s(row.get("purchase_store"))
    when = _s(row.get("purchase_date"))
    lines = [
        f"Hi {_s(row.get('user_name')) or 'there'},",
        "",
        f"Something you bought matches {'an' if source == 'FSSAI' else 'a'} {source} recall notice.",
        "",
        f"You bought: {bought}" + (f" from {where}" if where else "") + (f" on {when}" if when else ""),
        f"Recall notice {_s(row.get('recall_id'))}: {recalled}"
        + (f" ({_s(row.get('recall_fbo'))})" if _s(row.get("recall_fbo")) else ""),
    ]
    if _s(row.get("recall_date")):
        lines.append(f"Recall started: {_s(row.get('recall_date'))}"
                     + (f", status {_s(row.get('recall_status'))}" if _s(row.get("recall_status")) else ""))
    if _s(row.get("recall_type")):
        lines.append(f"Type: {_s(row.get('recall_type'))}")
    lines += [
        f"Reason: {reason_text(row.get('recall_reason'))}",
        "",
        "What to do",
        f"- {batch_advice(row.get('purchase_batch'), row.get('recall_batch'))}",
        "- If it is the recalled batch, stop using it and return it to the shop or contact the company.",
        f"- Notice: {NOTICE_URL.get(source, '')}",
        "",
        "Why you got this: " + ("a reviewer confirmed this match." if row.get("status") == "approved"
                                else f"the brand and product match the notice (score {row.get('score'):.0f}/100)."),
        "",
        FOOTER,
    ]
    subject = f"Recall alert: {recalled or bought} ({source} notice {_s(row.get('recall_id'))})"
    return subject, "\n".join(lines)

def _email(alert):
    msg = EmailMessage()
    msg["From"] = config.ALERT_FROM
    msg["To"] = alert.get("email") or f"{alert.get('user_id')}@users.recallindia.local"
    msg["Subject"] = alert["subject"]
    msg.set_content(alert["body"])
    return msg

def send(alert, channel=None):
    """Deliver one alert. Raises on failure so the caller can mark it failed."""
    channel = channel or alert.get("channel") or config.ALERT_CHANNEL
    if channel == "console":
        print(f"--- alert {alert.get('id')} to {alert.get('user_id')} ---\n{alert['subject']}\n\n{alert['body']}\n")
    elif channel == "outbox":
        out = Path(config.OUTBOX_DIR)
        out.mkdir(parents=True, exist_ok=True)
        (out / f"alert_{alert.get('id')}.eml").write_bytes(bytes(_email(alert)))
    elif channel == "email":
        if not alert.get("email"):
            raise ValueError(f"user {alert.get('user_id')} has no email address")
        if not config.SMTP_HOST:
            raise ValueError("SMTP_HOST is not set")
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=30) as smtp:
            smtp.starttls()
            if config.SMTP_USER:
                smtp.login(config.SMTP_USER, config.SMTP_PASSWORD)
            smtp.send_message(_email(alert))
    else:
        raise ValueError(f"unknown alert channel {channel!r}")

def queue_alerts(engine, channel=None):
    """Create one queued alert for every visible match that has none yet."""
    from .db import alertable, insert_alerts
    channel = channel or config.ALERT_CHANNEL
    rows = []
    for row in alertable(engine):
        subject, body = build_message(row)
        rows.append({"match_id": row["match_id"], "user_id": row["user_id"], "channel": channel,
                     "subject": subject, "body": body})
    return insert_alerts(engine, rows)

def dispatch(engine, channel=None):
    """Send every queued alert. Returns (sent, failed)."""
    from . import push
    from .db import mark_alert, queued_alerts
    sent = failed = 0
    for alert in queued_alerts(engine):
        pushed = push.send_to_user(engine, alert.get("user_id"), push.notification_for(alert))
        try:
            send(alert, channel or alert["channel"])
            mark_alert(engine, alert["id"], "sent")
            sent += 1
        except Exception as e:  # keep going; the error is stored on the alert
            if pushed:  # the phone notification still reached the user
                mark_alert(engine, alert["id"], "sent", f"pushed; {channel or alert['channel']} failed: {e}"[:500])
                sent += 1
            else:
                mark_alert(engine, alert["id"], "failed", str(e)[:500])
                failed += 1
    return sent, failed
