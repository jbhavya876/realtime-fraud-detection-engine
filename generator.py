import json
import random
import time
import uuid
import redis
from datetime import datetime
from confluent_kafka import Producer

# --- Configuration ---
KAFKA_TOPIC = "raw-transactions"
KAFKA_BOOTSTRAP_SERVERS = "localhost:19092"
REDIS_HOST = "localhost"
REDIS_PORT = 6379
TRANSACTIONS_PER_SECOND = 5
FRAUD_PROBABILITY = 0.05

# Connect to Services
producer = Producer({'bootstrap.servers': KAFKA_BOOTSTRAP_SERVERS})
r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)

def get_all_users():
    """Fetches all user profiles from Redis to generate valid transactions."""
    keys = r.keys("user_profile:*")
    users = []
    for key in keys:
        profile_json = r.get(key)
        if profile_json:
            users.append(json.loads(profile_json))
    return users

def delivery_report(err, msg):
    if err is not None:
        print(f"Message delivery failed: {err}")

# --- Generation Logic ---

def generate_normal_transaction(user):
    # Use the USER'S specific average, not a global one
    amount = max(1.0, random.gauss(user["avg_transaction_amount"], user["std_dev_amount"]))
    return {
        "transaction_id": str(uuid.uuid4()),
        "user_id": user["user_id"],
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "amount": round(amount, 2),
        "location": user["base_location"],
        "type": random.choice(["GROCERY", "RETAIL", "RESTAURANT", "ONLINE"]),
        "is_fraud": False
    }

def generate_fraud_pattern(user):
    # Inject anomalies based on the USER'S profile
    pattern_type = random.choice(["VELOCITY", "LARGE_AMOUNT", "LOCATION_ANOMALY"])
    transactions = []
    timestamp = datetime.utcnow()
    
    if pattern_type == "VELOCITY":
        for i in range(5):
            transactions.append({
                "transaction_id": str(uuid.uuid4()),
                "user_id": user["user_id"],
                "timestamp": timestamp.isoformat() + "Z",
                "amount": round(random.uniform(5.0, 50.0), 2),
                "location": user["base_location"],
                "type": "ONLINE",
                "is_fraud": True,
                "fraud_pattern": "VELOCITY"
            })
    elif pattern_type == "LARGE_AMOUNT":
        # Make it 10x their normal spend
        amount = user["avg_transaction_amount"] * 10
        transactions.append({
            "transaction_id": str(uuid.uuid4()),
            "user_id": user["user_id"],
            "timestamp": timestamp.isoformat() + "Z",
            "amount": round(amount, 2),
            "location": user["base_location"],
            "type": "TRANSFER",
            "is_fraud": True,
            "fraud_pattern": "LARGE_AMOUNT"
        })
    elif pattern_type == "LOCATION_ANOMALY":
        transactions.append({
            "transaction_id": str(uuid.uuid4()),
            "user_id": user["user_id"],
            "timestamp": timestamp.isoformat() + "Z",
            "amount": round(user["avg_transaction_amount"], 2),
            "location": "INTERNATIONAL", 
            "type": "RETAIL",
            "is_fraud": True,
            "fraud_pattern": "LOCATION_ANOMALY"
        })
    return transactions

def main():
    print("Loading users from Redis...")
    users = get_all_users()
    if not users:
        print("❌ No users found in Redis! Did you run setup_redis.py?")
        return
    print(f"Loaded {len(users)} users. Starting stream...")

    try:
        while True:
            user = random.choice(users)
            
            if random.random() < FRAUD_PROBABILITY:
                txs = generate_fraud_pattern(user)
                for tx in txs:
                    producer.produce(topic=KAFKA_TOPIC, value=json.dumps(tx).encode('utf-8'), callback=delivery_report)
            else:
                tx = generate_normal_transaction(user)
                producer.produce(topic=KAFKA_TOPIC, value=json.dumps(tx).encode('utf-8'), callback=delivery_report)
            
            producer.poll(0)
            time.sleep(1.0 / TRANSACTIONS_PER_SECOND)
            
    except KeyboardInterrupt:
        producer.flush()
        print("\nStopped.")

if __name__ == "__main__":
    main()