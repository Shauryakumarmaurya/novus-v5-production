import os
from dotenv import load_dotenv
load_dotenv()
print("GEMINI_API_KEY exists:", "GEMINI_API_KEY" in os.environ)
print("Key length:", len(os.environ.get("GEMINI_API_KEY", "")))
print("Key starts with:", os.environ.get("GEMINI_API_KEY", "")[:6])
