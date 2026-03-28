use rdkafka::consumer::{Consumer, StreamConsumer};
use rdkafka::ClientConfig;
use rdkafka::Message;
use redis::AsyncCommands;
use reqwest::Client;
use serde::{Deserialize, Serialize};
use std::env;
use std::time::Instant;

// --- Prometheus & Web Server Imports ---
use prometheus::{register_counter, register_histogram, Counter, Histogram, TextEncoder, Encoder};
use lazy_static::lazy_static;
use warp::Filter;

// --- Define Global Metrics ---
lazy_static! {
    static ref TX_PROCESSED: Counter = register_counter!(
        "transactions_processed_total",
        "Total transactions processed"
    ).unwrap();

    static ref FRAUD_CAUGHT: Counter = register_counter!(
        "fraud_caught_total",
        "Total fraudulent transactions caught"
    ).unwrap();

    static ref PROCESSING_TIME: Histogram = register_histogram!(
        "transaction_processing_seconds",
        "Time spent processing a transaction"
    ).unwrap();
}

#[derive(Deserialize, Debug)]
struct Transaction {
    transaction_id: String,
    user_id: String,
    amount: f64,
    location: String,
}

#[derive(Deserialize, Debug)]
struct UserProfile {
    avg_transaction_amount: f64,
    base_location: String,
}

#[derive(Serialize)]
struct MLPayload {
    amount: f64,
    user_avg_amount: f64,
    location_mismatch: i32,
    is_international: i32,
}

#[derive(Deserialize, Debug)]
struct MLResponse {
    is_fraud: bool,
    fraud_probability: f64,
    explanation: Vec<String>,
}

#[tokio::main]
async fn main() {
    println!("🚀 Starting Rust High-Performance Stream Processor...");

    // --- Start Prometheus Metrics Server in the Background ---
    let metrics_route = warp::any().map(|| {
        let encoder = TextEncoder::new();
        let mut buffer = vec![];
        let metric_families = prometheus::gather();
        encoder.encode(&metric_families, &mut buffer).unwrap();
        String::from_utf8(buffer).unwrap()
    });

    tokio::spawn(async move {
        println!("📊 Prometheus metrics server started on port 8001");
        warp::serve(metrics_route).run(([0, 0, 0, 0], 8001)).await;
    });

    // Configuration via Environment Variables
    let kafka_broker = env::var("KAFKA_BROKER").unwrap_or_else(|_| "localhost:19092".to_string());
    let kafka_topic = env::var("KAFKA_TOPIC").unwrap_or_else(|_| "raw-transactions".to_string());
    let redis_host = env::var("REDIS_HOST").unwrap_or_else(|_| "localhost".to_string());
    let redis_port = env::var("REDIS_PORT").unwrap_or_else(|_| "6379".to_string());
    let ml_service_url = env::var("ML_SERVICE_URL").unwrap_or_else(|_| "http://localhost:8000/predict".to_string());

    // Initialize Redis connection
    let redis_url = format!("redis://{}:{}", redis_host, redis_port);
    let redis_client = redis::Client::open(redis_url).expect("Invalid Redis URL");
    let mut redis_conn = redis_client.get_async_connection().await.expect("Failed to connect to Redis");

    // Initialize HTTP Client
    let http_client = Client::new();

    // Initialize Kafka Consumer
    let consumer: StreamConsumer = ClientConfig::new()
        .set("group.id", "rust-fraud-detector-group")
        .set("bootstrap.servers", &kafka_broker)
        .set("auto.offset.reset", "latest")
        .create()
        .expect("Consumer creation failed");

    consumer.subscribe(&[&kafka_topic]).expect("Can't subscribe to specified topic");
    println!("✅ Connected to Kafka, Redis, and ready to stream on topic: {}", kafka_topic);

    // Main Processing Loop
    loop {
        match consumer.recv().await {
            Err(e) => eprintln!("Kafka error: {}", e),
            Ok(m) => {
                let payload = match m.payload_view::<str>() {
                    None => continue,
                    Some(Ok(s)) => s,
                    Some(Err(e)) => {
                        eprintln!("Error deserializing message: {:?}", e);
                        continue;
                    }
                };

                // ⏱️ Start Latency Timer
                let start_time = Instant::now();

                // 📈 Increment Total Transactions Counter
                TX_PROCESSED.inc();

                let tx: Transaction = match serde_json::from_str(payload) {
                    Ok(tx) => tx,
                    Err(_) => continue,
                };

                // Fetch State from Redis
                let redis_key = format!("user_profile:{}", tx.user_id);
                let profile_json: redis::RedisResult<String> = redis_conn.get(&redis_key).await;

                let (avg_amount, base_loc) = match profile_json {
                    Ok(data) => {
                        let profile: UserProfile = serde_json::from_str(&data).unwrap();
                        (profile.avg_transaction_amount, profile.base_location)
                    }
                    Err(_) => (50.0, tx.location.clone()),
                };

                // Feature Engineering
                let location_mismatch = if tx.location != base_loc && tx.location != "INTERNATIONAL" && tx.location != "ONLINE" { 1 } else { 0 };
                let is_international = if tx.location == "INTERNATIONAL" { 1 } else { 0 };

                let ml_payload = MLPayload {
                    amount: tx.amount,
                    user_avg_amount: avg_amount,
                    location_mismatch,
                    is_international,
                };

                // Async ML Inference
                let res = http_client.post(&ml_service_url).json(&ml_payload).send().await;

                match res {
                    Ok(response) => {
                        if let Ok(ml_result) = response.json::<MLResponse>().await {
                            if ml_result.is_fraud {
                                // 📈 Increment Fraud Counter
                                FRAUD_CAUGHT.inc();
                                println!("🚨 FRAUD CAUGHT: {} | ${} | Probability: {:.2}% | Reasons: {:?}", 
                                         tx.user_id, tx.amount, ml_result.fraud_probability * 100.0, ml_result.explanation);
                            } else {
                                println!("✅ Cleared: {} | ${}", tx.user_id, tx.amount);
                            }
                        }
                    }
                    Err(e) => eprintln!("Failed to reach ML Service: {}", e),
                }

                // ⏱️ Record Processing Latency
                let duration = start_time.elapsed().as_secs_f64();
                PROCESSING_TIME.observe(duration);
            }
        };
    }
}