"""Real LLM integration test using garage database."""
import os
import sys

# Force UTF-8 output
sys.stdout.reconfigure(encoding='utf-8') if hasattr(sys.stdout, 'reconfigure') else None

# Set environment before importing app
os.environ['LLM_PROVIDER'] = 'openai'
os.environ['DATABASE_URL'] = 'postgresql+psycopg://garage:garage@127.0.0.1:5432/garage?connect_timeout=5'
os.environ['EMBEDDING_PROVIDER'] = 'none'

from fastapi.testclient import TestClient
from app.main import app
import json

def main():
    client = TestClient(app)
    session_id = 'real-llm-test-session'

    tests = [
        ("Xin chào", "Greeting"),
        ("Tôi muốn mua xe tải", "Sales inquiry"),
        ("Tôi muốn đặt lịch bảo dưỡng xe", "Appointment request"),
    ]

    print("=" * 80)
    print("REAL LLM CONVERSATION TEST")
    print("Database: garage")
    print("Session ID:", session_id)
    print("=" * 80)
    print()

    for message, description in tests:
        print(f"=== Test: {description} ===")
        print(f"User: {message}")

        response = client.post('/chat', json={
            'session_id': session_id,
            'message': message
        })

        print(f"Status: {response.status_code}")

        if response.status_code == 200:
            result = response.json()
            print(f"Bot: {result.get('reply', 'NO REPLY')}")
            print(f"Mode: {result.get('mode', 'UNKNOWN')}")
            if 'trace' in result:
                print(f"Trace: {', '.join(result['trace'])}")
        else:
            print(f"Error: {response.text}")

        print()

    print("=" * 80)
    print("Check trace.json for detailed preprocessing output")
    print("=" * 80)

if __name__ == "__main__":
    main()
