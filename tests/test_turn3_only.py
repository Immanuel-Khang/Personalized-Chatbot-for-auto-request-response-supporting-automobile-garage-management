"""Test only up to Turn 3 to capture its trace."""
import os
import sys
import uuid

sys.stdout.reconfigure(encoding='utf-8') if hasattr(sys.stdout, 'reconfigure') else None

os.environ['LLM_PROVIDER'] = 'openai'
os.environ['DATABASE_URL'] = 'postgresql+psycopg://garage:garage@127.0.0.1:5432/garage?connect_timeout=5'
os.environ['EMBEDDING_PROVIDER'] = 'none'

from fastapi.testclient import TestClient
from app.main import app

def chat(client, session_id, message):
    response = client.post('/chat', json={'session_id': session_id, 'message': message})
    if response.status_code != 200:
        return {"error": response.text, "status": response.status_code}
    return response.json()

def main():
    client = TestClient(app)
    session_id = f"turn3-{uuid.uuid4().hex[:8]}"

    turns = [
        "Tôi muốn đặt lịch bảo dưỡng xe",
        "Sáng thứ 7 tuần sau",
        "Tôi muốn mua xe tải",
    ]

    for i, message in enumerate(turns, 1):
        print(f"Turn {i}: {message}")
        result = chat(client, session_id, message)
        if "error" in result:
            print(f"ERROR {result['status']}: {result['error']}")
        else:
            print(f"Bot: {result.get('reply', 'NO REPLY')}")
        print()

if __name__ == "__main__":
    main()
