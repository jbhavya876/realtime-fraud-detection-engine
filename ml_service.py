import os
import io
import json
import hashlib
import redis
from fastapi import UploadFile, File, HTTPException, Request, Depends
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import pickle
import pandas as pd
import numpy as np
import shap

# --- Security Config ---
MAX_CSV_BYTES = 10 * 1024 * 1024  # 10 MB hard cap
INTERNAL_API_KEY = os.getenv("INTERNAL_API_KEY", "")  # Must be set in production

# Allowed origins for CORS (set ML_SERVICE_CORS_ORIGINS env var in prod, comma-separated)
_cors_origins_env = os.getenv("ML_SERVICE_CORS_ORIGINS", "")
ALLOWED_ORIGINS = [o.strip() for o in _cors_origins_env.split(",") if o.strip()] or ["*"]

app = FastAPI(title="Fraud Inference Engine")

# --- CORS Middleware ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["POST", "GET"],
    allow_headers=["*"],
)

REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)

def _require_internal_key(request: Request):
    """Validate the internal API key for service-to-service calls."""
    if INTERNAL_API_KEY:
        provided = request.headers.get("X-Internal-Key", "")
        if provided != INTERNAL_API_KEY:
            raise HTTPException(status_code=401, detail="Unauthorized")

# Load our pre-trained artifacts on startup
# SEC-02: Verify model artifact integrity via SHA-256 before loading.
# Set MODEL_SHA256 and EXPLAINER_SHA256 env vars in production to the
# expected hex digests; if unset, the check is skipped (dev mode).
def _verify_and_load(path: str, expected_sha256: str | None):
    with open(path, 'rb') as f:
        raw = f.read()
    if expected_sha256:
        actual = hashlib.sha256(raw).hexdigest()
        if actual != expected_sha256:
            raise RuntimeError(
                f"Artifact integrity check failed for {path}. "
                f"Expected {expected_sha256}, got {actual}. "
                "Do not load — the file may have been tampered with."
            )
    return pickle.loads(raw)  # noqa: S301 — integrity verified above

print("Loading Model and Explainer...")
model = _verify_and_load('fraud_model.pkl', os.getenv("MODEL_SHA256"))
explainer = _verify_and_load('shap_explainer.pkl', os.getenv("EXPLAINER_SHA256"))
print("Artifacts loaded.")

# Define the expected input payload
class TransactionFeatures(BaseModel):
    amount: float
    user_avg_amount: float
    location_mismatch: int
    is_international: int

@app.post("/predict")
def predict_fraud(features: TransactionFeatures, _: None = Depends(_require_internal_key)):
    # 1. Prepare data for XGBoost
    df = pd.DataFrame([{
        'amount': features.amount,
        'user_avg_amount': features.user_avg_amount,
        'amount_ratio': features.amount / features.user_avg_amount if features.user_avg_amount > 0 else 1.0,
        'location_mismatch': features.location_mismatch,
        'is_international': features.is_international
    }])
    
    # 2. Get Prediction (0 = Normal, 1 = Fraud)
    # predict_proba returns [probability_of_0, probability_of_1]
    fraud_prob = float(model.predict_proba(df)[0][1])
    is_fraud = bool(fraud_prob > 0.80) # Threshold for flagging
    
    reasons = []
    
    # 3. The "Explainability" Module (SHAP)
    if is_fraud:
        # Calculate SHAP values for this specific transaction
        shap_values = explainer.shap_values(df)
        
        # XGBoost handles binary classification SHAP values slightly differently depending on version.
        # Often it returns a list of arrays (one for each class) or a single array.
        if isinstance(shap_values, list):
            vals = shap_values[1][0] # Get values for the 'fraud' class
        else:
            vals = shap_values[0]
            
        feature_names = df.columns
        
        # Find the features that contributed most strongly to the 'fraud' decision (positive SHAP values)
        contributions = list(zip(feature_names, vals))
        contributions.sort(key=lambda x: x[1], reverse=True)
        
        # Translate the top contributors into human-readable reasons
        for feat, val in contributions[:2]: # Get top 2 reasons
            if val > 0.5: # Only report significant contributions
                if feat == 'amount_ratio':
                    reasons.append(f"Transaction amount is unusually high compared to user history.")
                elif feat == 'location_mismatch':
                    reasons.append(f"Transaction location does not match user's typical region.")
                elif feat == 'is_international':
                    reasons.append(f"International transaction flagged.")
                elif feat == 'amount':
                     reasons.append(f"Absolute transaction amount is suspiciously large.")

        if not reasons:
             reasons.append("Complex anomalous pattern detected across multiple features.")

    return {
        "fraud_probability": round(fraud_prob, 4),
        "is_fraud": is_fraud,
        "explanation": reasons
    }

@app.post("/batch-analyze")
async def analyze_csv(file: UploadFile = File(...)):
    # SEC-04: Validate MIME type, not just the filename extension.
    if not file.content_type in ("text/csv", "application/csv", "text/plain"):
        # Fallback: check filename as a secondary signal only
        if not (file.filename or "").lower().endswith('.csv'):
            raise HTTPException(status_code=400, detail="Only CSV files are supported.")

    # SEC-05: Enforce file size limit to prevent OOM from malicious uploads.
    contents = await file.read(MAX_CSV_BYTES + 1)
    if len(contents) > MAX_CSV_BYTES:
        raise HTTPException(status_code=413, detail="File too large. Maximum allowed size is 10 MB.")
    try:
        raw_df = pd.read_csv(io.StringIO(contents.decode('utf-8')))
    except Exception as e:
         raise HTTPException(status_code=400, detail=f"Error reading CSV: {str(e)}")
    
    # Required columns for our pipeline
    required_cols = ['transaction_id', 'user_id', 'amount', 'location']
    if not all(col in raw_df.columns for col in required_cols):
        raise HTTPException(status_code=400, detail=f"CSV must contain columns: {required_cols}")

    results = []
    
    # Process each row
    for index, row in raw_df.iterrows():
        user_id = row['user_id']
        amount = float(row['amount'])
        location = row['location']
        
        # 1. Fetch User Profile from Redis (Just like the detector does)
        profile_json = r.get(f"user_profile:{user_id}")
        if not profile_json:
            # Fallback for unknown users in the CSV
            avg_amount = 50.0 
            base_location = location
        else:
            profile = json.loads(profile_json)
            avg_amount = profile['avg_transaction_amount']
            base_location = profile['base_location']
            
        # 2. Engineer Features
        location_mismatch = 1 if location != base_location and location not in ['INTERNATIONAL', 'ONLINE'] else 0
        is_international = 1 if location == 'INTERNATIONAL' else 0
        amount_ratio = amount / avg_amount if avg_amount > 0 else 1.0
        
        feature_dict = {
            'amount': amount,
            'user_avg_amount': avg_amount,
            'amount_ratio': amount_ratio,
            'location_mismatch': location_mismatch,
            'is_international': is_international
        }
        
        feature_df = pd.DataFrame([feature_dict])
        
        # 3. Predict
        fraud_prob = float(model.predict_proba(feature_df)[0][1])
        is_fraud = bool(fraud_prob > 0.80)
        
        reasons = []
        if is_fraud:
            # Calculate SHAP values
            shap_values = explainer.shap_values(feature_df)
            vals = shap_values[1][0] if isinstance(shap_values, list) else shap_values[0]
            
            contributions = list(zip(feature_df.columns, vals))
            contributions.sort(key=lambda x: x[1], reverse=True)
            
            for feat, val in contributions[:2]:
                if val > 0.5:
                    if feat == 'amount_ratio': reasons.append("Amount unusually high vs history.")
                    elif feat == 'location_mismatch': reasons.append("Location mismatch.")
                    elif feat == 'is_international': reasons.append("International tx flagged.")
                    elif feat == 'amount': reasons.append("Absolute amount is suspicious.")
            if not reasons: reasons.append("Complex anomaly detected.")

        # Append to results
        results.append({
            "transaction_id": row['transaction_id'],
            "user_id": user_id,
            "amount": amount,
            "location": location,
            "is_fraud": is_fraud,
            "fraud_probability": round(fraud_prob, 4),
            "reasons": ", ".join(reasons) if reasons else "None"
        })

    return {"analyzed_transactions": results}

# Run this via terminal: uvicorn ml_service:app --reload --port 8000