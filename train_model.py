import pandas as pd
import xgboost as xgb
import shap
import pickle
import random
from generator import get_all_users, generate_normal_transaction, generate_fraud_pattern

print("Loading users from Redis...")
# Fetch users dynamically just like the generator does
users = get_all_users()

if not users:
    print("❌ No users found in Redis! Please run 'python setup_redis.py' first.")
    exit(1)

print("Generating training data...")
data = []
# Generate 10,000 transactions for training
for _ in range(10000):
    user = random.choice(users)
    if random.random() < 0.10: # 10% fraud rate for training to ensure enough examples
        txs = generate_fraud_pattern(user)
        data.extend(txs)
    else:
        tx = generate_normal_transaction(user)
        data.append(tx)

df = pd.DataFrame(data)

# --- Feature Engineering ---
print("Engineering features...")

# 1. We need the user's baseline average (simulating what Redis provides)
user_avgs = {u['user_id']: u['avg_transaction_amount'] for u in users}
df['user_avg_amount'] = df['user_id'].map(user_avgs)

# 2. Calculate the Ratio of Current Amount to Average Amount
df['amount_ratio'] = df['amount'] / df['user_avg_amount']

# 3. Handle Location Anomaly
user_locations = {u['user_id']: u['base_location'] for u in users}
df['base_location'] = df['user_id'].map(user_locations)
df['location_mismatch'] = (df['location'] != df['base_location']).astype(int)
df['is_international'] = (df['location'] == 'INTERNATIONAL').astype(int)

# Define the features we will use for training
features = ['amount', 'user_avg_amount', 'amount_ratio', 'location_mismatch', 'is_international']
X = df[features]
y = df['is_fraud'].astype(int)

# --- Train the XGBoost Model ---
print("Training XGBoost model...")
scale_weight = (len(y) - sum(y)) / sum(y) 

model = xgb.XGBClassifier(
    n_estimators=100,
    max_depth=4,
    learning_rate=0.1,
    scale_pos_weight=scale_weight,
    eval_metric='logloss'
)

model.fit(X, y)

# --- Prepare the Explainer ---
print("Fitting SHAP Explainer...")

# THE FIX: Extract the raw booster to bypass the SHAP parsing bug
booster = model.get_booster()

# Create a basic TreeExplainer without the background data/probability arguments
explainer = shap.TreeExplainer(booster)

print("Saving model and explainer artifacts...")
with open('fraud_model.pkl', 'wb') as f:
    pickle.dump(model, f)
with open('shap_explainer.pkl', 'wb') as f:
    pickle.dump(explainer, f)

print("✅ Training complete. Artifacts saved.")