import os
import io
import redis
from fastapi import UploadFile, File, HTTPException
from fastapi import FastAPI
from pydantic import BaseModel
import pickle
import pandas as pd
import numpy as np
import shap

app = FastAPI(title="Fraud Inference Engine")
REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)

# Load our pre-trained artifacts on startup
print("Loading Model and Explainer...")
with open('fraud_model.pkl', 'rb') as f:
    model = pickle.load(f)
with open('shap_explainer.pkl', 'rb') as f:
    explainer = pickle.load(f)
print("Artifacts loaded.")

# Define the expected input payload
class TransactionFeatures(BaseModel):
    amount: float
    user_avg_amount: float
    location_mismatch: int
    is_international: int

@app.post("/predict")
def predict_fraud(features: TransactionFeatures):
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
    if not file.filename.endswith('.csv'):
        raise HTTPException(status_code=400, detail="Only CSV files are supported.")
    
    # Read the uploaded CSV into a Pandas DataFrame
    contents = await file.read()
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
            import json
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