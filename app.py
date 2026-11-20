import os
from flask import Flask, jsonify, render_template, request
from google import genai
from google.genai import types

app = Flask(__name__)

# Railway-এর Variables থেকে নেবে, না পেলে কোডে দেওয়া Key ব্যবহার করবে
API_KEY = os.environ.get("GEMINI_API_KEY") or "AQ.Ab8RN6LHX4rF_tCaWdi-Vnn7IuzU3uP23G3wpwT7yQJlw4ErJQ"

client = genai.Client(api_key=API_KEY)

SYSTEM_PROMPT = """
তুমি 'SR FREE AI' ওয়েবসাইটের একজন বুদ্ধিমান, আন্তরিক ও বন্ধুসুলভ AI সহকারী।
ইউজার চ্যাটে যা লিখবে তা মনোযোগ দিয়ে বুঝে বিবেচনা করবে এবং সঠিক ও সহজ উত্তর দেবে।
ইউজার বাংলায় লিখলে সাবলীল বাংলায়, ইংরেজিতে লিখলে ইংরেজিতে, আর বাংলিশে লিখলে সহজ বাংলায় উত্তর দেবে।
দরকার হলে পয়েন্ট বা ছোট উদাহরণ দিয়ে বুঝিয়ে বলবে, অযথা কথা বাড়াবে না।
"""

MODELS = ["gemini-3.8-flash", "gemini-3.5-flash-lite"]

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/chat", methods=["POST"])
def chat():
    data = request.get_json() or {}
    history = data.get("history", [])

    if not history:
        return jsonify({"error": "মেসেজ খালি রাখা যাবে না।"}), 400

    contents = []
    for msg in history[-12:]:
        role = "user" if msg.get("role") == "user" else "model"
        text = str(msg.get("text", "")).strip()
        if text:
            contents.append(
                types.Content(
                    role=role,
                    parts=[types.Part.from_text(text=text)]
                )
            )

    if not contents:
        return jsonify({"error": "মেসেজ খালি রাখা যাবে না।"}), 400

    last_error = None
    for model_name in MODELS:
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    temperature=0.7,
                ),
            )
            reply_text = response.text or "দুঃখিত, এই মুহূর্তে উত্তর তৈরি করা সম্ভব হয়নি।"
            return jsonify({"reply": reply_text})

        except Exception as e:
            last_error = e
            err_str = str(e)
            if "503" in err_str or "UNAVAILABLE" in err_str or "high demand" in err_str.lower():
                continue
            break

    return jsonify({"error": f"API এরর: {str(last_error)}"}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
