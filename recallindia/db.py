import math

import pandas as pd
from sqlalchemy import (Column, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint,
                        create_engine, func, select, text)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import DeclarativeBase, Session

from .config import DATABASE_URL


class Base(DeclarativeBase):
    pass


class Recall(Base):
    __tablename__ = "recalls"
    recall_id = Column(String(64), primary_key=True)
    product_name = Column(Text, nullable=False)
    brand = Column(Text)
    category = Column(Text)
    batch_no = Column(String(128))
    fbo_name = Column(Text)
    license_no = Column(String(32))
    license_type = Column(Text)
    recall_type = Column(Text)
    reason = Column(Text)
    hazard = Column(Text)
    recall_class = Column(Text)
    recall_date = Column(Date)
    termination_date = Column(Date)
    manufacture_date = Column(Date)
    expiry_date = Column(Date)
    status = Column(Text)
    state = Column(Text)
    product_norm = Column(Text, nullable=False, default="")
    brand_norm = Column(Text, nullable=False, default="")
    fbo_norm = Column(Text, nullable=False, default="")
    search_norm = Column(Text, nullable=False, default="")
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class Purchase(Base):
    __tablename__ = "purchases"
    purchase_id = Column(String(64), primary_key=True)
    product_name = Column(Text, nullable=False)
    brand = Column(Text)
    batch_no = Column(String(128))
    store = Column(Text)
    purchase_date = Column(Date)
    product_norm = Column(Text, nullable=False, default="")
    brand_norm = Column(Text, nullable=False, default="")


class Match(Base):
    __tablename__ = "matches"
    id = Column(Integer, primary_key=True, autoincrement=True)
    purchase_id = Column(String(64), ForeignKey("purchases.purchase_id", ondelete="CASCADE"), nullable=False)
    recall_id = Column(String(64), ForeignKey("recalls.recall_id", ondelete="CASCADE"), nullable=False)
    score = Column(Float, nullable=False)
    product_score = Column(Float)
    brand_score = Column(Float)
    batch_score = Column(Float)
    notes = Column(Text)
    created_at = Column(DateTime, server_default=func.now())
    __table_args__ = (UniqueConstraint("purchase_id", "recall_id"),)


class FoodCategory(Base):
    __tablename__ = "food_categories"
    code = Column(String(16), primary_key=True)
    name = Column(Text, nullable=False)


class Substance(Base):
    __tablename__ = "substances"
    substance_id = Column(String(64), primary_key=True)
    ins = Column(String(8))
    name = Column(Text, nullable=False)
    kind = Column(Text)
    aliases = Column(Text)


class Rule(Base):
    __tablename__ = "rules"
    id = Column(Integer, primary_key=True, autoincrement=True)
    substance_id = Column(String(64), ForeignKey("substances.substance_id", ondelete="CASCADE"), nullable=False)
    category = Column(String(16), nullable=False)
    status = Column(String(32), nullable=False)
    max_level = Column(Text)
    unit = Column(Text)
    note = Column(Text)
    source = Column(Text)
    source_url = Column(Text)
    verified = Column(String(8))
    effective_from = Column(Text)
    __table_args__ = (UniqueConstraint("substance_id", "category", "status"),)


class Product(Base):
    __tablename__ = "products"
    barcode = Column(String(14), primary_key=True)
    product_name = Column(Text)
    brand = Column(Text)
    ingredients_text = Column(Text)
    additive_codes = Column(Text)
    category = Column(String(16))
    image_url = Column(Text)
    source = Column(Text)
    fetched_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


REFERENCE_TABLES = [("food_categories.csv", FoodCategory, "code"), ("substances.csv", Substance, "substance_id")]

INDEXES = [
    "CREATE EXTENSION IF NOT EXISTS pg_trgm",
    "CREATE INDEX IF NOT EXISTS recalls_product_trgm ON recalls USING gin (product_norm gin_trgm_ops)",
    "CREATE INDEX IF NOT EXISTS recalls_brand_trgm ON recalls USING gin (brand_norm gin_trgm_ops)",
    "CREATE INDEX IF NOT EXISTS recalls_search_trgm ON recalls USING gin (search_norm gin_trgm_ops)",
    "CREATE INDEX IF NOT EXISTS recalls_date ON recalls (recall_date)",
]

CANDIDATE_SQL = text("""
    SELECT recall_id, word_similarity(:query, search_norm) AS sim
    FROM recalls
    WHERE :query <% search_norm
    ORDER BY sim DESC
    LIMIT :limit
""")
WORD_SIMILARITY_THRESHOLD = 0.3


def get_engine(url=None):
    return create_engine(url or DATABASE_URL, future=True, pool_pre_ping=True)


def init_db(engine):
    with engine.begin() as conn:
        conn.execute(text(INDEXES[0]))
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        for stmt in INDEXES[1:]:
            conn.execute(text(stmt))
        conn.execute(text("SELECT set_limit(0.2)"))


def _records(df, columns):
    out = []
    for rec in df[columns].to_dict("records"):
        out.append({k: (None if (isinstance(v, float) and math.isnan(v)) or v is pd.NaT else v)
                    for k, v in rec.items()})
    return out


def upsert(engine, model, df, key):
    cols = [c.name for c in model.__table__.columns if c.name in df.columns]
    rows = _records(df, cols)
    if not rows:
        return 0
    stmt = insert(model.__table__).values(rows)
    stmt = stmt.on_conflict_do_update(index_elements=[key],
                                      set_={c: stmt.excluded[c] for c in cols if c != key})
    with engine.begin() as conn:
        conn.execute(stmt)
    return len(rows)


def load_reference(engine, directory):
    from pathlib import Path
    d = Path(directory)
    counts = {}
    for name, model, key in REFERENCE_TABLES:
        counts[model.__tablename__] = upsert(engine, model, pd.read_csv(d / name, dtype=str, keep_default_na=False), key)
    rules = pd.read_csv(d / "rules.csv", dtype=str, keep_default_na=False)
    with engine.begin() as conn:
        conn.execute(Rule.__table__.delete())
        if len(rules):
            conn.execute(Rule.__table__.insert(), rules.to_dict("records"))
    counts["rules"] = len(rules)
    return counts


def load_rulebook(engine):
    from .rulebook import RuleBook
    with engine.connect() as conn:
        frames = [pd.read_sql(select(m.__table__), conn).fillna("").astype(str) for m in (Substance, Rule, FoodCategory)]
    return RuleBook(*frames)


def read_table(engine, model):
    with engine.connect() as conn:
        return pd.read_sql(select(model.__table__), conn)


def candidates(engine, product_norm, brand_norm="", limit=25):
    from .matching import tokens
    query = " ".join(tokens(f"{brand_norm or ''} {product_norm or ''}".strip()))
    if not query:
        return []
    with engine.connect() as conn:
        conn.execute(text(f"SET pg_trgm.word_similarity_threshold = {WORD_SIMILARITY_THRESHOLD}"))
        rows = conn.execute(CANDIDATE_SQL, {"query": query, "limit": limit}).mappings().all()
    return [dict(r) for r in rows]


def save_matches(engine, matches):
    if matches.empty:
        return 0
    rows = _records(matches, ["purchase_id", "recall_id", "score", "product_score", "brand_score",
                              "batch_score", "notes"])
    stmt = insert(Match.__table__).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["purchase_id", "recall_id"],
        set_={c: stmt.excluded[c] for c in ["score", "product_score", "brand_score", "batch_score", "notes"]})
    with engine.begin() as conn:
        conn.execute(stmt)
    return len(rows)


def match_from_db(engine, threshold):
    from .matching import score_pair
    recalls = read_table(engine, Recall).set_index("recall_id", drop=False)
    purchases = read_table(engine, Purchase)
    rows = []
    for p in purchases.to_dict("records"):
        for c in candidates(engine, p["product_norm"], p["brand_norm"]):
            r = recalls.loc[c["recall_id"]].to_dict()
            score, parts, reasons = score_pair(r, p)
            if score >= threshold:
                rows.append({"purchase_id": p["purchase_id"], "recall_id": r["recall_id"], "score": score,
                             "product_score": parts.get("product"), "brand_score": parts.get("brand"),
                             "batch_score": parts.get("batch"), "notes": "; ".join(reasons)})
    cols = ["purchase_id", "recall_id", "score", "product_score", "brand_score", "batch_score", "notes"]
    return pd.DataFrame(rows, columns=cols)


def search_recalls(session: Session, q=None, category=None, limit=50, offset=0):
    stmt = select(Recall)
    if q:
        stmt = stmt.where(Recall.product_norm.op("%")(q.lower()) | Recall.brand_norm.op("%")(q.lower())) \
            .order_by(func.similarity(Recall.product_norm, q.lower()).desc())
    else:
        stmt = stmt.order_by(Recall.recall_date.desc().nullslast())
    if category:
        stmt = stmt.where(Recall.category.ilike(f"%{category}%"))
    return session.execute(stmt.limit(limit).offset(offset)).scalars().all()
