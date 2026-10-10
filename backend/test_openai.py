
import os
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

api_key = os.getenv("LLM_API_KEY")

if not api_key:
    print("ERROR: OPENAI_API_KEY is missing from .env")
else:
    try:
        client = OpenAI(api_key=api_key)

        response = client.responses.create(
            model="gpt-5.5",
            input="Reply with exactly: OpenAI API is working!"
        )

        print("SUCCESS: API request completed!")
        print("Response:", response.output_text)

    except Exception as e:
        print("API request failed:", type(e).__name__)
        print("Details:", str(e))