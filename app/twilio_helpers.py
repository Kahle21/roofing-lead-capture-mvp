from twilio.rest import Client
from twilio.request_validator import RequestValidator
from app.config import settings
import phonenumbers

client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
validator = RequestValidator(settings.TWILIO_AUTH_TOKEN)


def send_sms(to: str, body: str):
    return client.messages.create(
        from_=settings.TWILIO_PHONE_NUMBER,
        to=to,
        body=body
    )


def normalize_phone_number(phone: str) -> str:
    if not phone:
        return phone

    try:
        parsed = phonenumbers.parse(phone, "US")
        if phonenumbers.is_valid_number(parsed):
            return phonenumbers.format_number(
                parsed,
                phonenumbers.PhoneNumberFormat.E164
            )
    except Exception:
        pass

    return phone.strip()


def validate_twilio_request(request, form_data: dict) -> bool:
    signature = request.headers.get("X-Twilio-Signature", "")
    url = str(request.url)
    return validator.validate(url, form_data, signature)