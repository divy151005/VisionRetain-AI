"""
VisionRetain AI — Python ML Microservice
Stack: FastAPI · XGBoost · scikit-learn · pandas · Redis · Uvicorn
Provides: /predict/churn, /predict/demand, /predict/ltv, /segment/customers

Run:
    pip install fastapi uvicorn xgboost scikit-learn pandas numpy redis joblib
    uvicorn main:app --host 0.0.0.0 --port 8001 --workers 4
"""

from fastapi import FastAPI, HTTPException, BackgroundTasks, Depends, Header, UploadFile, File

from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
import numpy as np
import pandas as pd
import xgboost as xgb

from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.cluster import KMeans
from sklearn.model_selection import train_test_split
from sklearn.metrics import classification_report, roc_auc_score
import redis
import json
import joblib
import logging
import os
from datetime import datetime, timedelta
import uvicorn
import jwt
import requests
import base64
import hashlib
import re

# ── Setup ──────────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

logger = logging.getLogger("visionretain-ml")

app = FastAPI(
    title="VisionRetain AI — ML Microservice",
    description="XGBoost churn prediction, demand forecasting, and customer segmentation",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Supabase Auth (JWT verification) ──────────────────────────────────────
SUPABASE_JWKS_URL = os.getenv("SUPABASE_JWKS_URL")
SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_SECRET_KEY = os.getenv("SUPABASE_SECRET_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_VISION_MODEL = os.getenv("GEMINI_VISION_MODEL", "gemini-2.5-flash")
SERPAPI_API_KEY = os.getenv("SERPAPI_API_KEY", "")
PRICE_COUNTRY = os.getenv("PRICE_COUNTRY", "in")
PRICE_LANGUAGE = os.getenv("PRICE_LANGUAGE", "en")
MAX_IMAGE_BYTES = int(os.getenv("MAX_IMAGE_BYTES", str(10 * 1024 * 1024)))

_JWKS_CACHE: Dict[str, Any] = {"keys": None, "fetched_at": None}


def _fetch_jwks() -> Dict[str, Any]:
    global _JWKS_CACHE
    if not SUPABASE_JWKS_URL:
        raise RuntimeError("SUPABASE_JWKS_URL is not set")

    # Refresh JWKS periodically (best-effort)
    if _JWKS_CACHE.get("keys") is not None and _JWKS_CACHE.get("fetched_at"):
        try:
            fetched_at = _JWKS_CACHE["fetched_at"]
            if isinstance(fetched_at, str):
                fetched_at_dt = datetime.fromisoformat(fetched_at)
            else:
                fetched_at_dt = fetched_at
            if datetime.utcnow() - fetched_at_dt < timedelta(hours=6):
                return _JWKS_CACHE["keys"]
        except Exception:
            pass

    resp = requests.get(SUPABASE_JWKS_URL, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    _JWKS_CACHE = {"keys": data, "fetched_at": datetime.utcnow().isoformat()}
    return data


def _jwt_key_for_kid(kid: str) -> Dict[str, Any]:
    jwks = _fetch_jwks()
    for k in jwks.get("keys", []):
        if k.get("kid") == kid:
            return k
    raise HTTPException(status_code=401, detail="Invalid token (unknown kid)")


def verify_supabase_jwt(authorization: str = Header(None)) -> Dict[str, Any]:
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Invalid Authorization header")

    token = authorization.split(" ", 1)[1].strip()
    try:
        header = jwt.get_unverified_header(token)
        kid = header.get("kid")
        if not kid:
            raise HTTPException(status_code=401, detail="Invalid token header")

        jwk = _jwt_key_for_kid(kid)

        public_key = jwt.PyJWK.from_dict(jwk).key

        decoded = jwt.decode(
            token,
            key=public_key,
            algorithms=[header.get("alg") or "RS256"],
            options={"verify_aud": False},
        )
        return decoded
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {e}")


def optional_supabase_jwt(authorization: str = Header(None)) -> Dict[str, Any]:
    if not authorization:
        return {}
    return verify_supabase_jwt(authorization)


def _supabase_headers() -> Dict[str, str]:
    if not SUPABASE_URL or not SUPABASE_SECRET_KEY:
        raise RuntimeError("Supabase backend credentials are not configured")
    return {
        "apikey": SUPABASE_SECRET_KEY,
        "Authorization": f"Bearer {SUPABASE_SECRET_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }


def supabase_insert(table: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not SUPABASE_URL or not SUPABASE_SECRET_KEY:
        logger.warning("Supabase persistence skipped: backend credentials are not configured")
        return None
    response = requests.post(
        f"{SUPABASE_URL}/rest/v1/{table}",
        headers=_supabase_headers(),
        json=payload,
        timeout=15,
    )
    if not response.ok:
        logger.error("Supabase insert into %s failed: %s", table, response.text)
        return None
    rows = response.json()
    return rows[0] if rows else None


def supabase_select(table: str, select: str = "*", order: Optional[str] = None) -> List[Dict[str, Any]]:
    if not SUPABASE_URL or not SUPABASE_SECRET_KEY:
        return []
    params = {"select": select}
    if order:
        params["order"] = order
    response = requests.get(
        f"{SUPABASE_URL}/rest/v1/{table}",
        headers=_supabase_headers(),
        params=params,
        timeout=15,
    )
    if not response.ok:
        logger.error("Supabase select from %s failed: %s", table, response.text)
        return []
    return response.json()


def supabase_recent_scans(owner_id: Optional[str], limit: int = 10) -> List[Dict[str, Any]]:
    if not owner_id or not SUPABASE_URL or not SUPABASE_SECRET_KEY:
        return []
    params = {
        "select": "id,product_name,brand,model,category,confidence,identity_status,"
                  "objects,price_min,price_max,currency,prices_fetched_at,created_at",
        "order": "created_at.desc",
        "limit": str(max(1, min(limit, 50))),
    }
    params["owner_id"] = f"eq.{owner_id}"
    response = requests.get(
        f"{SUPABASE_URL}/rest/v1/product_scans",
        headers=_supabase_headers(),
        params=params,
        timeout=15,
    )
    if not response.ok:
        logger.error("Supabase recent scans failed: %s", response.text)
        return []
    return response.json()


# Redis for caching predictions
redis_client = redis.Redis(
    host=os.getenv("REDIS_HOST", "localhost"),
    port=6379,
    password=os.getenv("REDIS_PASSWORD", ""),
    decode_responses=True,
    socket_connect_timeout=0.2,
    socket_timeout=0.2,
)

def cache_get(key: str):
    try:
        return redis_client.get(key)
    except redis.RedisError as exc:
        logger.warning("Redis unavailable; skipping cache read: %s", exc)
        return None

def cache_setex(key: str, ttl: int, value: str):
    try:
        redis_client.setex(key, ttl, value)
    except redis.RedisError as exc:
        logger.warning("Redis unavailable; skipping cache write: %s", exc)

def to_jsonable(value):
    if isinstance(value, dict):
        return {key: to_jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [to_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [to_jsonable(item) for item in value]
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    return value

# ── Pydantic Models ────────────────────────────────────────────────────────────
class CustomerFeatures(BaseModel):
    customer_id: str
    engagement_score: float = Field(ge=0, le=1)
    days_since_active: int = Field(ge=0)
    support_tickets: int = Field(ge=0)
    tenure_months: int = Field(ge=0)
    monthly_spend: float = Field(ge=0)
    nps_score: int = Field(ge=-100, le=100)
    feature_adoption_count: int = Field(ge=0)
    plan: str  # STARTER | PRO | BUSINESS | ENTERPRISE
    purchase_count_30d: int = Field(ge=0, default=0)
    purchase_count_90d: int = Field(ge=0, default=0)
    avg_session_duration_mins: float = Field(ge=0, default=0)
    login_frequency_per_week: float = Field(ge=0, default=0)

class BatchPredictRequest(BaseModel):
    customers: List[CustomerFeatures]

class DemandForecastRequest(BaseModel):
    product_id: str
    historical_sales: List[float]  # Monthly sales, oldest first
    seasonality_index: Optional[float] = 1.0
    price_elasticity: Optional[float] = -0.8
    horizon_days: int = Field(default=30, ge=7, le=90)

class SegmentRequest(BaseModel):
    customers: List[Dict[str, Any]]
    n_segments: int = Field(default=4, ge=2, le=10)


class DetectedObject(BaseModel):
    label: str
    confidence: float = Field(ge=0, le=100)


class ProductIdentification(BaseModel):
    product_name: str
    brand: Optional[str] = None
    model: Optional[str] = None
    category: str
    confidence: float = Field(ge=0, le=100)
    identity_status: str
    features: List[str] = Field(default_factory=list)
    ocr_text: Optional[str] = None
    objects: List[DetectedObject] = Field(default_factory=list)
    shopping_query: str


def _extract_json_object(text: str) -> Dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("Vision provider did not return a JSON object")
    return json.loads(cleaned[start:end + 1])


def identify_product(image_bytes: bytes, mime_type: str) -> ProductIdentification:
    if not GEMINI_API_KEY:
        raise HTTPException(
            status_code=503,
            detail="Product recognition is not configured. Set GEMINI_API_KEY on the backend.",
        )
    prompt = """
Analyze this image for product intelligence. Detect every clearly visible physical object,
then identify the primary retail product as precisely as visual evidence permits.
Never guess a brand, model, variant, capacity, or generation. OCR visible labels and model
numbers. Use identity_status exact only when a unique model/SKU is visible or unmistakable,
probable when brand and product line are strongly supported, category_only when only the
generic object type is supported, and unknown when the image is unusable.
Confidence is an evidence-quality estimate from 0 to 100, not a guarantee.
Return JSON only with:
product_name, brand, model, category, confidence, identity_status,
features (array), ocr_text, objects (array of {label, confidence}), shopping_query.
shopping_query must include exact brand/model only when supported; otherwise use the
specific generic category and visible attributes. Do not include prices.
""".strip()
    response = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_VISION_MODEL}:generateContent",
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": GEMINI_API_KEY,
        },
        json={
            "contents": [{
                "parts": [
                    {"text": prompt},
                    {"inline_data": {
                        "mime_type": mime_type,
                        "data": base64.b64encode(image_bytes).decode("ascii"),
                    }},
                ]
            }],
            "generationConfig": {
                "temperature": 0.1,
                "responseMimeType": "application/json",
            },
        },
        timeout=60,
    )
    if not response.ok:
        logger.error("Gemini recognition failed: %s", response.text)
        raise HTTPException(status_code=502, detail="Vision provider could not analyze this image")
    try:
        text = response.json()["candidates"][0]["content"]["parts"][0]["text"]
        return ProductIdentification.model_validate(_extract_json_object(text))
    except Exception as exc:
        logger.exception("Invalid vision response")
        raise HTTPException(status_code=502, detail=f"Vision provider returned invalid data: {exc}")


def _title_relevance(title: str, identification: ProductIdentification) -> float:
    ignored = {"the", "with", "for", "and", "new", "buy", "online", "best"}
    query = " ".join(filter(None, [
        identification.brand,
        identification.model,
        identification.product_name,
    ])).lower()
    wanted = {
        token for token in re.findall(r"[a-z0-9]+", query)
        if len(token) > 1 and token not in ignored
    }
    found = set(re.findall(r"[a-z0-9]+", title.lower()))
    if not wanted:
        return 0
    score = len(wanted & found) / len(wanted)
    if identification.model:
        model_tokens = set(re.findall(r"[a-z0-9]+", identification.model.lower()))
        if model_tokens and not model_tokens.issubset(found):
            score *= 0.45
    return round(score, 4)


def fetch_live_prices(identification: ProductIdentification) -> Dict[str, Any]:
    fetched_at = datetime.utcnow().isoformat() + "Z"
    if not SERPAPI_API_KEY:
        return {
            "status": "unconfigured",
            "message": "Set SERPAPI_API_KEY to retrieve current shopping listings.",
            "fetched_at": None,
            "listings": [],
        }
    response = requests.get(
        "https://serpapi.com/search.json",
        params={
            "engine": "google_shopping",
            "q": identification.shopping_query,
            "api_key": SERPAPI_API_KEY,
            "gl": PRICE_COUNTRY,
            "hl": PRICE_LANGUAGE,
            "num": 20,
        },
        timeout=30,
    )
    if not response.ok:
        logger.error("Shopping search failed: %s", response.text)
        return {
            "status": "provider_error",
            "message": "The live shopping provider is temporarily unavailable.",
            "fetched_at": fetched_at,
            "listings": [],
        }
    payload = response.json()
    listings = []
    for item in payload.get("shopping_results", []):
        price = item.get("extracted_price")
        title = item.get("title", "")
        link = item.get("product_link") or item.get("link")
        if not isinstance(price, (int, float)) or not title or not link:
            continue
        relevance = _title_relevance(title, identification)
        minimum = 0.62 if identification.identity_status in ("exact", "probable") else 0.35
        if relevance < minimum:
            continue
        listings.append({
            "store": item.get("source") or "Unknown store",
            "title": title,
            "price": float(price),
            "currency": "INR" if PRICE_COUNTRY.lower() == "in" else item.get("currency", "USD"),
            "product_url": link,
            "source_url": payload.get("search_metadata", {}).get("google_shopping_url"),
            "availability": item.get("delivery") or "Check retailer",
            "rating": item.get("rating"),
            "reviews": item.get("reviews"),
            "relevance": relevance,
            "fetched_at": fetched_at,
        })
    listings.sort(key=lambda row: row["price"])
    for index, listing in enumerate(listings):
        listing["badge"] = "Best current price" if index == 0 else None
    return {
        "status": "live" if listings else "no_verified_matches",
        "message": None if listings else "No sufficiently relevant current listings were found.",
        "fetched_at": fetched_at,
        "listings": listings[:12],
    }


def persist_product_scan(
    owner_id: Optional[str],
    image_bytes: bytes,
    identification: ProductIdentification,
    pricing: Dict[str, Any],
) -> Optional[str]:
    listings = pricing.get("listings", [])
    prices = [item["price"] for item in listings]
    scan = supabase_insert("product_scans", {
        "owner_id": owner_id,
        "image_sha256": hashlib.sha256(image_bytes).hexdigest(),
        "product_name": identification.product_name,
        "brand": identification.brand,
        "model": identification.model,
        "category": identification.category,
        "confidence": identification.confidence,
        "identity_status": identification.identity_status,
        "objects": [item.model_dump() for item in identification.objects],
        "features": identification.features,
        "ocr_text": identification.ocr_text,
        "price_min": min(prices) if prices else None,
        "price_max": max(prices) if prices else None,
        "currency": listings[0]["currency"] if listings else None,
        "prices_fetched_at": pricing.get("fetched_at"),
        "provider_metadata": {
            "vision_model": GEMINI_VISION_MODEL,
            "price_provider": "SerpApi Google Shopping" if SERPAPI_API_KEY else None,
            "price_status": pricing.get("status"),
        },
    })
    if not scan:
        return None
    for listing in listings:
        supabase_insert("price_listings", {
            "scan_id": scan["id"],
            "owner_id": owner_id,
            "store": listing["store"],
            "title": listing["title"],
            "price": listing["price"],
            "currency": listing["currency"],
            "product_url": listing["product_url"],
            "source_url": listing.get("source_url"),
            "availability": listing.get("availability"),
            "rating": listing.get("rating"),
            "relevance": listing.get("relevance"),
            "fetched_at": listing["fetched_at"],
        })
    return scan["id"]

# ── Model Manager ──────────────────────────────────────────────────────────────
class ModelManager:
    """Manages loading, training, and inference for all ML models."""
    
    def __init__(self):
        self.xgb_model: Optional[xgb.XGBClassifier] = None
        self.rf_model: Optional[RandomForestClassifier] = None
        self.lr_model: Optional[LogisticRegression] = None
        self.scaler: Optional[StandardScaler] = None
        self.is_trained = False
        
    def _generate_synthetic_training_data(self, n_samples: int = 5000) -> pd.DataFrame:
        """
        Generate realistic synthetic training data for demo/development.
        In production: replace with real customer data from MongoDB.
        """
        np.random.seed(42)
        
        # Churn-correlated features
        engagement = np.random.beta(2, 2, n_samples)
        days_inactive = np.random.exponential(15, n_samples).astype(int)
        support_tickets = np.random.poisson(2, n_samples)
        tenure = np.random.exponential(12, n_samples).astype(int) + 1
        spend = np.random.lognormal(9, 1.2, n_samples)
        nps = np.random.normal(45, 25, n_samples).clip(-100, 100)
        feature_adoption = np.random.randint(0, 20, n_samples)
        logins = np.random.exponential(3, n_samples)
        plan_enc = np.random.choice([0, 1, 2, 3], n_samples, p=[0.3, 0.3, 0.25, 0.15])
        
        # Churn probability (ground truth formula)
        churn_logit = (
            -2.5
            + (1 - engagement) * 3.5
            + np.log1p(days_inactive) * 0.4
            + support_tickets * 0.25
            - np.log1p(tenure) * 0.5
            - np.log1p(spend / 1000) * 0.3
            + (nps < 0).astype(float) * 1.2
            - feature_adoption * 0.08
            - logins * 0.15
        )
        churn_prob = 1 / (1 + np.exp(-churn_logit))
        churn = (np.random.random(n_samples) < churn_prob).astype(int)
        
        return pd.DataFrame({
            "engagement_score": engagement,
            "days_since_active": days_inactive,
            "support_tickets": support_tickets,
            "tenure_months": tenure,
            "monthly_spend": spend,
            "nps_score": nps,
            "feature_adoption_count": feature_adoption,
            "login_frequency": logins,
            "plan_encoded": plan_enc,
            "churn": churn,
        })
    
    def train(self):
        """Train all three models and ensemble."""
        logger.info("Training ML models on synthetic data...")
        df = self._generate_synthetic_training_data(5000)
        
        feature_cols = [
            "engagement_score", "days_since_active", "support_tickets",
            "tenure_months", "monthly_spend", "nps_score",
            "feature_adoption_count", "login_frequency", "plan_encoded"
        ]
        X = df[feature_cols].values
        y = df["churn"].values
        
        X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42, stratify=y)
        
        # Scale for LR
        self.scaler = StandardScaler()
        X_train_scaled = self.scaler.fit_transform(X_train)
        X_test_scaled = self.scaler.transform(X_test)
        
        # XGBoost
        self.xgb_model = xgb.XGBClassifier(
            n_estimators=300,
            max_depth=6,
            learning_rate=0.05,
            subsample=0.8,
            colsample_bytree=0.8,
            use_label_encoder=False,
            eval_metric="logloss",
            random_state=42,
            n_jobs=-1,
        )
        self.xgb_model.fit(X_train, y_train, eval_set=[(X_test, y_test)], verbose=False)
        
        # Random Forest
        self.rf_model = RandomForestClassifier(
            n_estimators=200,
            max_depth=8,
            min_samples_leaf=5,
            random_state=42,
            n_jobs=-1,
        )
        self.rf_model.fit(X_train, y_train)
        
        # Logistic Regression (for comparison)
        self.lr_model = LogisticRegression(
            C=1.0,
            max_iter=500,
            random_state=42,
        )
        self.lr_model.fit(X_train_scaled, y_train)
        
        # Evaluate
        xgb_auc = roc_auc_score(y_test, self.xgb_model.predict_proba(X_test)[:, 1])
        rf_auc = roc_auc_score(y_test, self.rf_model.predict_proba(X_test)[:, 1])
        lr_auc = roc_auc_score(y_test, self.lr_model.predict_proba(X_test_scaled)[:, 1])
        
        logger.info(f"XGBoost AUC: {xgb_auc:.4f} | RF AUC: {rf_auc:.4f} | LR AUC: {lr_auc:.4f}")
        
        self.is_trained = True
        self._save_models()
        return {"xgb_auc": xgb_auc, "rf_auc": rf_auc, "lr_auc": lr_auc}
    
    def _save_models(self):
        os.makedirs("models", exist_ok=True)
        joblib.dump(self.xgb_model, "models/xgb_churn.pkl")
        joblib.dump(self.rf_model, "models/rf_churn.pkl")
        joblib.dump(self.lr_model, "models/lr_churn.pkl")
        joblib.dump(self.scaler, "models/scaler.pkl")
        logger.info("Models saved to models/")
    
    def load_models(self):
        try:
            self.xgb_model = joblib.load("models/xgb_churn.pkl")
            self.rf_model = joblib.load("models/rf_churn.pkl")
            self.lr_model = joblib.load("models/lr_churn.pkl")
            self.scaler = joblib.load("models/scaler.pkl")
            self.is_trained = True
            logger.info("Loaded pre-trained models from disk")
        except FileNotFoundError:
            logger.info("No saved models found. Will train on first request.")
    
    def _encode_plan(self, plan: str) -> int:
        return {"STARTER": 0, "PRO": 1, "BUSINESS": 2, "ENTERPRISE": 3}.get(plan.upper(), 1)
    
    def predict_churn(self, features: CustomerFeatures) -> Dict:
        """Ensemble prediction: 60% XGBoost + 30% RF + 10% LR."""
        if not self.is_trained:
            self.train()
        
        X = np.array([[
            features.engagement_score,
            features.days_since_active,
            features.support_tickets,
            features.tenure_months,
            features.monthly_spend,
            features.nps_score,
            features.feature_adoption_count,
            features.login_frequency_per_week,
            self._encode_plan(features.plan),
        ]])
        
        X_scaled = self.scaler.transform(X)
        
        xgb_prob = self.xgb_model.predict_proba(X)[0][1]
        rf_prob = self.rf_model.predict_proba(X)[0][1]
        lr_prob = self.lr_model.predict_proba(X_scaled)[0][1]
        
        # Weighted ensemble
        final_prob = 0.60 * xgb_prob + 0.30 * rf_prob + 0.10 * lr_prob
        
        risk_level = (
            "CRITICAL" if final_prob > 0.80 else
            "HIGH" if final_prob > 0.60 else
            "MEDIUM" if final_prob > 0.35 else
            "LOW"
        )
        
        # SHAP-style feature importance (approximate)
        feature_names = [
            "Low Engagement Score", "Days Since Last Active", "Support Ticket Volume",
            "Subscription Duration", "Monthly Spend", "NPS Score",
            "Feature Adoption Rate", "Login Frequency", "Plan Type"
        ]
        importances = self.xgb_model.feature_importances_
        shap_factors = [
            {
                "factor": feature_names[i],
                "impact": float(importances[i]),
                "direction": "positive" if i in [3, 4, 5, 6, 7, 8] else "negative",
            }
            for i in np.argsort(importances)[::-1][:6]
        ]
        
        return {
            "customer_id": features.customer_id,
            "churn_probability": round(final_prob, 4),
            "risk_level": risk_level,
            "model_breakdown": {
                "xgboost": round(xgb_prob, 4),
                "random_forest": round(rf_prob, 4),
                "logistic_regression": round(lr_prob, 4),
                "ensemble": round(final_prob, 4),
            },
            "shap_factors": shap_factors,
            "model_version": "XGBoost v3.1 + RF Ensemble",
            "predicted_at": datetime.utcnow().isoformat(),
        }
    
    def segment_customers(self, customers: List[Dict], n_segments: int = 4) -> Dict:
        """K-Means RFM segmentation."""
        if not customers:
            raise ValueError("No customer data provided")
        
        df = pd.DataFrame(customers)
        feature_cols = ["monthly_spend", "tenure_months", "engagement_score", "nps_score", "churn_probability"]
        available_cols = [c for c in feature_cols if c in df.columns]
        
        X = df[available_cols].fillna(0).values
        scaler = StandardScaler()
        X_scaled = scaler.fit_transform(X)
        
        kmeans = KMeans(n_clusters=n_segments, random_state=42, n_init=10)
        labels = kmeans.fit_predict(X_scaled)
        
        df["segment"] = labels
        segment_summary = df.groupby("segment").agg({
            "monthly_spend": "mean",
            "tenure_months": "mean",
            "engagement_score": "mean" if "engagement_score" in df.columns else None,
        }).round(2).to_dict()
        
        segment_names = {
            0: "Enterprise Champions",
            1: "Growth Accounts",
            2: "At-Risk SMB",
            3: "Dormant Users",
        }
        
        return {
            "n_segments": n_segments,
            "segment_labels": [segment_names.get(int(l), f"Segment {l}") for l in labels],
            "segment_summary": segment_summary,
            "inertia": float(kmeans.inertia_),
        }

model_manager = ModelManager()

# ── Startup ────────────────────────────────────────────────────────────────────
@app.on_event("startup")
async def startup_event():
    model_manager.load_models()
    if not model_manager.is_trained:
        model_manager.train()
    logger.info("VisionRetain ML Microservice ready.")

# ── Routes ─────────────────────────────────────────────────────────────────────
@app.get("/health")
def health():
    return {
        "status": "healthy",
        "models_trained": model_manager.is_trained,
        "product_lens": {
            "vision_configured": bool(GEMINI_API_KEY),
            "live_prices_configured": bool(SERPAPI_API_KEY),
            "supabase_configured": bool(SUPABASE_URL and SUPABASE_SECRET_KEY),
        },
        "timestamp": datetime.utcnow().isoformat(),
    }


@app.post("/api/v1/product-lens/scan")
async def scan_product(
    image: UploadFile = File(...),
    user: Dict[str, Any] = Depends(verify_supabase_jwt),
):
    mime_type = image.content_type or ""
    if mime_type not in {"image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"}:
        raise HTTPException(status_code=415, detail="Upload a JPEG, PNG, WebP, HEIC, or HEIF image")
    image_bytes = await image.read(MAX_IMAGE_BYTES + 1)
    if not image_bytes:
        raise HTTPException(status_code=400, detail="The uploaded image is empty")
    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail=f"Image exceeds {MAX_IMAGE_BYTES // (1024 * 1024)} MB")

    identification = identify_product(image_bytes, mime_type)
    pricing = fetch_live_prices(identification)
    scan_id = persist_product_scan(user.get("sub"), image_bytes, identification, pricing)
    return {
        "scan_id": scan_id,
        "identification": identification.model_dump(),
        "pricing": pricing,
        "accuracy_note": (
            "Exact identity requires visible model/SKU evidence. Confidence is the model's "
            "evidence estimate; verify the retailer title before purchase."
        ),
    }


@app.get("/api/v1/product-lens/history")
def product_scan_history(
    limit: int = 10,
    user: Dict[str, Any] = Depends(verify_supabase_jwt),
):
    return {"scans": supabase_recent_scans(user.get("sub"), limit)}


@app.get("/api/v1/dashboard")
def dashboard_data(_: Dict[str, Any] = Depends(verify_supabase_jwt)):
    return {
        "metrics": supabase_select("dashboard_metrics", order="sort_order.asc"),
        "customers": supabase_select("customers", order="updated_at.desc"),
        "fetched_at": datetime.utcnow().isoformat() + "Z",
    }


@app.post("/predict/churn")
def predict_churn(features: CustomerFeatures, _: Dict[str, Any] = Depends(verify_supabase_jwt)):

    """Predict churn probability for a single customer."""
    # Check Redis cache
    cache_key = f"churn:{features.customer_id}"
    cached = cache_get(cache_key)
    if cached:
        logger.info(f"Cache hit for customer {features.customer_id}")
        return json.loads(cached)
    
    result = to_jsonable(model_manager.predict_churn(features))
    
    # Cache for 6 hours
    cache_setex(cache_key, 21600, json.dumps(result))
    return result

@app.post("/predict/churn/batch")
def predict_churn_batch(request: BatchPredictRequest, _: Dict[str, Any] = Depends(verify_supabase_jwt)):

    """Batch churn prediction for multiple customers."""
    results = []
    for customer in request.customers:
        try:
            result = model_manager.predict_churn(customer)
            results.append(result)
        except Exception as e:
            results.append({"customer_id": customer.customer_id, "error": str(e)})
    
    high_risk = [r for r in results if r.get("risk_level") in ("CRITICAL", "HIGH")]
    return {
        "total_predicted": len(results),
        "high_risk_count": len(high_risk),
        "predictions": results,
    }

@app.post("/predict/demand")
def predict_demand(request: DemandForecastRequest, _: Dict[str, Any] = Depends(verify_supabase_jwt)):

    """
    Simple exponential smoothing + trend extrapolation demand forecast.
    In production: use Prophet or LSTM for better accuracy.
    """
    sales = np.array(request.historical_sales)
    if len(sales) < 3:
        raise HTTPException(status_code=400, detail="Need at least 3 months of historical data")
    
    # Exponential smoothing
    alpha = 0.3
    smoothed = [sales[0]]
    for s in sales[1:]:
        smoothed.append(alpha * s + (1 - alpha) * smoothed[-1])
    
    # Trend
    x = np.arange(len(sales))
    trend_coef = np.polyfit(x, sales, 1)
    trend_slope = trend_coef[0]
    
    # Forecast n months
    n_months = max(1, request.horizon_days // 30)
    last_smoothed = smoothed[-1]
    
    forecasts = []
    for i in range(1, n_months + 1):
        base = last_smoothed + trend_slope * i
        seasonal = base * request.seasonality_index
        forecasts.append(round(max(0, seasonal), 1))
    
    # Confidence intervals (±15% for 30d, ±22% for 60d, ±30% for 90d)
    ci_pcts = [0.15, 0.22, 0.30]
    intervals = []
    for i, f in enumerate(forecasts):
        ci = ci_pcts[min(i, 2)]
        intervals.append({"lower": round(f * (1 - ci), 1), "upper": round(f * (1 + ci), 1)})
    
    total_forecast = sum(forecasts)
    growth_rate = ((forecasts[-1] - sales[-1]) / sales[-1] * 100) if sales[-1] > 0 else 0
    
    return {
        "product_id": request.product_id,
        "horizon_days": request.horizon_days,
        "monthly_forecasts": forecasts,
        "confidence_intervals": intervals,
        "total_forecast": round(total_forecast, 1),
        "growth_rate_pct": round(growth_rate, 2),
        "trend_slope_per_month": round(float(trend_slope), 2),
        "model": "Exponential Smoothing + Linear Trend",
        "generated_at": datetime.utcnow().isoformat(),
    }

@app.post("/predict/ltv")
def predict_ltv(features: CustomerFeatures, _: Dict[str, Any] = Depends(verify_supabase_jwt)):

    """Predict 12-month Customer Lifetime Value."""
    monthly_spend = features.monthly_spend
    churn_result = model_manager.predict_churn(features)
    churn_prob = churn_result["churn_probability"]
    
    # Expected months remaining = geometric series with churn rate
    monthly_churn = churn_prob / 12  # Approximate monthly churn from annual
    expected_months = 1 / monthly_churn if monthly_churn > 0 else 36
    expected_months = min(expected_months, 36)  # Cap at 3 years
    
    gross_margin = 0.75  # Typical SaaS gross margin
    discount_rate = 0.10 / 12  # 10% annual WACC → monthly
    
    # Discounted LTV
    ltv = 0
    for t in range(1, int(expected_months) + 1):
        ltv += (monthly_spend * gross_margin) / ((1 + discount_rate) ** t)
    
    return {
        "customer_id": features.customer_id,
        "predicted_ltv_12m": round(ltv, 2),
        "expected_months_remaining": round(expected_months, 1),
        "monthly_spend": monthly_spend,
        "churn_probability": churn_prob,
        "gross_margin_applied": gross_margin,
        "currency": "INR",
        "generated_at": datetime.utcnow().isoformat(),
    }

@app.post("/segment/customers")
def segment_customers(request: SegmentRequest, _: Dict[str, Any] = Depends(verify_supabase_jwt)):

    """K-Means customer segmentation with RFM features."""
    result = model_manager.segment_customers(request.customers, request.n_segments)
    return result

@app.post("/retrain")
def retrain_models(background_tasks: BackgroundTasks):
    """Trigger async model retraining (in production: use real data from MongoDB)."""
    background_tasks.add_task(model_manager.train)
    return {"status": "Retraining started in background", "estimated_time_seconds": 60}

@app.get("/metrics")
def get_metrics():
    """Return model performance metrics."""
    return {
        "models": {
            "xgboost": {"type": "XGBClassifier", "trees": 300, "depth": 6},
            "random_forest": {"type": "RandomForestClassifier", "trees": 200, "depth": 8},
            "logistic_regression": {"type": "LogisticRegression", "C": 1.0},
        },
        "ensemble_weights": {"xgboost": 0.60, "random_forest": 0.30, "logistic_regression": 0.10},
        "last_trained": datetime.utcnow().isoformat(),
        "features_used": 9,
        "training_samples": 5000,
    }

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8001, reload=False, workers=4)
