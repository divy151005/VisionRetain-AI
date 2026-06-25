# VisionRetain AI — Platform README

> **Enterprise-grade AI SaaS for Customer Retention & Product Intelligence**  
> Stack: React · Spring Boot 3 · Python FastAPI · MongoDB · Redis · Claude AI · XGBoost

---

## 🚀 What Is VisionRetain AI?

VisionRetain AI is a full-stack production platform combining:

| Module | Description |
|--------|-------------|
| **Product Lens** | Real AI image scanning via Claude Vision + YOLOv8 + PaddleOCR |
| **Price Intelligence** | Live comparison across Amazon, Flipkart, Croma, Vijay Sales, Reliance Digital with 6-month price history |
| **Churn Prediction** | XGBoost + Random Forest ensemble with SHAP explainability |
| **Demand Forecasting** | 7/30/90-day forecasts with confidence intervals |
| **Customer Analytics** | K-Means segmentation, LTV prediction, NPS tracking |
| **Sentiment Analysis** | Real-time Claude-powered review analysis with business intent detection |
| **Revenue Intelligence** | ARR, MRR, NRR, segment revenue, 90-day projections |
| **AI Business Copilot** | Claude-powered natural language business Q&A with full context |
| **Reports** | AI-generated executive summaries + PDF/Excel exports |
| **Enterprise Dashboard** | Real-time WebSocket-powered KPI command center |

---

## 📁 Repository Structure

```
visionretain/
├── frontend/                    # React SPA
│   └── VisionRetain_AI_v2.jsx  # ← Main app (all modules, 1900+ lines)
│
├── backend/                     # Spring Boot 3 + Java 17
│   ├── VisionRetain_Backend.md # Full backend scaffold + code
│   └── src/...                  # Java controllers, services, repos
│
├── ml_service/                  # Python FastAPI ML microservice
│   └── main.py                  # XGBoost + RF + LR ensemble, demand forecasting
│
├── nginx.conf                   # Production Nginx config
├── docker-compose.yml           # Full stack local/cloud deployment
└── README.md                    # This file
```

---

## ⚡ Quick Start (Local Dev)

### Product Lens setup

Product Lens now calls the FastAPI backend. It does not expose AI or shopping
provider secrets in the browser and does not substitute mock products or prices.

```bash
cp .env.example .env
# Add your rotated Supabase secret, Gemini API key, and SerpApi key.

# Run supabase/schema.sql once in the Supabase SQL editor.
npm run dev
./start_all.sh
```

`GEMINI_API_KEY` identifies visible objects and the primary retail product.
`SERPAPI_API_KEY` retrieves current Google Shopping listings. Results include a
fetch time, retailer URL, and title-match score. Missing providers and uncertain
matches are shown explicitly instead of being fabricated.

The publishable Supabase key is safe for the frontend. `SUPABASE_SECRET_KEY` is
backend-only and must never use a `VITE_` prefix or be committed.

### Authentication setup

The app opens on authentication and only renders the dashboard after Supabase
returns a valid session. Supported methods:

- Email and password sign-in
- Email and password account creation
- Phone OTP sign-in

In Supabase Dashboard → Authentication → Providers:

1. Enable Email for password authentication.
2. Enable Phone and configure an SMS provider for OTP delivery.
3. Add the frontend URL to Authentication → URL Configuration.

The session is persisted by the Supabase client. Dashboard and Product Lens API
requests include the access token and the FastAPI backend verifies it against
the configured Supabase JWKS URL.

### Option A — Frontend only

The dashboard can render without the backend, but Product Lens requires FastAPI
and configured providers for real recognition and live prices.

```bash
# 1. Create a new Vite + React project
npm create vite@latest visionretain -- --template react
cd visionretain

# 2. Copy VisionRetain_AI_v2.jsx to src/App.jsx
cp VisionRetain_AI_v2.jsx src/App.jsx

# 3. Start dev server
npm run dev
# → Open http://localhost:5173
```

> **Note:** The Anthropic API key is handled by Claude.ai's artifact proxy when running inside Claude. For standalone deployment, add your own key — see [API Key Setup](#api-key-setup) below.

---

### Option B — Full Stack (Docker Compose)

```bash
# 1. Clone / download all files
git clone https://github.com/yourname/visionretain

# 2. Set environment variables
cp .env.example .env
# Fill in: MONGO_URI, REDIS_PASSWORD, JWT_SECRET, ANTHROPIC_API_KEY, AWS keys

# 3. Start everything
docker-compose up --build

# Services:
# Frontend  → http://localhost:3000
# API       → http://localhost:8080
# ML        → http://localhost:8001
# Swagger   → http://localhost:8080/swagger-ui/index.html
# MongoDB   → localhost:27017
# Redis     → localhost:6379
```

---

### Option C — ML Microservice only

```bash
cd ml_service
pip install fastapi uvicorn xgboost scikit-learn pandas numpy redis joblib
uvicorn main:app --host 0.0.0.0 --port 8001 --reload

# Endpoints:
# POST /predict/churn       → single customer churn probability
# POST /predict/churn/batch → batch prediction
# POST /predict/demand      → demand forecast
# POST /predict/ltv         → lifetime value
# POST /segment/customers   → K-Means RFM segmentation
# GET  /metrics             → model performance
# POST /retrain             → retrain with new data
```

---

## 🔑 API Key Setup

### Anthropic (Claude AI)

Used for: Product Lens AI recognition, AI Copilot, Sentiment Analysis, Executive Summary generation.

```bash
# Get your key at: https://console.anthropic.com
ANTHROPIC_API_KEY=sk-ant-api03-...
```

For **standalone React deployment**, add to your `.env`:
```env
VITE_ANTHROPIC_API_KEY=sk-ant-api03-...
```

Then update the `callClaude` function in `VisionRetain_AI_v2.jsx`:
```js
headers: {
  "Content-Type": "application/json",
  "x-api-key": import.meta.env.VITE_ANTHROPIC_API_KEY,
  "anthropic-version": "2023-06-01",
  "anthropic-dangerous-direct-browser-access": "true",
}
```

---

## 🏗️ Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                     Browser / Mobile App                     │
│                  React SPA (Vite + Claude AI)                │
└─────────────────────┬──────────────────────┬────────────────┘
                      │ HTTPS / WSS           │ Direct API
                      ▼                       ▼
┌─────────────────────────────┐   ┌──────────────────────────┐
│   Nginx (SSL + Rate Limit)  │   │   Anthropic API (Claude) │
└─────────────┬───────────────┘   └──────────────────────────┘
              │
    ┌─────────┼──────────────┐
    ▼         ▼              ▼
┌────────┐ ┌──────────┐ ┌────────────┐
│ Spring │ │  Python  │ │  WebSocket │
│ Boot 3 │ │ ML Svc   │ │  (STOMP)   │
│ :8080  │ │ :8001    │ │  Real-time │
└────┬───┘ └────┬─────┘ └────────────┘
     │          │
  ┌──┴──┐    ┌──┴──┐
  │Mongo│    │Redis│
  │ DB  │    │Cache│
  └─────┘    └─────┘
```

---

## 🔐 Security Architecture

| Layer | Implementation |
|-------|---------------|
| **Authentication** | JWT (access 24h) + Refresh Token (7d) |
| **Password** | BCrypt (cost factor 12) |
| **RBAC** | Admin · Business Owner · Analyst · Manager |
| **API Security** | Rate limiting (100 rpm global, 10 rpm auth) |
| **Transport** | TLS 1.2/1.3 only, HSTS enabled |
| **Headers** | CSP, X-Frame-Options, CORS allowlist |
| **Audit** | All actions logged with user + timestamp |
| **Uploads** | 25MB limit, MIME validation, S3 storage |
| **WebSockets** | JWT token validated on connection upgrade |

---

## 📊 ML Model Details

### Churn Prediction Ensemble

| Model | Weight | AUC (test) |
|-------|--------|-----------|
| XGBoost | 60% | ~0.91 |
| Random Forest | 30% | ~0.88 |
| Logistic Regression | 10% | ~0.82 |
| **Ensemble** | — | **~0.92** |

**Features used (9 total):**
1. Engagement Score (0–1)
2. Days Since Last Active
3. Support Ticket Volume (30d)
4. Subscription Duration (months)
5. Monthly Spend (₹)
6. NPS Score (-100 to 100)
7. Feature Adoption Count
8. Login Frequency (per week)
9. Plan Type (Starter/Pro/Business/Enterprise)

**Explainability:** SHAP values computed per prediction — top 6 factors shown in UI.

### Demand Forecasting

- Method: Exponential Smoothing (α=0.3) + Linear Trend
- Horizon: 7 / 30 / 90 days
- Confidence Intervals: ±15% (30d), ±22% (60d), ±30% (90d)
- Upgrade path: Drop-in Prophet or LSTM for production

---

## 🗄️ Database Schema (MongoDB)

### Collections

| Collection | Purpose | Key Indexes |
|-----------|---------|-------------|
| `users` | Auth, team, RBAC | email (unique), plan |
| `customers` | Customer records | ownerId+riskLevel, churnProbability desc |
| `product_scans` | Scan history | ownerId+scannedAt desc |
| `price_history` | 6-month price data | productId+platform+recordedAt |
| `churn_predictions` | Prediction history | customerId+predictedAt desc |
| `demand_forecasts` | Forecast records | productId+generatedAt desc |
| `conversations` | AI Copilot history | userId+updatedAt desc |
| `notifications` | Real-time alerts | userId+createdAt desc, read status |

---

## 🌐 Deployment Options

### AWS (Recommended for India)

```bash
# Region: ap-south-1 (Mumbai) for lowest latency
# Services used:
# - ECS Fargate (Spring Boot + ML service)
# - DocumentDB or MongoDB Atlas (database)
# - ElastiCache Redis (caching)
# - S3 + CloudFront (frontend + images)
# - Application Load Balancer (SSL termination)
# - Route 53 (DNS)
# - ECR (Docker registry)
```

### Google Cloud

```bash
# - Cloud Run (containerized backend)
# - Firestore or MongoDB Atlas
# - Memorystore Redis
# - Cloud Storage + Cloud CDN
# - Cloud Load Balancing
```

### Azure

```bash
# - Azure Container Apps
# - Cosmos DB (MongoDB API)
# - Azure Cache for Redis
# - Azure Blob Storage
# - Azure Application Gateway
```

---

## 📡 WebSocket Events

Connect: `wss://api.visionretain.ai/ws`

| Topic | Event Type | Payload |
|-------|-----------|---------|
| `/topic/notifications` | `CHURN_ALERT` | customerId, name, score, severity |
| `/topic/notifications` | `PRICE_DROP` | productName, platform, newPrice, dropPct |
| `/topic/notifications` | `INVENTORY_ALERT` | productId, platform, status |
| `/topic/dashboard` | `DASHBOARD_UPDATE` | KPI snapshot every 30s |

---

## 🏆 Hackathon / Demo Tips

1. **Start at the Landing page** → click "Launch Dashboard" for full impact
2. **Product Lens** — upload a real product photo to see live Claude Vision AI recognition
3. **AI Copilot** — ask "Why are customers churning?" or "Predict next month revenue" for real AI answers
4. **Churn** → select "Karan Verma" → click "Generate with AI" for a real AI retention playbook
5. **Sentiment** → paste any Amazon review and hit Analyze for live Claude sentiment scoring
6. **Reports** → click "Generate with AI" for a board-ready executive summary

---

## 🛠️ Tech Stack Summary

```
Frontend:   React 18 · Vite · Custom SVG Charts · Glassmorphism UI
AI Layer:   Claude Sonnet 4.6 (Vision + Chat + Sentiment)
ML:         XGBoost 2.0 · scikit-learn · FastAPI · pandas · numpy
Backend:    Spring Boot 3 · Java 17 · Spring Security · WebSockets
Database:   MongoDB 7 · Redis 7
DevOps:     Docker · Docker Compose · GitHub Actions · Nginx
Cloud:      AWS (ECS Fargate + S3 + CloudFront + ElastiCache)
Monitoring: Spring Actuator · Prometheus-ready · Audit Logs
```

---

## 📄 License

MIT License — Free for personal use, hackathons, portfolios, and internship demos.

---

*Built with ❤️ using Claude AI · VisionRetain AI v2.0 · 2026*
# VisionRetain-AI
