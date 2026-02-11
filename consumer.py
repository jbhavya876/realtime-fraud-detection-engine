import json
from confluent_kafka import Consumer

# --- Configuration ---
KAFKA_TOPIC = "raw-transactions"
KAFKA_BOOTSTRAP_SERVERS = "localhost:19092"
GROUP_ID = "fraud-detector-v1" # Consumers with the same ID share the workload

# Set up the Kafka Consumer
consumer_conf = {
    'bootstrap.servers': KAFKA_BOOTSTRAP_SERVERS,
    'group.id': GROUP_ID,
    'auto.offset.reset': 'earliest' # If starting fresh, read from the beginning of the topic
}

consumer = Consumer(consumer_conf)
consumer.subscribe([KAFKA_TOPIC])

print(f"Listening to {KAFKA_BOOTSTRAP_SERVERS} on topic '{KAFKA_TOPIC}'...")
print("Waiting for messages... (Press Ctrl+C to stop)")

try:
    while True:
        # Poll the broker for new messages, wait up to 1 second
        msg = consumer.poll(1.0) 

        if msg is None:
            continue
        if msg.error():
            print(f"Consumer error: {msg.error()}")
            continue

        # Decode the byte payload back into a Python dictionary
        transaction = json.loads(msg.value().decode('utf-8'))
        
        # Let's format the output so the anomalies jump out at us
        if transaction.get('is_fraud'):
            print(f"🚨 FRAUD INJECTED [{transaction['fraud_pattern']}]: User {transaction['user_id']} | Amount: ${transaction['amount']}")
        else:
            print(f"✅ Normal Tx: User {transaction['user_id']} | Amount: ${transaction['amount']}")

except KeyboardInterrupt:
    print("\nClosing consumer connection...")
finally:
    # Always close the consumer cleanly so it leaves the consumer group gracefully
    consumer.close()