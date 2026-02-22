# ⚡ Real-Time Fraud Detection Engine

A production-grade, stream-processing fraud detection system built with **Kafka (Redpanda)**, **Redis**, **XGBoost**, and **FastAPI** — containerised end-to-end with Docker Compose and observable via **Prometheus + Grafana**.

---

## 🏗️ Architecture

```
┌─────────────────┐     Kafka Topic        ┌──────────────────────┐
│  generator.py   │ ──(raw-transactions)──▶ │    detector.py       │
│                 │                         │                      │
│ Simulates 50    │                         │  • Velocity check    │
│ user profiles   │                         │  • Amount anomaly    │
│ w/ fraud        │                         │  • Location mismatch │
│ injection       │                         │  • ML inference call │
└─────────────────┘                         └────────┬─────────────┘
                                                     │ HTTP POST
                                          ┌──────────▼──────────┐
        ┌──────────────────────┐          │    ml_service.py     │
        │       Redis          │◀────────▶│                      │
        │  User profiles &     │          │  XGBoost classifier  │
        │  TX velocity state   │          │  + SHAP explainability│
        └──────────────────────┘          └─────────────────────┘
                                                     │
                                          ┌──────────▼──────────┐
                                          │  Prometheus + Grafana│
                                          │  (metrics on :8001)  │
                                          └─────────────────────┘
```

### Services at a Glance

| Service        | File / Image            | Port             | Role                                         |
| -------------- | ----------------------- | ---------------- | -------------------------------------------- |
| **Generator**  | `generator.py`          | —                | Produces synthetic transactions to Kafka     |
| **Detector**   | `detector.py`           | `8001` (metrics) | Consumes, applies rules + ML, emits verdicts |
| **ML Service** | `ml_service.py`         | `8000`           | XGBoost inference + SHAP explanations        |
| **Redpanda**   | `redpandadata/redpanda` | `19092`          | Kafka-compatible message broker              |
| **Redis**      | `redis:alpine`          | `6379`           | User profiles + velocity state store         |
| **Prometheus** | `prom/prometheus`       | `9090`           | Metrics scraper                              |
| **Grafana**    | `grafana/grafana`       | `3000`           | Dashboards                                   |

---

## 🧠 How Fraud Is Detected

The detector applies a **layered defence** strategy per transaction:

1. **Rule-Based Checks** (fast, synchronous)
   - 📈 **Amount Anomaly** — flags if amount > 5× the user's historical average
   - 🚀 **Velocity Anomaly** — flags 5+ transactions within 60 seconds
   - 🌍 **Location Anomaly** — flags transactions outside the user's home region

2. **ML Inference** (XGBoost via HTTP, 100 ms timeout)
   - Features: `amount`, `user_avg_amount`, `amount_ratio`, `location_mismatch`, `is_international`
   - Threshold: fraud probability > **80%**
   - **SHAP values** generate human-readable explanations for every positive prediction

3. **Cold-Start Handling** — new/unknown users fall back to safe defaults so the system never crashes on missing profiles.

---

## 🚀 Quickstart

### Prerequisites

- [Docker](https://docs.docker.com/get-docker/) & Docker Compose
- Python 3.9+ (for local runs / model training)

### 1 — Clone & spin up infrastructure

```bash
git clone <your-repo-url>
cd realtime-fraud-engine
docker compose up -d
```

This starts Redpanda, Redis, Prometheus, and Grafana automatically.

### 2 — Seed user profiles into Redis

```bash
pip install -r requirements.txt
python setup_redis.py
```

Creates 50 synthetic user profiles, each with a home location, average spend, and spend volatility.

### 3 — Train the ML model

```bash
python train_model.py
```

Generates ~10 000 labelled transactions, trains an XGBoost classifier, fits a SHAP TreeExplainer, and saves both as pickle artifacts (`fraud_model.pkl`, `shap_explainer.pkl`).

> **Pre-trained artifacts** (`fraud_model.pkl` and `shap_explainer.pkl`) are already committed. Skip this step if you just want to run the engine.

### 4 — Start the application stack

```bash
docker compose up --build
```

Or run each service individually for development:

```bash
# Terminal 1 — ML inference API
uvicorn ml_service:app --reload --port 8000

# Terminal 2 — Fraud detector
python detector.py

# Terminal 3 — Transaction generator
python generator.py
```

---

## 🗂️ Project Structure

```
realtime-fraud-engine/
├── generator.py        # Synthetic transaction producer (Kafka)
├── detector.py         # Core stream processor & fraud logic
├── ml_service.py       # FastAPI ML inference endpoint
├── train_model.py      # XGBoost + SHAP model training script
├── setup_redis.py      # Seeds user profiles into Redis
├── consumer.py         # Lightweight debug consumer (print-only)
├── fraud_model.pkl     # Pre-trained XGBoost model artifact
├── shap_explainer.pkl  # Pre-trained SHAP TreeExplainer artifact
├── requirements.txt    # Python dependencies
├── Dockerfile          # Single image for all Python services
├── docker-compose.yml  # Full stack orchestration
└── prometheus.yml      # Prometheus scrape config
```

---

## ⚙️ Configuration

All services read configuration from environment variables with sensible defaults:

| Variable                  | Default                         | Description                        |
| ------------------------- | ------------------------------- | ---------------------------------- |
| `KAFKA_BROKER`            | `localhost:19092`               | Kafka / Redpanda bootstrap servers |
| `KAFKA_TOPIC`             | `raw-transactions`              | Input topic name                   |
| `REDIS_HOST`              | `localhost`                     | Redis hostname                     |
| `REDIS_PORT`              | `6379`                          | Redis port                         |
| `ML_SERVICE_URL`          | `http://localhost:8000/predict` | ML inference endpoint              |
| `FRAUD_PROBABILITY`       | `0.05`                          | Fraction of injected fraud events  |
| `TRANSACTIONS_PER_SECOND` | `5.0`                           | Generator throughput rate          |

When running with Docker Compose these are automatically wired to the correct service hostnames.

---

## 📊 Observability

The detector exposes a **Prometheus metrics endpoint** on port `8001`:

| Metric                           | Type      | Description                         |
| -------------------------------- | --------- | ----------------------------------- |
| `transactions_processed_total`   | Counter   | Total transactions consumed         |
| `fraud_caught_total`             | Counter   | Total transactions flagged as fraud |
| `transaction_processing_seconds` | Histogram | Per-transaction processing latency  |

### Access dashboards

| Tool        | URL                                                                       |
| ----------- | ------------------------------------------------------------------------- |
| Prometheus  | [http://localhost:9090](http://localhost:9090)                            |
| Grafana     | [http://localhost:3000](http://localhost:3000) (default: `admin`/`admin`) |
| ML API docs | [http://localhost:8000/docs](http://localhost:8000/docs)                  |

To visualise fraud metrics in Grafana, add Prometheus as a data source (`http://prometheus:9090`) and build a dashboard using the metrics above.

---

## 🔌 ML Service API

**`POST /predict`**

```json
// Request
{
  "amount": 850.00,
  "user_avg_amount": 45.00,
  "location_mismatch": 1,
  "is_international": 0
}

// Response
{
  "fraud_probability": 0.9732,
  "is_fraud": true,
  "explanation": [
    "Transaction amount is unusually high compared to user history.",
    "Transaction location does not match user's typical region."
  ]
}
```

Interactive API docs available at [`/docs`](http://localhost:8000/docs) (Swagger UI).

---

## 🛠️ Tech Stack

| Layer            | Technology                                           |
| ---------------- | ---------------------------------------------------- |
| Message Broker   | [Redpanda](https://redpanda.com/) (Kafka-compatible) |
| Stream Processor | Python + `confluent-kafka`                           |
| State Store      | Redis                                                |
| ML Model         | XGBoost 1.7.6                                        |
| Explainability   | SHAP (TreeExplainer)                                 |
| Inference API    | FastAPI + Uvicorn                                    |
| Observability    | Prometheus + Grafana                                 |
| Containerisation | Docker / Docker Compose                              |

---

## 📄 License

MIT — feel free to use, modify, and distribute.
