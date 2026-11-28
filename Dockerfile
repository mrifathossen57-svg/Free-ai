FROM python:3.13-slim

# Node.js এবং npm ইনস্টল করা
RUN apt-get update && apt-get install -y nodejs npm && rm -rf /var/lib/apt/lists/*

# ওয়ার্কিং ডিরেক্টরি সেট করা
WORKDIR /app

# প্রজেক্টের সব ফাইল কপি করা
COPY . /app

# পাইথনের প্যাকেজগুলো অটো ইনস্টল হবে
RUN if [ -f requirements.txt ]; then pip install --no-cache-dir -r requirements.txt; fi

# Node.js এর প্যাকেজগুলোও (md5, telegram bot ইত্যাদি) অটো ইনস্টল হবে
RUN if [ -f package.json ]; then npm install; fi

# বট রান করার কমান্ড
CMD ["python", "main.py"]
