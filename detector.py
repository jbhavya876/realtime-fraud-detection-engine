import json
import redis
import time
from datetime import datetime
from confluent_kafka import Consumer, Producer
import requests

# --- Configuration ---
KAFKA_BOOTSTRAP_SERVERS = "localhost:19092"
IN_TOPIC = "raw-transactions"
OUT_TOPIC = "evaluated-transactions"
REDIS_HOST = "localhost"
REDIS_PORT = 6379

# Set up Redis
r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)

# Set up Kafka Consumer
consumer = Consumer({
    'bootstrap.servers': KAFKA_BOOTSTRAP_SERVERS,
    'group.id': 'fraud-detector-group',
    'auto.offset.reset': 'latest'
})
consumer.subscribe([IN_TOPIC])

# Set up Kafka Producer
producer = Producer({'bootstrap.servers': KAFKA_BOOTSTRAP_SERVERS})

def delivery_report(err, msg):
    if err is not None:
        print(f"Delivery failed: {err}")

def parse_time(iso_str):
    return datetime.fromisoformat(iso_str.replace('Z', ''))

def process_transaction(tx):
    start_time = time.time() # Latency tracking start
    
    user_id = tx['user_id']
    amount = tx['amount']
    tx_time = parse_time(tx['timestamp'])
    
    # 1. Fetch User Profile & Handle Cold Start
    profile_json = r.get(f"user_profile:{user_id}")
    
    if not profile_json:
        # Fallback heuristic for new/unknown users
        avg_amount = 50.0 
        base_location = tx['location'] 
    else:
        profile = json.loads(profile_json)
        avg_amount = profile['avg_transaction_amount']
        base_location = profile['base_location']
    
    # 2. Update and Check Velocity via Redis
    history_key = f"tx_history:{user_id}"
    
    r.lpush(history_key, tx_time.timestamp())
    r.ltrim(history_key, 0, 4)
    # Critical Improvement: Prevent Memory Leaks
    r.expire(history_key, 3600) 
    
    history = r.lrange(history_key, 0, -1)
    
    # --- The Detection Logic ---
    is_fraud = False
    reasons = []
    
    # Check A: Large Amount Anomaly
    if amount > (avg_amount * 5):
        is_fraud = True
        reasons.append(f"Amount ${amount} is > 5x historical average (${avg_amount}).")
        
    # Check B: Velocity Anomaly
    if len(history) == 5:
        oldest_tx_time = float(history[-1])
        newest_tx_time = float(history[0])
        time_diff_seconds = newest_tx_time - oldest_tx_time
        
        if time_diff_seconds < 60:
            is_fraud = True
            reasons.append(f"Velocity alert: 5 transactions in {time_diff_seconds:.1f} seconds.")
            
    # Check C: Location Anomaly
    location_mismatch = 1 if tx['location'] != base_location and tx['location'] not in ['INTERNATIONAL', 'ONLINE'] else 0
    is_international = 1 if tx['location'] == 'INTERNATIONAL' else 0

    payload = {
        "amount": amount,
        "user_avg_amount": avg_amount,
        "location_mismatch": location_mismatch,
        "is_international": is_international
    }

    try:
        # We use a short timeout. In real-time systems, it's better to fail fast than hang.
        response = requests.post("http://localhost:8000/predict", json=payload, timeout=0.1)
        if response.status_code == 200:
            ml_result = response.json()
            is_fraud = ml_result['is_fraud']
            reasons = ml_result['explanation']
            fraud_prob = ml_result['fraud_probability']
        else:
            print(f"ML Service Error: {response.status_code}")
            is_fraud = False
            reasons = ["ML Service Unavailable"]
    except requests.exceptions.Timeout:
         print("ML Service Timeout!")
         is_fraud = False
         reasons = ["Timeout"]

    # --- Prepare Output ---
    output_event = tx.copy()
    output_event['system_flagged_fraud'] = is_fraud
    output_event['reasons'] = reasons
    
    latency_ms = (time.time() - start_time) * 1000 # Latency tracking end
    
    if is_fraud:
        print(f"🚨 FRAUD CAUGHT: {user_id} | ${amount} | Latency: {latency_ms:.2f}ms | Reasons: {reasons}")
    else:
        print(f"✅ Cleared: {user_id} | ${amount} | Latency: {latency_ms:.2f}ms")
        
    # Critical Improvement: Key-based routing for partitions
    producer.produce(
        topic=OUT_TOPIC, 
        key=user_id.encode('utf-8'), 
        value=json.dumps(output_event).encode('utf-8'), 
        callback=delivery_report
    )
    producer.poll(0)

print("Starting Hardened Real-Time Fraud Detector...")
try:
    while True:
        msg = consumer.poll(1.0)
        if msg is None: continue
        if msg.error():
            print(f"Consumer error: {msg.error()}")
            continue
            
        tx = json.loads(msg.value().decode('utf-8'))
        process_transaction(tx)
        
except KeyboardInterrupt:
    print("\nShutting down gracefully...")
finally:
    consumer.close()
    # Critical Improvement: Guaranteed flush timeout
    producer.flush(timeout=5.0) 
    print("Shutdown complete.")