from twilio.rest import Client
from app.config import settings

client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)


def send_sms(to: str, body: str):
    return client.messages.create(
        from_=settings.TWILIO_PHONE_NUMBER,
        to=to,
        body=body
    )