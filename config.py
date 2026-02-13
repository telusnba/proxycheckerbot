import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
API_KEY = os.getenv("API_KEY")
API_USER = os.getenv("API_USER")
BASE_URL = f"https://api12.scamalytics.com/v3/{API_USER}/"
