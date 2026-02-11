# Real-Time Fraud Detection Engine

A distributed, streaming fraud detection system using Kafka, Redis, Python, XGBoost, and SHAP for explainable AI.

## Architecture
Generator → Kafka → Stateful Detector → ML Inference API → Explainability

## Stack
- Redpanda (Kafka API)
- Redis (state store)
- Python stream processor
- XGBoost fraud model
- SHAP explainability
