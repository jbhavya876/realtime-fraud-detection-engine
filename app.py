import streamlit as st
import pandas as pd
import requests
import os
import time

# --- Configuration ---
# Targets for Docker networking
ML_SERVICE_URL = os.getenv("ML_SERVICE_URL_BATCH", "http://ml-service:8000/batch-analyze")
PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://prometheus:9090")

st.set_page_config(page_title="FraudOps Portal", page_icon="🛡️", layout="wide")
st.title("🛡️ FraudOps Command Center")

# --- Helper Functions for Prometheus ---
def fetch_current_metric(query):
    """Fetches a single, current value from Prometheus."""
    try:
        response = requests.get(f"{PROMETHEUS_URL}/api/v1/query", params={'query': query}, timeout=2)
        data = response.json()
        if data['status'] == 'success' and data['data']['result']:
            return float(data['data']['result'][0]['value'][1])
    except Exception as e:
        pass
    return 0.0

def fetch_timeseries(query, step="5s"):
    """Fetches historical data for line charts (last 5 minutes)."""
    try:
        now = time.time()
        start = now - 300 # 5 minutes ago
        response = requests.get(
            f"{PROMETHEUS_URL}/api/v1/query_range", 
            params={'query': query, 'start': start, 'end': now, 'step': step},
            timeout=2
        )
        data = response.json()
        if data['status'] == 'success' and data['data']['result']:
            values = data['data']['result'][0]['values']
            df = pd.DataFrame(values, columns=['Time', 'Value'])
            df['Time'] = pd.to_datetime(df['Time'], unit='s')
            df['Value'] = df['Value'].astype(float)
            return df.set_index('Time')
    except Exception as e:
        pass
    return pd.DataFrame()

# --- UI Layout ---
tab1, tab2 = st.tabs(["📈 Live System Monitoring", "📁 Batch Document Analysis"])

# ==========================================
# TAB 1: REAL-TIME GRAFANA CLONE
# ==========================================
with tab1:
    st.markdown("### Real-Time Streaming Pipeline Telemetry")
    
    # Auto-refresh toggle
    auto_refresh = st.checkbox("Enable Live Auto-Refresh (3s)", value=False)
    
    # Fetch top-level KPIs
    tx_total = fetch_current_metric('transactions_processed_total')
    fraud_total = fetch_current_metric('fraud_caught_total')
    current_tps = fetch_current_metric('rate(transactions_processed_total[1m])')
    
    # Display Top Metrics
    col1, col2, col3 = st.columns(3)
    col1.metric("Total Transactions Evaluated", int(tx_total))
    col2.metric("Fraudulent Patterns Caught", int(fraud_total))
    col3.metric("Current Pipeline Throughput", f"{current_tps:.2f} TPS")
    
    st.divider()
    
    # Fetch and Display Charts
    col_chart1, col_chart2 = st.columns(2)
    
    with col_chart1:
        st.markdown("**Transactions Per Second (Last 5 Min)**")
        df_tps = fetch_timeseries('rate(transactions_processed_total[1m])')
        if not df_tps.empty:
            st.line_chart(df_tps, color="#4CAF50")
        else:
            st.info("Waiting for Prometheus telemetry...")

    with col_chart2:
        st.markdown("**Fraud Detection Rate (Last 5 Min)**")
        # Query: Fraud / Total = Rate
        query_rate = 'rate(fraud_caught_total[1m]) / rate(transactions_processed_total[1m])'
        df_rate = fetch_timeseries(query_rate)
        if not df_rate.empty:
            st.line_chart(df_rate, color="#FF5252")
        else:
            st.info("Waiting for Prometheus telemetry...")
            
    # The Auto-Refresh Logic
    if auto_refresh:
        time.sleep(3)
        st.rerun()

# ==========================================
# TAB 2: BATCH CSV ANALYSIS
# ==========================================
with tab2:
    st.markdown("### Agentic AI Document Analysis")
    st.markdown("Upload a CSV of transactions to run them through our Explainable AI fraud detection engine.")

    uploaded_file = st.file_uploader("Upload Transaction CSV", type=["csv"])

    if uploaded_file is not None:
        st.info("File uploaded successfully. Routing to XAI Inference Engine...")
        
        with st.spinner('Analyzing behavior baselines & evaluating risk...'):
            try:
                files = {"file": (uploaded_file.name, uploaded_file.getvalue(), "text/csv")}
                # Note: We must use the exact endpoint name we configured in FastAPI
                response = requests.post(ML_SERVICE_URL, files=files)
                
                if response.status_code == 200:
                    data = response.json().get("analyzed_transactions", [])
                    if data:
                        df_results = pd.DataFrame(data)
                        
                        col1, col2, col3 = st.columns(3)
                        col1.metric("Total Evaluated", len(df_results))
                        col2.metric("Anomalies Detected", df_results['is_fraud'].sum())
                        col3.metric("Model Used", "XGBoost + SHAP")
                        
                        # Highlighting function
                        def highlight_fraud(row):
                            if row['is_fraud']:
                                return ['background-color: rgba(255, 82, 82, 0.2); color: #FF5252; font-weight: bold'] * len(row)
                            return [''] * len(row)
                        
                        st.dataframe(
                            df_results.style.apply(highlight_fraud, axis=1),
                            use_container_width=True,
                            height=400
                        )
                        
                        # Export Button
                        csv_export = df_results.to_csv(index=False).encode('utf-8')
                        st.download_button(
                            label="📥 Download Annotated Report",
                            data=csv_export,
                            file_name='fraud_analysis_report.csv',
                            mime='text/csv',
                        )
                    else:
                        st.warning("Analysis returned no data.")
                else:
                    st.error(f"Error from API: HTTP {response.status_code} - {response.text}")
                    
            except Exception as e:
                st.error(f"Failed to connect to ML Service. Error: {e}")