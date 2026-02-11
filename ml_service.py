from fastapi import FastAPI
from pydantic import BaseModel
import pickle
import pandas as pd
import numpy as np
import shap

app = FastAPI(title="Fraud Inference Engine")

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

# Run this via terminal: uvicorn ml_service:app --reload --port 8000