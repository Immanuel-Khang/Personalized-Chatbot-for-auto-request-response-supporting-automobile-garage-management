
import os
from dotenv import load_dotenv
from openai import OpenAI, AuthenticationError, APIConnectionError, APIStatusError

load_dotenv()

api_key = os.getenv("OPENAI_API_KEY")
model = os.getenv("OPENAI_MODEL", "gpt-5.4-nano")

print(f"API KEY: {api_key}")
if not api_key:
    print("ERROR: OPENAI_API_KEY is missing from .env")
    raise SystemExit(1)

try:
    client = OpenAI(api_key=api_key, timeout=20.0)

    response = client.responses.create(
        model=model,
        input="Reply with exactly: API connection successful."
    )

    print("SUCCESS: OpenAI API is working.")
    print("Model:", model)
    print("Response:", response.output_text)

except AuthenticationError:
    print("ERROR: 401 Authentication failed. Check your API key.")

except APIConnectionError as e:
    print("ERROR: Could not connect to the OpenAI API.")
    print("Details:", str(e))

except APIStatusError as e:
    print(f"ERROR: API returned HTTP {e.status_code}.")
    print("Details:", str(e))
