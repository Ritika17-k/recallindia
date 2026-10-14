import math
import pandas as pd
from sqlalchemy import (Boolean, Column, Date, DateTime, Float, ForeignKey, Integer, String, Text,
                        UniqueConstraint, create_engine, func, select, text)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import DeclarativeBase, Session
from .config import AUTO_PUBLISH_SCORE, DATABASE_URL, DEFAULT_USER_ID

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
    source = Column(String(16), nullable=False, server_default="FSSAI")
    first_seen_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

class User(Base):
    __tablename__ = "users"
    user_id = Column(String(64), primary_key=True)
    email = Column(Text)
    name = Column(Text)
    alerts_enabled = Column(Boolean, nullable=False, server_default="true")
    created_at = Column(DateTime, server_default=func.now())

class Purchase(Base):
    __tablename__ = "purchases"
    purchase_id = Column(String(64), primary_key=True)
    user_id = Column(String(64), ForeignKey("users.user_id", ondelete="CASCADE"))
    product_name = Column(Text, nullable=False)
    brand = Column(Text)
    batch_no = Column(String(128))
    store = Column(Text)
    purchase_date = Column(Date)
    product_norm = Column(Text, nullable=False, default="")
    brand_norm = Column(Text, nullable=False, default="")
    source = Column(String(16))
    barcode = Column(String(14))
    ingredients_text = Column(Text)
    status = Column(String(16), nullable=False, server_default="active")
    created_at = Column(DateTime, server_default=func.now())

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
    status = Column(String(16), nullable=False, server_default="pending_review")
    reviewed_at = Column(DateTime)
    reviewed_by = Column(Text)
    review_note = Column(Text)
    created_at = Column(DateTime, server_default=func.now())
    __table_args__ = (UniqueConstraint("purchase_id", "recall_id"),)

class Alert(Base):
    __tablename__ = "alerts"
    id = Column(Integer, primary_key=True, autoincrement=True)
    match_id = Column(Integer, ForeignKey("matches.id", ondelete="CASCADE"), nullable=False, unique=True)
    user_id = Column(String(64), ForeignKey("users.user_id", ondelete="CASCADE"))
    channel = Column(String(16), nullable=False)
    status = Column(String(16), nullable=False, server_default="queued")
    subject = Column(Text, nullable=False)
    body = Column(Text, nullable=False)
    error = Column(Text)
    feedback = Column(String(16))
    feedback_at = Column(DateTime)
    created_at = Column(DateTime, server_default=func.now())
    sent_at = Column(DateTime)

class PushSubscription(Base):
    __tablename__ = "push_subscriptions"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(64), ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False)
    endpoint = Column(Text, nullable=False, unique=True)
    p256dh = Column(Text, nullable=False)
    auth = Column(Text, nullable=False)
    created_at = Column(DateTime, server_default=func.now())

class Run(Base):
    __tablename__ = "runs"
    id = Column(Integer, primary_key=True, autoincrement=True)
    started_at = Column(DateTime, server_default=func.now())
    finished_at = Column(DateTime)
    new_recalls = Column(Integer)
    auto_published = Column(Integer)
    pending_review = Column(Integer)
    alerts_queued = Column(Integer)
    alerts_sent = Column(Integer)

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

class GlobalStatus(Base):
    __tablename__ = "global_status"
    id = Column(Integer, primary_key=True, autoincrement=True)
    substance_id = Column(String(64), ForeignKey("substances.substance_id", ondelete="CASCADE"), nullable=False)
    jurisdiction = Column(String(16), nullable=False)
    status = Column(String(32), nullable=False)
    note = Column(Text)
    source = Column(Text)
    source_url = Column(Text)
    as_of = Column(Text)
    verified = Column(String(8))
    __table_args__ = (UniqueConstraint("substance_id", "jurisdiction"),)

class Product(Base):
    __tablename__ = "products"
    barcode = Column(String(14), primary_key=True)
    product_name = Column(Text)
    brand = Column(Text)
    ingredients_text = Column(Text)
    additive_codes = Column(Text)
    category = Column(String(16))
    image_url = Column(Text)
    categories_tags = Column(Text)
    source = Column(Text)
    fetched_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

REFERENCE_TABLES = [("food_categories.csv", FoodCategory, "code"), ("substances.csv", Substance, "substance_id")]

MIGRATIONS = [
    "ALTER TABLE recalls ADD COLUMN IF NOT EXISTS source VARCHAR(16) NOT NULL DEFAULT 'FSSAI'",
    "ALTER TABLE recalls ADD COLUMN IF NOT EXISTS first_seen_at TIMESTAMP DEFAULT now()",
    "ALTER TABLE purchases ADD COLUMN IF NOT EXISTS user_id VARCHAR(64) REFERENCES users(user_id) ON DELETE CASCADE",
    "ALTER TABLE purchases ADD COLUMN IF NOT EXISTS source VARCHAR(16)",
    "ALTER TABLE purchases ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT now()",
    "ALTER TABLE matches ADD COLUMN IF NOT EXISTS status VARCHAR(16) NOT NULL DEFAULT 'pending_review'",
    "ALTER TABLE matches ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMP",
    "ALTER TABLE matches ADD COLUMN IF NOT EXISTS reviewed_by TEXT",
    "ALTER TABLE matches ADD COLUMN IF NOT EXISTS review_note TEXT",
    "ALTER TABLE purchases ADD COLUMN IF NOT EXISTS barcode VARCHAR(14)",
    "ALTER TABLE purchases ADD COLUMN IF NOT EXISTS ingredients_text TEXT",
    "ALTER TABLE purchases ADD COLUMN IF NOT EXISTS status VARCHAR(16) NOT NULL DEFAULT 'active'",
    "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS feedback VARCHAR(16)",
    "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS feedback_at TIMESTAMP",
    "ALTER TABLE products ADD COLUMN IF NOT EXISTS categories_tags TEXT",
]

INDEXES = [
    "CREATE EXTENSION IF NOT EXISTS pg_trgm",
    "CREATE INDEX IF NOT EXISTS recalls_product_trgm ON recalls USING gin (product_norm gin_trgm_ops)",
    "CREATE INDEX IF NOT EXISTS recalls_brand_trgm ON recalls USING gin (brand_norm gin_trgm_ops)",
    "CREATE INDEX IF NOT EXISTS recalls_search_trgm ON recalls USING gin (search_norm gin_trgm_ops)",
    "CREATE INDEX IF NOT EXISTS recalls_date ON recalls (recall_date)",
    "CREATE INDEX IF NOT EXISTS matches_status ON matches (status)",
    "CREATE INDEX IF NOT EXISTS purchases_user ON purchases (user_id)",
    "CREATE INDEX IF NOT EXISTS alerts_status ON alerts (status)",
]

CANDIDATE_SQL = text("""
    SELECT recall_id, word_similarity(:query, search_norm) AS sim
    FROM recalls
    WHERE :query <% search_norm
    ORDER BY sim DESC
    LIMIT :limit
""")
WORD_SIMILARITY_THRESHOLD = 0.3

SAVE_MATCH_SQL = text("""
    INSERT INTO matches (purchase_id, recall_id, score, product_score, brand_score, batch_score, notes, status)
    VALUES (:purchase_id, :recall_id, :score, :product_score, :brand_score, :batch_score, :notes, :status)
    ON CONFLICT (purchase_id, recall_id) DO UPDATE SET
        score = EXCLUDED.score, product_score = EXCLUDED.product_score, brand_score = EXCLUDED.brand_score,
        batch_score = EXCLUDED.batch_score, notes = EXCLUDED.notes,
        status = CASE
            WHEN matches.status IN ('approved', 'rejected') THEN matches.status
            WHEN EXISTS (SELECT 1 FROM alerts a WHERE a.match_id = matches.id) THEN matches.status
            ELSE EXCLUDED.status END
""")

REVIEW_QUEUE_SQL = text("""
    SELECT m.id AS match_id, m.score, m.product_score, m.brand_score, m.batch_score, m.notes, m.created_at,
           p.purchase_id, p.user_id, p.product_name AS purchase_product, p.brand AS purchase_brand,
           p.batch_no AS purchase_batch, p.store AS purchase_store, p.purchase_date, p.source AS purchase_source,
           r.recall_id, r.product_name AS recall_product, r.brand AS recall_brand, r.batch_no AS recall_batch,
           r.fbo_name AS recall_fbo, r.reason AS recall_reason, r.recall_date, r.source AS recall_source
    FROM matches m
    JOIN purchases p ON p.purchase_id = m.purchase_id
    JOIN recalls r ON r.recall_id = m.recall_id
    WHERE m.status = 'pending_review'
    ORDER BY m.score DESC, m.id
    LIMIT :limit OFFSET :offset
""")

DECIDE_SQL = text("""
    UPDATE matches SET status = :status, reviewed_at = now(), reviewed_by = :reviewer, review_note = :note
    WHERE id = :match_id AND status = 'pending_review'
    RETURNING id
""")

# Visible matches (auto-published or approved) that have no alert yet, for users who want alerts.
ALERTABLE_SQL = text("""
    SELECT m.id AS match_id, m.score, m.status, p.purchase_id, p.user_id, p.product_name AS purchase_product,
           p.brand AS purchase_brand, p.batch_no AS purchase_batch, p.store AS purchase_store, p.purchase_date,
           r.recall_id, r.product_name AS recall_product, r.brand AS recall_brand, r.batch_no AS recall_batch,
           r.fbo_name AS recall_fbo, r.reason AS recall_reason, r.recall_type, r.hazard, r.recall_date,
           r.status AS recall_status, r.source AS recall_source, u.email, u.name AS user_name
    FROM matches m
    JOIN purchases p ON p.purchase_id = m.purchase_id
    JOIN recalls r ON r.recall_id = m.recall_id
    LEFT JOIN users u ON u.user_id = p.user_id
    WHERE m.status IN ('auto_published', 'approved')
      AND NOT EXISTS (SELECT 1 FROM alerts a WHERE a.match_id = m.id)
      AND COALESCE(u.alerts_enabled, true)
    ORDER BY m.id
""")

INSERT_ALERT_SQL = text("""
    INSERT INTO alerts (match_id, user_id, channel, status, subject, body)
    VALUES (:match_id, :user_id, :channel, 'queued', :subject, :body)
    ON CONFLICT (match_id) DO NOTHING
""")

QUEUED_ALERTS_SQL = text("""
    SELECT a.id, a.match_id, a.user_id, a.channel, a.subject, a.body, u.email
    FROM alerts a LEFT JOIN users u ON u.user_id = a.user_id
    WHERE a.status = 'queued' ORDER BY a.id
""")

MARK_ALERT_SQL = text("""
    UPDATE alerts SET status = :status, error = :error,
           sent_at = CASE WHEN :status = 'sent' THEN now() ELSE sent_at END
    WHERE id = :id
""")

NEW_RECALLS_SQL = text("""
    SELECT count(*) FROM recalls
    WHERE first_seen_at > COALESCE((SELECT max(finished_at) FROM runs), 'epoch')
""")


def get_engine(url=None):
    return create_engine(url or DATABASE_URL, future=True, pool_pre_ping=True)


def init_db(engine):
    with engine.begin() as conn:
        conn.execute(text(INDEXES[0]))
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        for stmt in MIGRATIONS + INDEXES[1:]:
            conn.execute(text(stmt))
        conn.execute(text("SELECT set_limit(0.2)"))
        conn.execute(text("INSERT INTO users (user_id, name) VALUES (:u, 'Demo user') ON CONFLICT DO NOTHING"),
                     {"u": DEFAULT_USER_ID})
        # Upgrading an older database: purchases without an owner go to the demo user, and matches saved before
        # the review queue existed get the status their score earns.
        conn.execute(text("UPDATE purchases SET user_id = :u WHERE user_id IS NULL"), {"u": DEFAULT_USER_ID})
        conn.execute(text("UPDATE matches SET status = 'auto_published' WHERE status = 'pending_review' "
                          "AND reviewed_at IS NULL AND score >= :auto"), {"auto": AUTO_PUBLISH_SCORE})


def _clean_value(v):
    if v is None or v is pd.NaT:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    return v


def _records(df, columns):
    return [{k: _clean_value(v) for k, v in rec.items()} for rec in df[columns].to_dict("records")]


def upsert(engine, model, df, key):
    cols = [c.name for c in model.__table__.columns if c.name in df.columns]
    rows = _records(df, cols)
    if not rows:
        return 0
    stmt = insert(model.__table__).values(rows)
    update = {c: stmt.excluded[c] for c in cols if c != key}
    stmt = stmt.on_conflict_do_update(index_elements=[key], set_=update) if update else \
        stmt.on_conflict_do_nothing(index_elements=[key])
    with engine.begin() as conn:
        conn.execute(stmt)
    return len(rows)


def ensure_users(engine, user_ids):
    ids = sorted({u for u in user_ids if u})
    if ids:
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO users (user_id) VALUES (:u) ON CONFLICT DO NOTHING"),
                         [{"u": u} for u in ids])
    return ids


def save_purchases(engine, purchases, user_id=None, source=None):
    df = purchases.copy()
    if "user_id" not in df.columns or df["user_id"].isna().all():
        df["user_id"] = user_id or DEFAULT_USER_ID
    df["user_id"] = df["user_id"].fillna(user_id or DEFAULT_USER_ID)
    if source and ("source" not in df.columns or df["source"].isna().all()):
        df["source"] = source
    ensure_users(engine, df["user_id"])
    return upsert(engine, Purchase, df, "purchase_id")


def load_reference(engine, directory):
    from pathlib import Path
    d = Path(directory)
    counts = {}
    for name, model, key in REFERENCE_TABLES:
        counts[model.__tablename__] = upsert(engine, model, pd.read_csv(d / name, dtype=str, keep_default_na=False), key)
    for name, model in (("rules.csv", Rule), ("global_status.csv", GlobalStatus)):
        path = d / name
        if not path.exists():
            continue
        frame = pd.read_csv(path, dtype=str, keep_default_na=False)
        with engine.begin() as conn:
            conn.execute(model.__table__.delete())
            if len(frame):
                conn.execute(model.__table__.insert(), frame.to_dict("records"))
        counts[model.__tablename__] = len(frame)
    return counts


def load_rulebook(engine):
    from .rulebook import RuleBook
    with engine.connect() as conn:
        frames = [pd.read_sql(select(m.__table__), conn).fillna("").astype(str) for m in (Substance, Rule, FoodCategory)]
    return RuleBook(*frames)


def load_global_status(engine):
    with engine.connect() as conn:
        return pd.read_sql(select(GlobalStatus.__table__), conn).fillna("").astype(str)


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


MATCH_COLUMNS = ["purchase_id", "recall_id", "score", "product_score", "brand_score", "batch_score", "notes", "status"]


def save_matches(engine, matches):
    if matches.empty:
        return 0
    if "status" not in matches.columns:
        from .review import assign_status
        matches = assign_status(matches)
    rows = _records(matches, MATCH_COLUMNS)
    with engine.begin() as conn:
        conn.execute(SAVE_MATCH_SQL, rows)
    return len(rows)


def match_from_db(engine, floor=None, purchase_ids=None):
    """Score every purchase (or just `purchase_ids`) against its trigram candidates and band the results."""
    from .config import REVIEW_MIN_SCORE
    from .matching import score_pair
    from .review import band
    floor = REVIEW_MIN_SCORE if floor is None else floor
    recalls = read_table(engine, Recall).set_index("recall_id", drop=False)
    purchases = read_table(engine, Purchase)
    if purchase_ids is not None:
        purchases = purchases[purchases["purchase_id"].isin(set(purchase_ids))]
    rows = []
    for p in purchases.to_dict("records"):
        for c in candidates(engine, p["product_norm"], p["brand_norm"]):
            r = recalls.loc[c["recall_id"]].to_dict()
            score, parts, reasons = score_pair(r, p)
            status = band(score, floor=floor)
            if status:
                rows.append({"purchase_id": p["purchase_id"], "recall_id": r["recall_id"], "score": score,
                             "product_score": parts.get("product"), "brand_score": parts.get("brand"),
                             "batch_score": parts.get("batch"), "notes": "; ".join(reasons), "status": status})
    return pd.DataFrame(rows, columns=MATCH_COLUMNS)


def review_queue(engine, limit=50, offset=0):
    with engine.connect() as conn:
        return [dict(r) for r in conn.execute(REVIEW_QUEUE_SQL, {"limit": limit, "offset": offset}).mappings()]


def decide(engine, match_id, approve, reviewer=None, note=None):
    """Approve or reject a pending match. Returns False if it is not (or no longer) pending."""
    from .review import APPROVED, REJECTED
    with engine.begin() as conn:
        row = conn.execute(DECIDE_SQL, {"status": APPROVED if approve else REJECTED, "reviewer": reviewer,
                                        "note": note, "match_id": int(match_id)}).first()
    return row is not None


def alertable(engine):
    with engine.connect() as conn:
        return [dict(r) for r in conn.execute(ALERTABLE_SQL).mappings()]


def insert_alerts(engine, alerts):
    if not alerts:
        return 0
    with engine.begin() as conn:
        conn.execute(INSERT_ALERT_SQL, alerts)
    return len(alerts)


def queued_alerts(engine):
    with engine.connect() as conn:
        return [dict(r) for r in conn.execute(QUEUED_ALERTS_SQL).mappings()]


def mark_alert(engine, alert_id, status, error=None):
    with engine.begin() as conn:
        conn.execute(MARK_ALERT_SQL, {"id": alert_id, "status": status, "error": error})


def new_recall_count(engine):
    with engine.connect() as conn:
        return conn.execute(NEW_RECALLS_SQL).scalar()


def start_run(engine):
    with engine.begin() as conn:
        return conn.execute(text("INSERT INTO runs DEFAULT VALUES RETURNING id")).scalar()


def finish_run(engine, run_id, **counts):
    cols = ["new_recalls", "auto_published", "pending_review", "alerts_queued", "alerts_sent"]
    with engine.begin() as conn:
        conn.execute(text(f"UPDATE runs SET finished_at = now(), {', '.join(f'{c} = :{c}' for c in cols)} "
                          "WHERE id = :id"), {"id": run_id, **{c: counts.get(c) for c in cols}})


# ---- queries for the app ----

VISIBLE_SQL = "('auto_published', 'approved')"

USER_PURCHASES_SQL = text(f"""
    SELECT p.purchase_id, p.product_name, p.brand, p.batch_no, p.store, p.purchase_date, p.source, p.status,
           p.barcode, p.created_at,
           COALESCE(json_agg(json_build_object(
               'recall_id', r.recall_id, 'brand', r.brand, 'product', r.product_name, 'batch', r.batch_no,
               'score', m.score, 'match_status', m.status, 'alert_id', a.id, 'feedback', a.feedback)
               ORDER BY m.score DESC) FILTER (WHERE m.id IS NOT NULL), '[]') AS recalls
    FROM purchases p
    LEFT JOIN matches m ON m.purchase_id = p.purchase_id AND m.status IN {VISIBLE_SQL}
    LEFT JOIN recalls r ON r.recall_id = m.recall_id
    LEFT JOIN alerts a ON a.match_id = m.id
    WHERE p.user_id = :u
    GROUP BY p.purchase_id
    ORDER BY p.purchase_date DESC NULLS LAST, p.created_at DESC, p.purchase_id
""")

AFFECTED_SQL = text(f"""
    SELECT DISTINCT p.purchase_id, p.product_name
    FROM purchases p
    JOIN matches m ON m.purchase_id = p.purchase_id AND m.status IN {VISIBLE_SQL}
    LEFT JOIN alerts a ON a.match_id = m.id
    WHERE p.user_id = :u AND p.status = 'active' AND COALESCE(a.feedback, '') <> 'not_my_batch'
    ORDER BY p.product_name
""")

SUMMARY_SQL = text("""
    SELECT (SELECT count(*) FROM purchases WHERE user_id = :u AND status = 'active') AS watched,
           (SELECT count(*) FROM recalls) AS recalls,
           (SELECT count(*) FROM alerts WHERE user_id = :u AND feedback IS NULL) AS open_alerts,
           (SELECT max(finished_at) FROM runs) AS last_checked
""")

LATEST_RECALLS_SQL = text("""
    SELECT recall_id, brand, product_name, batch_no, reason, recall_type, recall_date, source
    FROM recalls ORDER BY recall_date DESC NULLS LAST, recall_id DESC LIMIT :n
""")

USER_ALERTS_SQL = text("""
    SELECT a.id, a.match_id, a.status, a.feedback, a.created_at, a.sent_at, m.score, m.status AS match_status,
           p.purchase_id, p.product_name AS purchase_product, p.brand AS purchase_brand, p.batch_no AS purchase_batch,
           p.store AS purchase_store, p.purchase_date,
           r.recall_id, r.product_name AS recall_product, r.brand AS recall_brand, r.batch_no AS recall_batch,
           r.fbo_name AS recall_fbo, r.reason AS recall_reason, r.recall_type, r.recall_date,
           r.status AS recall_status, r.source AS recall_source
    FROM alerts a
    JOIN matches m ON m.id = a.match_id
    JOIN purchases p ON p.purchase_id = m.purchase_id
    JOIN recalls r ON r.recall_id = m.recall_id
    WHERE a.user_id = :u
    ORDER BY (a.feedback IS NOT NULL), a.created_at DESC, a.id DESC
""")

FEEDBACK_SQL = text("""
    UPDATE alerts SET feedback = :f, feedback_at = now()
    WHERE id = :id AND user_id = :u
    RETURNING id
""")

# Editing a purchase resets the matcher's own decisions for it (a new batch number can rule a recall out),
# but keeps anything a reviewer decided.
RESET_MATCHES_SQL = text("DELETE FROM matches WHERE purchase_id = :p AND reviewed_at IS NULL")
DELETE_PURCHASE_SQL = text("DELETE FROM purchases WHERE purchase_id = :p AND user_id = :u RETURNING purchase_id")


SAVE_PUSH_SQL = text("""
    INSERT INTO push_subscriptions (user_id, endpoint, p256dh, auth) VALUES (:u, :e, :p, :a)
    ON CONFLICT (endpoint) DO UPDATE SET user_id = EXCLUDED.user_id, p256dh = EXCLUDED.p256dh, auth = EXCLUDED.auth
""")
PUSH_SUBS_SQL = text("SELECT endpoint, p256dh, auth FROM push_subscriptions WHERE user_id = :u")
DELETE_PUSH_SQL = text("DELETE FROM push_subscriptions WHERE user_id = :u AND endpoint = :e")


def save_push_subscription(engine, user_id, endpoint, p256dh, auth):
    with engine.begin() as conn:
        conn.execute(SAVE_PUSH_SQL, {"u": user_id, "e": endpoint, "p": p256dh, "a": auth})


def push_subscriptions(engine, user_id):
    return _rows(engine, PUSH_SUBS_SQL, u=user_id)


def delete_push_subscription(engine, user_id, endpoint):
    with engine.begin() as conn:
        conn.execute(DELETE_PUSH_SQL, {"u": user_id, "e": endpoint})


def _rows(engine, sql, **params):
    with engine.connect() as conn:
        return [dict(r) for r in conn.execute(sql, params).mappings()]


def user_purchases(engine, user_id):
    return _rows(engine, USER_PURCHASES_SQL, u=user_id)


def user_summary(engine, user_id, latest=5):
    s = _rows(engine, SUMMARY_SQL, u=user_id)[0]
    s["affected"] = _rows(engine, AFFECTED_SQL, u=user_id)
    s["latest_recalls"] = _rows(engine, LATEST_RECALLS_SQL, n=latest)
    return s


def user_alerts(engine, user_id):
    return _rows(engine, USER_ALERTS_SQL, u=user_id)


def set_feedback(engine, user_id, alert_id, feedback):
    with engine.begin() as conn:
        return conn.execute(FEEDBACK_SQL, {"f": feedback, "id": int(alert_id), "u": user_id}).first() is not None


PURCHASE_EDITABLE = ("product_name", "brand", "batch_no", "store", "status")


def update_purchase(engine, user_id, purchase_id, changes):
    """Apply `changes` to one of the user's purchases. Returns True if the matcher should run again for it."""
    from .clean import normalize_name
    fields = {k: v for k, v in changes.items() if k in PURCHASE_EDITABLE}
    if not fields:
        return False
    if "batch_no" in fields and fields["batch_no"]:
        fields["batch_no"] = str(fields["batch_no"]).strip().upper()
    if "product_name" in fields:
        fields["product_norm"] = normalize_name(fields["product_name"])
    if "brand" in fields:
        fields["brand_norm"] = normalize_name(fields["brand"])
    sets = ", ".join(f"{k} = :{k}" for k in fields)
    with engine.begin() as conn:
        found = conn.execute(text(f"UPDATE purchases SET {sets} WHERE purchase_id = :pid AND user_id = :uid "
                                  "RETURNING purchase_id"), {**fields, "pid": purchase_id, "uid": user_id}).first()
        if not found:
            raise KeyError(purchase_id)
        rematch = bool({"product_name", "brand", "batch_no"} & set(fields))
        if rematch:
            conn.execute(RESET_MATCHES_SQL, {"p": purchase_id})
    return rematch


def delete_purchase(engine, user_id, purchase_id):
    with engine.begin() as conn:
        return conn.execute(DELETE_PURCHASE_SQL, {"p": purchase_id, "u": user_id}).first() is not None


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
