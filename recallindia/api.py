import re
from datetime import date
from typing import Optional
import pandas as pd
from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker
from . import __version__
from .clean import normalize_name
from .config import MATCH_THRESHOLD
from . import openfoodfacts
from .checker import ProductNotFound, check_product
from .db import (FoodCategory, Match, Product, Purchase, Recall, Substance, candidates, get_engine, load_rulebook,
                 search_recalls, upsert)
from .matching import score_pair

engine = get_engine()
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
app = FastAPI(title="RecallIndia API", version=__version__)
_rulebook = {}

def rulebook():
    if "rb" not in _rulebook:
        _rulebook["rb"] = load_rulebook(engine)
    return _rulebook["rb"]

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
    parts: dict
    notes: list[str]

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
def match(req: MatchRequest, threshold: float = MATCH_THRESHOLD, session: Session = Depends(get_session)):
    purchase = {"product_norm": normalize_name(req.product_name), "brand_norm": normalize_name(req.brand),
                "batch_no": req.batch_no.upper().strip() if req.batch_no else None, "purchase_date": req.purchase_date}
    out = []
    for c in candidates(engine, purchase["product_norm"], purchase["brand_norm"]):
        recall = session.get(Recall, c["recall_id"])
        r = {col.name: getattr(recall, col.name) for col in Recall.__table__.columns}
        score, parts, notes = score_pair(r, purchase)
        if score >= threshold:
            out.append(MatchOut(recall=RecallOut.model_validate(recall), score=score, parts=parts, notes=notes))
    return sorted(out, key=lambda m: m.score, reverse=True)

@app.get("/purchases/{purchase_id}/matches", response_model=list[MatchOut])
def purchase_matches(purchase_id: str, session: Session = Depends(get_session)):
    if not session.get(Purchase, purchase_id):
        raise HTTPException(404, "Purchase not found")
    rows = session.execute(select(Match, Recall).join(Recall, Match.recall_id == Recall.recall_id)
                           .where(Match.purchase_id == purchase_id).order_by(Match.score.desc())).all()
    return [MatchOut(recall=RecallOut.model_validate(r), score=m.score,
                     parts={k: v for k, v in {"product": m.product_score, "brand": m.brand_score,
                                              "batch": m.batch_score}.items() if v is not None},
                     notes=[n for n in (m.notes or "").split("; ") if n]) for m, r in rows]

@app.get("/stats")
def stats(session: Session = Depends(get_session)):
    by_category = session.execute(select(Recall.category, func.count()).group_by(Recall.category)
                                  .order_by(func.count().desc())).all()
    by_month = session.execute(select(func.to_char(Recall.recall_date, "YYYY-MM").label("m"), func.count())
                               .where(Recall.recall_date.isnot(None)).group_by("m").order_by("m")).all()
    return {
        "recalls": session.scalar(select(func.count(Recall.recall_id))),
        "purchases": session.scalar(select(func.count(Purchase.purchase_id))),
        "matches": session.scalar(select(func.count(Match.id))),
        "by_category": [{"category": c or "Unknown", "count": n} for c, n in by_category],
        "by_month": [{"month": m, "count": n} for m, n in by_month],
    }

@app.post("/check")
def check(req: CheckRequest):
    try:
        return check_product(rulebook(), barcode=req.barcode, ingredients_text=req.ingredients_text,
                             category=req.category, product_name=req.product_name, brand=req.brand,
                             fetch_product=cached_product, recall_lookup=recall_lookup)
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
    _rulebook.pop("rb", None)
    rb = rulebook()
    return {"substances": len(rb.substances), "rules": len(rb.rules)}