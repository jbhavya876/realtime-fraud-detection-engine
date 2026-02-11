import redis
import random
import json

# --- Configuration ---
NUM_USERS = 50
REDIS_HOST = "localhost"
REDIS_PORT = 6379

# Connect to Redis
r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)

print("Connected to Redis. Clearing old data...")
r.flushall() # Clean slate for our simulation

print(f"Generating profiles for {NUM_USERS} users...")

users_created = []

for i in range(NUM_USERS):
    user_id = f"usr_{i:03d}"
    
    # Create a realistic profile
    profile = {
        "user_id": user_id,
        "base_location": random.choice(["NY", "CA", "TX", "FL", "IL"]),
        "avg_transaction_amount": round(random.uniform(10.0, 150.0), 2),
        "std_dev_amount": round(random.uniform(2.0, 15.0), 2) # Volatility of their spending
    }
    
    # Save to Redis as a Hash (Key: "usr_001", Field: "profile", Value: JSON string)
    # In production, you might use specific fields like "hset(user_id, mapping=profile)"
    r.set(f"user_profile:{user_id}", json.dumps(profile))
    
    users_created.append(profile)

print(f"✅ Successfully loaded {len(users_created)} user profiles into Redis.")
print("Sample User 0:", r.get("user_profile:usr_000"))