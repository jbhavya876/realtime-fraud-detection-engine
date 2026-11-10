import redis
import random
import json
import os
import sys

# --- Configuration with Env Var Fallbacks ---
NUM_USERS = 50
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))

# Production guard: require an explicit flag to seed data
FORCE_SEED = os.getenv("FORCE_SEED", "false").lower() == "true"

# Connect to Redis
r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)

# Check if profiles already exist — avoid accidental overwrites
existing = r.keys("user_profile:*")
if existing and not FORCE_SEED:
    print(f"⚠️  Found {len(existing)} existing user profiles in Redis.")
    print("    Set FORCE_SEED=true to overwrite them.")
    print("    Exiting without changes to protect production data.")
    sys.exit(0)

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