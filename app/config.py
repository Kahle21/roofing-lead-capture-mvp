import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
    TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
    TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER")

    OWNER_PHONE = os.getenv("OWNER_PHONE")
    BUSINESS_NAME = os.getenv("BUSINESS_NAME", "Prairie Peak Roofing")

    SUPABASE_URL = os.getenv("SUPABASE_URL")
    SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

    BASE_URL = os.getenv("BASE_URL")


settings = Settings()