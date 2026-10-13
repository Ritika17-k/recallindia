import re
import tempfile
from datetime import date
from pathlib import Path
from typing import Literal, Optional
import pandas as pd
from fastapi import Depends, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker
from . import __version__, alerts
from .clean import normalize_name
from .config import AUTO_PUBLISH_SCORE, REVIEW_MIN_SCORE, SOURCES
from . import openfoodfacts
from .checker import ProductNotFound, check_product
from .db import (Alert, FoodCategory, Match, Product, Purchase, Recall, Run, Substance, User, candidates, decide,
                 get_engine, load_global_status, load_rulebook, review_queue, search_recalls, upsert)
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
                    "additive_codes": [c for c in (p.additive_codes or "").split(",") if c], "source": p.source}
    product = openfoodfacts.fetch(barcode)
    if product:
        row = {**product, "additive_codes": ",".join(product["additive_codes"]), "barcode": barcode}
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

class Decision(BaseModel):
    decision: Literal["approve", "reject"]
    reviewer: Optional[str] = None
    note: Optional[str] = None

def _match_out(m, r):
    return MatchOut(recall=RecallOut.model_validate(r), score=m.score, confidence=confidence(m.score), status=m.status,
                    parts={k: v for k, v in {"product": m.product_score, "brand": m.brand_score,
                                             "batch": m.batch_score}.items() if v is not None},
                    notes=[n for n in (m.notes or "").split("; ") if n])

def _ingest(user_id, purchases):
    with SessionLocal() as s:
        if not s.get(User, user_id):
            raise HTTPException(404, "User not found. Create it with POST /users first.")
    summary = ingest_purchases(engine, purchases, user_id=user_id)
    summary.pop("matches", None)
    summary["purchase_ids"] = list(purchases["purchase_id"]) if len(purchases) else []
    summary["lines"] = purchases[["purchase_id", "product_name", "store", "purchase_date"]].astype(str).to_dict(
        "records") if len(purchases) else []
    return summary

@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/docs")

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

@app.get("/purchases/{purchase_id}/matches", response_model=list[MatchOut])
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
    upsert(engine, User, pd.DataFrame([user.model_dump()]), "user_id")
    return user

@app.get("/users/{user_id}/matches", response_model=list[MatchOut])
def user_matches(user_id: str, session: Session = Depends(get_session)):
    rows = session.execute(select(Match, Recall).join(Recall, Match.recall_id == Recall.recall_id)
                           .join(Purchase, Match.purchase_id == Purchase.purchase_id)
                           .where(Purchase.user_id == user_id, Match.status.in_(VISIBLE))
                           .order_by(Match.created_at.desc())).all()
    return [_match_out(m, r) for m, r in rows]

@app.get("/users/{user_id}/alerts")
def user_alerts(user_id: str, session: Session = Depends(get_session)):
    rows = session.scalars(select(Alert).where(Alert.user_id == user_id).order_by(Alert.created_at.desc())).all()
    return [{"id": a.id, "match_id": a.match_id, "status": a.status, "channel": a.channel, "subject": a.subject,
             "body": a.body, "created_at": a.created_at, "sent_at": a.sent_at} for a in rows]

@app.post("/users/{user_id}/purchases")
def add_purchases(user_id: str, items: list[PurchaseIn]):
    """Add purchases typed in or exported from elsewhere, then match and alert."""
    rows = [i.model_dump() for i in items]
    key = "|".join(f"{r['product_name']}{r['purchase_date']}" for r in rows)
    return _ingest(user_id, to_purchases(rows, user_id, "manual", key=key))

@app.post("/users/{user_id}/receipts")
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
    finally:
        Path(tmp.name).unlink(missing_ok=True)
    summary = _ingest(user_id, purchases)
    summary["receipt"] = {"store": parsed["store"], "purchase_date": parsed["purchase_date"]}
    return summary

@app.post("/users/{user_id}/order-emails")
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
    return summary

# ---- review queue ----

@app.get("/review")
def list_review(limit: int = Query(50, le=200), offset: int = 0):
    """Medium-confidence matches waiting for a decision. These are never shown to users until approved."""
    return review_queue(engine, limit=limit, offset=offset)

@app.post("/review/{match_id}")
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

@app.post("/admin/alerts/send")
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


@app.post("/admin/reload-rules")
def reload_rules():
    _rulebook.clear()
    rb = rulebook()
    return {"substances": len(rb.substances), "rules": len(rb.rules), "global_status": len(global_status().table)}