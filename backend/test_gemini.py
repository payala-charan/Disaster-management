
import os
from dotenv import load_dotenv
from google import genai

load_dotenv()

api_key = os.getenv("GEMINI_API_KEY")

if not api_key:
    print("ERROR: GEMINI_API_KEY not found in .env")
else:
    try:
        client = genai.Client(api_key=api_key)

        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents="Reply with exactly: Gemini API is working!"
        )

        print("SUCCESS: Gemini API is working!")
        print("Response:", response.text)

    except Exception as e:
        print("FAILED:", type(e).__name__)
        print("Details:", str(e))