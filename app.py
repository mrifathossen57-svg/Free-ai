import os
from flask import Flask, jsonify, render_template, request
from google import genai
from google.genai import types

app = Flask(__name__)

# Railway-এর Variables ট্যাবে GEMINI_API_KEY নামে Key সেট করতে হবে
API_KEY = os.environ.get("GEMINI_API_KEY")
if not API_KEY:
  raise RuntimeError("GEMINI_API_KEY Environment Variable সেট করা নেই!")

client = genai.Client(api_key=API_KEY)

SYSTEM_PROMPT = """
তুমি 'SR FREE AI' ওয়েবসাইটের একজন বুদ্ধিমান, আন্তরিক ও বন্ধুসুলভ AI সহকারী।
ইউজার চ্যাটে যা লিখবে তা মনোযোগ দিয়ে বুঝে বিবেচনা করবে এবং সঠিক ও সহজ উত্তর দেবে।
ইউজার বাংলায় লিখলে সাবলীল বাংলায়, ইংরেজিতে লিখলে ইংরেজিতে, আর বাংলিশে লিখলে সহজ বাংলায় উত্তর দেবে।
দরকার হলে পয়েন্ট বা ছোট উদাহরণ দিয়ে বুঝিয়ে বলবে, অযথা কথা বাড়াবে না।
"""


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
          types.Content(role=role, parts=[types.Part.from_text(text=text)])
      )

  try:
    response = client.models.generate_content(
        model="gemini-flash-latest",
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            temperature=0.7,
        ),
    )
    reply_text = (
        response.text or "দুঃখিত, এই মুহূর্তে উত্তর তৈরি করা সম্ভব হয়নি।"
    )
    return jsonify({"reply": reply_text})

  except Exception as e:
    return jsonify({"error": f"API এরর: {str(e)}"}), 500


if __name__ == "__main__":
  port = int(os.environ.get("PORT", 5000))
  app.run(host="0.0.0.0", port=port)
