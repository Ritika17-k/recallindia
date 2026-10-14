import re
import tempfile
from datetime import date
from pathlib import Path
from typing import Literal, Optional
import pandas as pd
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker
from . import __version__, alerts, auth, push
from .clean import normalize_name
from .config import AUTO_PUBLISH_SCORE, REVIEW_MIN_SCORE, SOURCES
from . import openfoodfacts
from .checker import ProductNotFound, check_product
from .db import (Alert, FoodCategory, Match, Product, Purchase, Recall, Run, User, candidates, decide,
                 delete_purchase, get_engine, load_global_status, load_rulebook, review_queue, search_recalls,
                 set_feedback, update_purchase, upsert, user_alerts, user_purchases, user_summary)
from .global_status import GlobalStatus
from .intake import to_purchases
from .matching import score_pair
from .pipeline import ingest_purchases
from .review import VISIBLE, band, confidence

engine = get_engine()
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
app = FastAPI(title="RecallIndia API", version=__version__)
_rulebook = {}

def rulebook():
    if "rb" not in _rulebook:
        _rulebook["rb"] = load_rulebook(engine)
    return _rulebook["rb"]

def global_status():
    if "gs" not in _rulebook:
        _rulebook["gs"] = GlobalStatus(rulebook(), load_global_status(engine))
    return _rulebook["gs"]

def cached_product(barcode):
    with SessionLocal() as s:
        p = s.get(Product, barcode)
        if p:
            return {"barcode": p.barcode, "product_name": p.product_name, "brand": p.brand,
                    "ingredients_text": p.ingredients_text, "category": p.category, "image_url": p.image_url,
                    "additive_codes": [c for c in (p.additive_codes or "").split(",") if c],
                    "categories_tags": [c for c in (p.categories_tags or "").split(",") if c], "source": p.source}
    product = openfoodfacts.fetch(barcode)
    if product:
        row = {**product, "additive_codes": ",".join(product["additive_codes"]),
               "categories_tags": ",".join(product.get("categories_tags") or []), "barcode": barcode}
        upsert(engine, Product, pd.DataFrame([row]), "barcode")
    return product

def recall_lookup(product_norm, brand_norm):
    with SessionLocal() as s:
        out = []
        for c in candidates(engine, product_norm, brand_norm):
            r = s.get(Recall, c["recall_id"])
            out.append({col.name: getattr(r, col.name) for col in Recall.__table__.columns})
        return out

def get_session():
    with SessionLocal() as s:
        yield s

def require_owner(user_id: str, authorization: Optional[str] = Header(None)):
    """With Google sign-in on, a user's data is only for that user's session."""
    if not auth.AUTH_ENABLED:
        return
    uid = auth.read_session(auth.bearer(authorization))
    if not uid:
        raise HTTPException(401, "Please sign in again.")
    if uid != user_id:
        raise HTTPException(403, "That isn't your account.")

def require_admin(authorization: Optional[str] = Header(None)):
    """With Google sign-in on, review and admin actions need a signed-in user listed in ADMIN_EMAILS."""
    if not auth.AUTH_ENABLED:
        return
    uid = auth.read_session(auth.bearer(authorization))
    if not uid:
        raise HTTPException(401, "Please sign in again.")
    with SessionLocal() as s:
        u = s.get(User, uid)
    if not u or (u.email or "").lower() not in auth.ADMIN_EMAILS:
        raise HTTPException(403, "Admins only.")

OWNER = [Depends(require_owner)]
ADMIN = [Depends(require_admin)]

class RecallOut(BaseModel):
    recall_id: str
    product_name: str
    brand: Optional[str] = None
    category: Optional[str] = None
    batch_no: Optional[str] = None
    fbo_name: Optional[str] = None
    recall_type: Optional[str] = None
    reason: Optional[str] = None
    hazard: Optional[str] = None
    recall_class: Optional[str] = None
    recall_date: Optional[date] = None
    termination_date: Optional[date] = None
    manufacture_date: Optional[date] = None
    expiry_date: Optional[date] = None
    status: Optional[str] = None
    state: Optional[str] = None

    model_config = {"from_attributes": True}

class MatchRequest(BaseModel):
    product_name: str
    brand: Optional[str] = None
    batch_no: Optional[str] = None
    purchase_date: Optional[date] = None

class CheckRequest(BaseModel):
    barcode: Optional[str] = None
    ingredients_text: Optional[str] = None
    category: Optional[str] = None
    product_name: Optional[str] = None
    brand: Optional[str] = None

class MatchOut(BaseModel):
    recall: RecallOut
    score: float
    confidence: str
    status: Optional[str] = None
    parts: dict
    notes: list[str]

class UserIn(BaseModel):
    user_id: str
    email: Optional[str] = None
    name: Optional[str] = None
    alerts_enabled: bool = True

class PurchaseIn(BaseModel):
    product_name: str
    brand: Optional[str] = None
    batch_no: Optional[str] = None
    store: Optional[str] = None
    purchase_date: Optional[date] = None
    barcode: Optional[str] = None
    ingredients_text: Optional[str] = None

class PurchaseUpdate(BaseModel):
    product_name: Optional[str] = None
    brand: Optional[str] = None
    batch_no: Optional[str] = None
    store: Optional[str] = None
    status: Optional[Literal["active", "finished"]] = None

class Feedback(BaseModel):
    answer: Literal["has_batch", "not_my_batch"]

class Decision(BaseModel):
    decision: Literal["approve", "reject"]
    reviewer: Optional[str] = None
    note: Optional[str] = None

def _match_out(m, r):
    return MatchOut(recall=RecallOut.model_validate(r), score=m.score, confidence=confidence(m.score), status=m.status,
                    parts={k: v for k, v in {"product": m.product_score, "brand": m.brand_score,
                                             "batch": m.batch_score}.items() if v is not None},
                    notes=[n for n in (m.notes or "").split("; ") if n])

def _remove_temp(path):
    """Delete an uploaded temp file. On Windows a file still held open can't be deleted; leave it for the OS."""
    try:
        Path(path).unlink(missing_ok=True)
    except PermissionError:
        pass

def _ingest(user_id, purchases):
    with SessionLocal() as s:
        if not s.get(User, user_id):
            raise HTTPException(404, "User not found. Create it with POST /users first.")
    summary = ingest_purchases(engine, purchases, user_id=user_id)
    summary.pop("matches", None)
    summary["purchase_ids"] = list(purchases["purchase_id"]) if len(purchases) else []
    cols = ["purchase_id", "product_name", "store", "purchase_date"]
    summary["lines"] = [{c: "" if pd.isna(r[c]) else str(r[c]) for c in cols}
                        for r in purchases[cols].to_dict("records")] if len(purchases) else []
    return summary

APP_DIR = Path(__file__).parent / "static" / "app"

@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/app/")

@app.get("/app", include_in_schema=False)
def app_redirect():
    return RedirectResponse("/app/")

@app.get("/app/{path:path}", include_in_schema=False)
def app_files(path: str = ""):
    """The RecallIndia web app (a PWA). Unknown paths return the app so links inside it work."""
    target = (APP_DIR / (path or "index.html")).resolve()
    if APP_DIR.resolve() not in target.parents or not target.is_file():
        target = APP_DIR / "index.html"
    headers = {"Service-Worker-Allowed": "/app/"} if target.name == "sw.js" else None
    return FileResponse(target, headers=headers)

@app.get("/health")
def health(session: Session = Depends(get_session)):
    return {"status": "ok", "version": __version__, "recalls": session.scalar(select(func.count(Recall.recall_id)))}

@app.get("/recalls", response_model=list[RecallOut])
def list_recalls(q: Optional[str] = None, category: Optional[str] = None,
                 limit: int = Query(50, le=200), offset: int = 0, session: Session = Depends(get_session)):
    return search_recalls(session, q=normalize_name(q) if q else None, category=category, limit=limit, offset=offset)

@app.get("/recalls/{recall_id}", response_model=RecallOut)
def get_recall(recall_id: str, session: Session = Depends(get_session)):
    recall = session.get(Recall, recall_id.upper())
    if not recall:
        raise HTTPException(404, "Recall not found")
    return recall

@app.get("/recalls/{recall_id}/alternatives")
def recall_alternatives(recall_id: str, q: Optional[str] = None, limit: int = Query(4, ge=1, le=8),
                        session: Session = Depends(get_session)):
    """Similar products from other brands (Open Food Facts, sold in India) that match no FSSAI recall.
    `q` is the user's own product name, when there is one; otherwise the search comes from the recall itself."""
    from .alternatives import find_recall_alternatives
    recall = session.get(Recall, recall_id.upper())
    if not recall:
        raise HTTPException(404, "Recall not found")
    row = {col.name: getattr(recall, col.name) for col in Recall.__table__.columns}
    try:
        found = find_recall_alternatives(row, openfoodfacts.search_products, recall_lookup=recall_lookup, query=q,
                                         limit=limit)
    except openfoodfacts.ProductLookupError as e:
        raise HTTPException(502, str(e))
    found["recall_id"] = recall.recall_id
    found["note"] = ("Other brands from Open Food Facts with no FSSAI recall on record. "
                     "Not a safety rating; check the label yourself.")
    return found

@app.post("/match", response_model=list[MatchOut])
def match(req: MatchRequest, include_medium: bool = False, session: Session = Depends(get_session)):
    """Recalls matching one product. Only high-confidence matches unless include_medium=true (for reviewers)."""
    purchase = {"product_norm": normalize_name(req.product_name), "brand_norm": normalize_name(req.brand),
                "batch_no": req.batch_no.upper().strip() if req.batch_no else None, "purchase_date": req.purchase_date}
    floor = REVIEW_MIN_SCORE if include_medium else AUTO_PUBLISH_SCORE
    out = []
    for c in candidates(engine, purchase["product_norm"], purchase["brand_norm"]):
        recall = session.get(Recall, c["recall_id"])
        r = {col.name: getattr(recall, col.name) for col in Recall.__table__.columns}
        score, parts, notes = score_pair(r, purchase)
        if score >= floor:
            out.append(MatchOut(recall=RecallOut.model_validate(recall), score=score, confidence=confidence(score),
                                status=band(score), parts=parts, notes=notes))
    return sorted(out, key=lambda m: m.score, reverse=True)

@app.get("/purchases/{purchase_id}/matches", response_model=list[MatchOut], dependencies=ADMIN)
def purchase_matches(purchase_id: str, session: Session = Depends(get_session)):
    """Published matches for one purchase. Matches waiting for review are not shown."""
    if not session.get(Purchase, purchase_id):
        raise HTTPException(404, "Purchase not found")
    rows = session.execute(select(Match, Recall).join(Recall, Match.recall_id == Recall.recall_id)
                           .where(Match.purchase_id == purchase_id, Match.status.in_(VISIBLE))
                           .order_by(Match.score.desc())).all()
    return [_match_out(m, r) for m, r in rows]

# ---- users and intake ----

@app.post("/users")
def create_user(user: UserIn):
    if auth.AUTH_ENABLED:
        raise HTTPException(403, "Use Sign in with Google.")
    upsert(engine, User, pd.DataFrame([user.model_dump()]), "user_id")
    return user

class PushIn(BaseModel):
    endpoint: str
    keys: dict

@app.get("/push/config")
def push_config():
    return {"enabled": push.PUSH_ENABLED, "public_key": push.VAPID_PUBLIC_KEY or None}

@app.post("/users/{user_id}/push", dependencies=OWNER)
def push_subscribe(user_id: str, sub: PushIn):
    """Turn on phone notifications for this browser."""
    if not push.PUSH_ENABLED:
        raise HTTPException(400, "Notifications aren't set up on this server (VAPID keys).")
    if not sub.keys.get("p256dh") or not sub.keys.get("auth"):
        raise HTTPException(422, "Subscription is missing its keys.")
    from .db import save_push_subscription
    save_push_subscription(engine, user_id, sub.endpoint, sub.keys["p256dh"], sub.keys["auth"])
    return {"subscribed": True}

@app.post("/users/{user_id}/push/remove", dependencies=OWNER)
def push_unsubscribe(user_id: str, sub: PushIn):
    from .db import delete_push_subscription
    delete_push_subscription(engine, user_id, sub.endpoint)
    return {"subscribed": False}

@app.post("/users/{user_id}/push/test", dependencies=OWNER)
def push_test(user_id: str):
    """Send a test notification to the user's devices."""
    n = push.send_to_user(engine, user_id, {"title": "RecallIndia notifications are on",
                                            "body": "You'll get a notification like this if something you bought is recalled.",
                                            "url": "/app/#/home", "tag": "test"})
    return {"sent": n}

class GoogleLogin(BaseModel):
    credential: str

@app.get("/auth/config")
def auth_config():
    return {"auth_enabled": auth.AUTH_ENABLED, "google_client_id": auth.GOOGLE_CLIENT_ID or None}

@app.post("/auth/google")
def google_login(body: GoogleLogin):
    """Exchange the ID token from Google's sign-in button for a RecallIndia session."""
    if not auth.AUTH_ENABLED:
        raise HTTPException(400, "Google sign-in isn't set up on this server (GOOGLE_CLIENT_ID).")
    try:
        claims = auth.verify_google_token(body.credential)
    except auth.AuthError as e:
        raise HTTPException(401, str(e))
    user_id = auth.google_user_id(claims)
    upsert(engine, User, pd.DataFrame([{"user_id": user_id, "name": claims.get("name") or claims.get("email"),
                                        "email": claims.get("email")}]), "user_id")
    return {"token": auth.make_session(user_id),
            "user": {"user_id": user_id, "name": claims.get("name"), "email": claims.get("email"),
                     "picture": claims.get("picture")},
            "is_admin": (claims.get("email") or "").lower() in auth.ADMIN_EMAILS}

@app.get("/users/{user_id}/matches", response_model=list[MatchOut], dependencies=OWNER)
def user_matches(user_id: str, session: Session = Depends(get_session)):
    rows = session.execute(select(Match, Recall).join(Recall, Match.recall_id == Recall.recall_id)
                           .join(Purchase, Match.purchase_id == Purchase.purchase_id)
                           .where(Purchase.user_id == user_id, Match.status.in_(VISIBLE))
                           .order_by(Match.created_at.desc())).all()
    return [_match_out(m, r) for m, r in rows]

@app.get("/users/{user_id}", dependencies=OWNER)
def get_user(user_id: str, session: Session = Depends(get_session)):
    u = session.get(User, user_id)
    if not u:
        raise HTTPException(404, "User not found")
    return {"user_id": u.user_id, "name": u.name, "email": u.email, "alerts_enabled": u.alerts_enabled}

@app.get("/users/{user_id}/summary", dependencies=OWNER)
def summary(user_id: str):
    """Home screen: products watched, which are affected, latest recalls."""
    return user_summary(engine, user_id)

@app.get("/users/{user_id}/alerts", dependencies=OWNER)
def list_user_alerts(user_id: str):
    """The user's recall alerts with everything an alert card shows. Unanswered ones first."""
    rows = user_alerts(engine, user_id)
    for r in rows:
        r["reason_text"] = alerts.reason_text(r["recall_reason"])
        r["batch_advice"] = alerts.batch_advice(r["purchase_batch"], r["recall_batch"])
        r["notice_url"] = alerts.NOTICE_URL.get(r["recall_source"] or "FSSAI")
    return rows

@app.post("/users/{user_id}/alerts/{alert_id}/feedback", dependencies=OWNER)
def alert_feedback(user_id: str, alert_id: int, fb: Feedback):
    """The user's answer on an alert: they have the recalled batch, or not."""
    if not set_feedback(engine, user_id, alert_id, fb.answer):
        raise HTTPException(404, "Alert not found")
    return {"alert_id": alert_id, "feedback": fb.answer}

@app.get("/users/{user_id}/purchases", dependencies=OWNER)
def list_purchases(user_id: str):
    """My Products: every purchase with the published recalls it matches."""
    return user_purchases(engine, user_id)

@app.patch("/users/{user_id}/purchases/{purchase_id}", dependencies=OWNER)
def edit_purchase(user_id: str, purchase_id: str, change: PurchaseUpdate):
    """Add a batch number, fix a name, or mark a product finished. Rematches when name, brand or batch change."""
    try:
        rematch = update_purchase(engine, user_id, purchase_id, change.model_dump(exclude_none=True))
    except KeyError:
        raise HTTPException(404, "Purchase not found")
    result = {"purchase_id": purchase_id, "rematched": rematch}
    if rematch:
        from .db import match_from_db, save_matches
        save_matches(engine, match_from_db(engine, purchase_ids=[purchase_id]))
        result["alerts_queued"] = alerts.queue_alerts(engine)
        result["alerts_sent"] = alerts.dispatch(engine)[0]
    return result

@app.delete("/users/{user_id}/purchases/{purchase_id}", dependencies=OWNER)
def remove_purchase(user_id: str, purchase_id: str):
    if not delete_purchase(engine, user_id, purchase_id):
        raise HTTPException(404, "Purchase not found")
    return {"deleted": purchase_id}

@app.post("/users/{user_id}/purchases", dependencies=OWNER)
def add_purchases(user_id: str, items: list[PurchaseIn]):
    """Add purchases typed in or exported from elsewhere, then match and alert."""
    rows = [i.model_dump() for i in items]
    key = "|".join(f"{r['product_name']}{r['purchase_date']}" for r in rows)
    return _ingest(user_id, to_purchases(rows, user_id, "manual", key=key))

@app.post("/users/{user_id}/receipts", dependencies=OWNER)
async def upload_receipt(user_id: str, file: UploadFile = File(...)):
    """A receipt photo (OCR) or a .txt receipt."""
    from .receipts import import_receipt
    suffix = Path(file.filename or "receipt.jpg").suffix or ".jpg"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(await file.read())
    try:
        purchases, parsed = import_receipt(tmp.name, user_id)
    except RuntimeError as e:
        raise HTTPException(501, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))
    finally:
        _remove_temp(tmp.name)
    summary = _ingest(user_id, purchases)
    summary["receipt"] = {"store": parsed["store"], "purchase_date": parsed["purchase_date"]}
    summary["not_tracked"] = [i["product_name"] for i in parsed.get("not_tracked", [])]
    return summary

@app.post("/users/{user_id}/order-emails", dependencies=OWNER)
async def upload_order_emails(user_id: str, file: UploadFile = File(...)):
    """An .eml order email or a Google Takeout .mbox of Gmail."""
    from .email_import import import_messages, iter_messages
    suffix = Path(file.filename or "mail.mbox").suffix or ".mbox"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(await file.read())
    try:
        purchases, orders = import_messages(iter_messages(tmp.name), user_id)
    finally:
        Path(tmp.name).unlink(missing_ok=True)
    summary = _ingest(user_id, purchases)
    summary["order_emails"] = len(orders)
    summary["not_tracked"] = [i["product_name"] for o in orders for i in o.get("not_tracked", [])]
    return summary

# ---- review queue ----

@app.get("/review", dependencies=ADMIN)
def list_review(limit: int = Query(50, le=200), offset: int = 0):
    """Medium-confidence matches waiting for a decision. These are never shown to users until approved."""
    return review_queue(engine, limit=limit, offset=offset)

@app.post("/review/{match_id}", dependencies=ADMIN)
def review_decision(match_id: int, d: Decision, send: bool = True):
    if not decide(engine, match_id, d.decision == "approve", reviewer=d.reviewer, note=d.note):
        raise HTTPException(409, "Match is not pending review (already decided or unknown ID)")
    result = {"match_id": match_id, "status": "approved" if d.decision == "approve" else "rejected"}
    if d.decision == "approve" and send:
        result["alerts_queued"] = alerts.queue_alerts(engine)
        result["alerts_sent"], result["alerts_failed"] = alerts.dispatch(engine)
    return result

@app.get("/admin/review", response_class=HTMLResponse)
def review_page():
    return (Path(__file__).parent / "static" / "review.html").read_text(encoding="utf-8")

@app.post("/admin/alerts/send", dependencies=ADMIN)
def send_alerts():
    queued = alerts.queue_alerts(engine)
    sent, failed = alerts.dispatch(engine)
    return {"queued": queued, "sent": sent, "failed": failed}

# ---- reference ----

@app.get("/sources")
def sources():
    return SOURCES

@app.get("/global-status")
def global_status_lookup(q: str):
    """An ingredient's regulatory status in India and abroad. Ingredient-level only; no product or brand input."""
    return global_status().lookup(q)

@app.get("/global-status/divergent")
def global_status_divergent():
    """Ingredients permitted in India that at least one other regulator does not authorise."""
    return global_status().divergent()

@app.get("/stats")
def stats(session: Session = Depends(get_session)):
    by_category = session.execute(select(Recall.category, func.count()).group_by(Recall.category)
                                  .order_by(func.count().desc())).all()
    by_month = session.execute(select(func.to_char(Recall.recall_date, "YYYY-MM").label("m"), func.count())
                               .where(Recall.recall_date.isnot(None)).group_by("m").order_by("m")).all()
    by_status = session.execute(select(Match.status, func.count()).group_by(Match.status)).all()
    alerts_by_status = session.execute(select(Alert.status, func.count()).group_by(Alert.status)).all()
    last = session.scalars(select(Run).where(Run.finished_at.isnot(None)).order_by(Run.id.desc()).limit(1)).first()
    return {
        "recalls": session.scalar(select(func.count(Recall.recall_id))),
        "users": session.scalar(select(func.count(User.user_id))),
        "purchases": session.scalar(select(func.count(Purchase.purchase_id))),
        "matches": session.scalar(select(func.count(Match.id))),
        "matches_by_status": {s or "unknown": n for s, n in by_status},
        "alerts_by_status": {s or "unknown": n for s, n in alerts_by_status},
        "last_run": {"finished_at": last.finished_at, "new_recalls": last.new_recalls,
                     "auto_published": last.auto_published, "pending_review": last.pending_review,
                     "alerts_sent": last.alerts_sent} if last else None,
        "by_category": [{"category": c or "Unknown", "count": n} for c, n in by_category],
        "by_month": [{"month": m, "count": n} for m, n in by_month],
    }

@app.post("/check")
def check(req: CheckRequest):
    try:
        return check_product(rulebook(), barcode=req.barcode, ingredients_text=req.ingredients_text,
                             category=req.category, product_name=req.product_name, brand=req.brand,
                             fetch_product=cached_product, recall_lookup=recall_lookup,
                             global_status=global_status())
    except ValueError as e:
        raise HTTPException(422, str(e))
    except ProductNotFound as e:
        raise HTTPException(404, str(e))
    except openfoodfacts.ProductLookupError as e:
        raise HTTPException(502, str(e))

@app.post("/check/photo")
async def check_photo(file: UploadFile = File(...), category: Optional[str] = Form(None),
                      product_name: Optional[str] = Form(None), brand: Optional[str] = Form(None)):
    """A photo of the ingredient list on a pack: OCR, then the same check as /check."""
    from .checker import label_ingredients
    from .receipts import ocr_image
    suffix = Path(file.filename or "label.jpg").suffix or ".jpg"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(await file.read())
    try:
        text = ocr_image(tmp.name)
    except RuntimeError as e:
        raise HTTPException(501, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))
    finally:
        _remove_temp(tmp.name)
    ingredients = label_ingredients(text)
    if len(ingredients) < 5:
        raise HTTPException(422, "Couldn't read an ingredient list in that photo. Try a closer, sharper photo of "
                                 "the ingredients, or type them.")
    try:
        result = check_product(rulebook(), ingredients_text=ingredients, category=category or None,
                               product_name=product_name, brand=brand, recall_lookup=recall_lookup,
                               global_status=global_status())
    except (ValueError, ProductNotFound) as e:
        raise HTTPException(422, str(e))
    result["product"]["source"] = "label photo"
    result["ocr_text"] = text
    return result

@app.get("/alternatives")
def alternatives(barcode: str, category: Optional[str] = None, limit: int = Query(4, le=8)):
    """Similar products (same Open Food Facts category, sold in India) that don't list the scanned product's
    flagged ingredients and have no high-confidence recall match. Facts only, not a safety ranking."""
    from .alternatives import find_alternatives
    try:
        product = cached_product(barcode)
    except ValueError as e:
        raise HTTPException(422, str(e))
    except openfoodfacts.ProductLookupError as e:
        raise HTTPException(502, str(e))
    if not product:
        raise HTTPException(404, "Barcode not found on Open Food Facts")
    check = check_product(rulebook(), barcode=barcode, category=category, fetch_product=lambda _: product)
    try:
        found = find_alternatives(rulebook(), product, check, openfoodfacts.search_category,
                                  recall_lookup=recall_lookup, limit=limit)
    except openfoodfacts.ProductLookupError as e:
        raise HTTPException(502, str(e))
    found["flagged"] = [f["substance"] for f in check["findings"] if f["severity"] > 0]
    found["note"] = "Matched by category and listed ingredients. Not a safety rating."
    return found

@app.get("/categories")
def categories(session: Session = Depends(get_session)):
    return [{"code": c.code, "name": c.name} for c in session.scalars(select(FoodCategory).order_by(FoodCategory.code))]


@app.get("/substances")
def substances(q: Optional[str] = None, limit: int = Query(20, le=100)):
    rb = rulebook()
    if q:
        code = re.search(r"(\d{3,4}[a-z]?)", q.lower())
        ids = [rb.find_by_code(code.group(1)) if code else None, rb.find_by_name(normalize_name(q))[0]]
        ids = list(dict.fromkeys(i for i in ids if i))
    else:
        ids = list(rb.substances.index[:limit])
    return [{**rb.substances.loc[i].to_dict(), "rules": rb.rules[rb.rules["substance_id"] == i].to_dict("records")}
            for i in ids]


@app.post("/admin/reload-rules", dependencies=ADMIN)
def reload_rules():
    _rulebook.clear()
    rb = rulebook()
    return {"substances": len(rb.substances), "rules": len(rb.rules), "global_status": len(global_status().table)}