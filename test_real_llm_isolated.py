"""Isolated real LLM test - each test uses fresh session."""
import os
import sys
import uuid

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

    tests = [
        ("Xin chào", "Greeting"),
        ("Tôi muốn mua xe tải", "Sales inquiry"),
        ("Tôi muốn đặt lịch bảo dưỡng xe", "Appointment request"),
    ]

    print("=" * 80)
    print("ISOLATED REAL LLM CONVERSATION TEST")
    print("Database: garage")
    print("Each test uses a fresh session")
    print("=" * 80)
    print()

    for message, description in tests:
        session_id = f"isolated-test-{uuid.uuid4().hex[:8]}"
        print(f"=== Test: {description} ===")
        print(f"Session: {session_id}")
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
    print("Check trace.json for detailed preprocessing output (last test only)")
    print("=" * 80)

if __name__ == "__main__":
    main()
