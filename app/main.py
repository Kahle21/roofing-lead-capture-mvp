from fastapi import FastAPI, Form, Request, HTTPException
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from xml.sax.saxutils import escape

from app.config import settings
from app.db import supabase
from app.twilio_helpers import (
    send_sms,
    normalize_phone_number,
    validate_twilio_request,
)

app = FastAPI()
templates = Jinja2Templates(directory="app/templates")


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/voice/incoming")
async def voice_incoming(
    request: Request,
    CallSid: str = Form(...),
    From: str = Form(...),
    To: str = Form(...),
):
    full_form = await request.form()
    form_data = dict(full_form)

    if not validate_twilio_request(request, form_data):
        raise HTTPException(status_code=403, detail="Invalid Twilio signature")

    caller_phone = normalize_phone_number(From)
    twilio_number = normalize_phone_number(To)
    owner_phone = normalize_phone_number(settings.OWNER_PHONE)

    supabase.table("leads").upsert(
        {
            "call_sid": CallSid,
            "caller_phone": caller_phone,
            "twilio_number": twilio_number,
            "owner_phone": owner_phone,
            "business_name": settings.BUSINESS_NAME,
            "call_status": "incoming",
            "source": "voice",
        },
        on_conflict="call_sid",
    ).execute()

    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Dial timeout="20" action="{settings.BASE_URL}/voice/dial-status" method="POST">
        <Number>{owner_phone}</Number>
    </Dial>
</Response>"""

    return Response(content=twiml, media_type="application/xml")


@app.post("/voice/dial-status")
async def voice_dial_status(
    request: Request,
    DialCallStatus: str = Form(None),
    CallSid: str = Form(...),
    From: str = Form(...),
):
    full_form = await request.form()
    form_data = dict(full_form)

    if not validate_twilio_request(request, form_data):
        raise HTTPException(status_code=403, detail="Invalid Twilio signature")

    caller_phone = normalize_phone_number(From)
    owner_phone = normalize_phone_number(settings.OWNER_PHONE)

    missed_statuses = {"no-answer", "busy", "failed", "canceled"}
    was_missed = DialCallStatus in missed_statuses

    lead_result = (
        supabase.table("leads")
        .select("*")
        .eq("call_sid", CallSid)
        .limit(1)
        .execute()
    )

    lead = lead_result.data[0] if lead_result.data else None

    supabase.table("leads").update(
        {
            "call_status": DialCallStatus,
            "was_missed": was_missed,
        }
    ).eq("call_sid", CallSid).execute()

    if was_missed and lead:
        if not lead.get("missed_sms_sent"):
            caller_text = (
                f"Hi, this is {settings.BUSINESS_NAME}. "
                "Sorry we missed your call. What do you need help with today?"
            )
            send_sms(caller_phone, caller_text)

            supabase.table("leads").update(
                {"missed_sms_sent": True}
            ).eq("call_sid", CallSid).execute()

        if not lead.get("owner_alert_sent"):
            owner_text = f"Missed call from {caller_phone}. Intake text sent."
            send_sms(owner_phone, owner_text)

            supabase.table("leads").update(
                {"owner_alert_sent": True}
            ).eq("call_sid", CallSid).execute()

        spoken_name = escape(settings.BUSINESS_NAME)
        twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="alice">
        Sorry we missed your call to {spoken_name}. We have sent you a text message to collect your details.
    </Say>
    <Hangup />
</Response>"""

        return Response(content=twiml, media_type="application/xml")

    twiml = """<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Hangup />
</Response>"""

    return Response(content=twiml, media_type="application/xml")


@app.post("/sms/incoming")
async def sms_incoming(
    request: Request,
    From: str = Form(...),
    To: str = Form(...),
    Body: str = Form(...),
):
    full_form = await request.form()
    form_data = dict(full_form)

    if not validate_twilio_request(request, form_data):
        raise HTTPException(status_code=403, detail="Invalid Twilio signature")

    caller_phone = normalize_phone_number(From)
    owner_phone = normalize_phone_number(settings.OWNER_PHONE)

    text = Body.strip()

    result = (
        supabase.table("leads")
        .select("*")
        .eq("caller_phone", caller_phone)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )

    if not result.data:
        twiml = """<?xml version="1.0" encoding="UTF-8"?>
<Response></Response>"""
        return Response(content=twiml, media_type="application/xml")

    lead = result.data[0]
    stage = lead["sms_stage"]

    reply = ""
    updates = {}

    if stage == "new":
        updates["service_needed"] = text
        updates["sms_stage"] = "awaiting_urgency"
        reply = "Thanks — is this urgent or related to storm damage?"

    elif stage == "awaiting_urgency":
        updates["urgency"] = text
        updates["sms_stage"] = "awaiting_location"
        reply = "What city or postal code is the job in?"

    elif stage == "awaiting_location":
        updates["location_text"] = text
        updates["sms_stage"] = "awaiting_name"
        reply = "Thanks — what’s your name?"

    elif stage == "awaiting_name":
        updates["customer_name"] = text
        updates["sms_stage"] = "complete"

        summary = (
            f"New {settings.BUSINESS_NAME} lead\n\n"
            f"Phone: {caller_phone}\n"
            f"Name: {text}\n"
            f"Service: {lead.get('service_needed') or ''}\n"
            f"Urgency: {lead.get('urgency') or ''}\n"
            f"Location: {lead.get('location_text') or ''}"
        )

        updates["summary_text"] = summary
        updates["summary_sent"] = True
        reply = (
            f"Thanks, {text}. We’ve sent your request to "
            f"{settings.BUSINESS_NAME} and someone will follow up soon."
        )

        if not lead.get("summary_sent"):
            send_sms(owner_phone, summary)

    elif stage == "complete":
        reply = (
            f"Thanks — we already received your request and sent it to "
            f"{settings.BUSINESS_NAME}."
        )

    else:
        reply = (
            f"Hi, this is {settings.BUSINESS_NAME}. "
            "What do you need help with today?"
        )

    if updates:
        (
            supabase.table("leads")
            .update(updates)
            .eq("id", lead["id"])
            .execute()
        )

    safe_reply = escape(reply)

    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Message>{safe_reply}</Message>
</Response>"""

    return Response(content=twiml, media_type="application/xml")


@app.get("/leads", response_class=HTMLResponse)
def leads_page(request: Request):
    result = (
        supabase.table("leads")
        .select("*")
        .order("created_at", desc=True)
        .limit(100)
        .execute()
    )

    return templates.TemplateResponse(
        "leads.html",
        {"request": request, "leads": result.data},
    )