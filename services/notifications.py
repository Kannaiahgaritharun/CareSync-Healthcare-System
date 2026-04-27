"""
CureSync Notification Service
Handles multi-channel alerts: In-App, SMS (Twilio), WhatsApp, Email (SMTP)
Set MOCK_MODE=true in .env to simulate without sending real messages.
"""

import os
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

from dotenv import load_dotenv
load_dotenv()

logger = logging.getLogger("caresync.notifications")

MOCK_MODE = os.getenv("MOCK_MODE", "true").lower() == "true"

# ─── Twilio client (lazy-loaded) ───────────────────────────────────────────
_twilio_client = None

def _get_twilio():
    global _twilio_client
    if _twilio_client is None:
        try:
            from twilio.rest import Client
            sid = os.getenv("TWILIO_ACCOUNT_SID", "")
            token = os.getenv("TWILIO_AUTH_TOKEN", "")
            if sid and token and sid != "your_twilio_account_sid_here":
                _twilio_client = Client(sid, token)
        except Exception as e:
            logger.warning(f"Twilio init failed: {e}")
    return _twilio_client


# ─── SMS ───────────────────────────────────────────────────────────────────
def send_sms(to_number: str, message: str) -> dict:
    """Send SMS via Twilio. Falls back to mock if not configured."""
    if not to_number:
        return {"success": False, "error": "No phone number"}

    if MOCK_MODE:
        logger.info(f"[MOCK SMS] to {to_number}: {message}")
        print(f"\n{'='*60}")
        print(f"[MOCK SMS] To: {to_number}")
        print(f"   {message}")
        print(f"{'='*60}\n")
        return {"success": True, "mock": True}

    client = _get_twilio()
    if not client:
        logger.error("Twilio not configured. Set MOCK_MODE=false and provide credentials.")
        return {"success": False, "error": "Twilio not configured"}

    try:
        msg = client.messages.create(
            body=message,
            from_=os.getenv("TWILIO_PHONE_NUMBER"),
            to=to_number
        )
        logger.info(f"SMS sent to {to_number}, SID: {msg.sid}")
        return {"success": True, "sid": msg.sid}
    except Exception as e:
        logger.error(f"SMS failed to {to_number}: {e}")
        return {"success": False, "error": str(e)}


# ─── WhatsApp ──────────────────────────────────────────────────────────────
def send_whatsapp(to_number: str, message: str) -> dict:
    """Send WhatsApp message via Twilio WhatsApp sandbox."""
    if not to_number:
        return {"success": False, "error": "No phone number"}

    if MOCK_MODE:
        logger.info(f"[MOCK WHATSAPP] to {to_number}: {message}")
        print(f"\n{'='*60}")
        print(f"[MOCK WHATSAPP] To: {to_number}")
        print(f"   {message}")
        print(f"{'='*60}\n")
        return {"success": True, "mock": True}

    client = _get_twilio()
    if not client:
        return {"success": False, "error": "Twilio not configured"}

    try:
        whatsapp_from = os.getenv("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886")
        to_wa = f"whatsapp:{to_number}" if not to_number.startswith("whatsapp:") else to_number
        msg = client.messages.create(
            body=message,
            from_=whatsapp_from,
            to=to_wa
        )
        logger.info(f"WhatsApp sent to {to_number}, SID: {msg.sid}")
        return {"success": True, "sid": msg.sid}
    except Exception as e:
        logger.error(f"WhatsApp failed to {to_number}: {e}")
        return {"success": False, "error": str(e)}


# ─── Email ─────────────────────────────────────────────────────────────────
def send_email(to_email: str, subject: str, html_body: str) -> dict:
    """Send email via SMTP (Gmail or any SMTP server)."""
    if not to_email:
        return {"success": False, "error": "No email address"}

    if MOCK_MODE:
        logger.info(f"[MOCK EMAIL] to {to_email}: {subject}")
        print(f"\n{'='*60}")
        print(f"[MOCK EMAIL] To: {to_email}")
        print(f"   Subject: {subject}")
        print(f"   Body: {html_body[:200]}...")
        print(f"{'='*60}\n")
        return {"success": True, "mock": True}

    smtp_server = os.getenv("SMTP_SERVER", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_email = os.getenv("SMTP_EMAIL", "")
    smtp_password = os.getenv("SMTP_PASSWORD", "")
    sender_name = os.getenv("SMTP_SENDER_NAME", "CareSync Health")

    if not smtp_email or smtp_email == "your_email@gmail.com":
        logger.error("SMTP not configured.")
        return {"success": False, "error": "SMTP not configured"}

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"{sender_name} <{smtp_email}>"
        msg["To"] = to_email
        msg.attach(MIMEText(html_body, "html"))

        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(smtp_email, smtp_password)
            server.sendmail(smtp_email, to_email, msg.as_string())

        logger.info(f"Email sent to {to_email}")
        return {"success": True}
    except Exception as e:
        logger.error(f"Email failed to {to_email}: {e}")
        return {"success": False, "error": str(e)}


# ─── Medicine Reminder Message Builders ────────────────────────────────────
def build_reminder_sms(medicine_name: str, dosage: str, time_str: str, repeat_num: int = 0) -> str:
    prefix = ""
    if repeat_num == 1:
        prefix = "[REMINDER] "
    elif repeat_num >= 2:
        prefix = "[FINAL REMINDER] "
    return f"{prefix}CareSync: Take {medicine_name} {dosage} now at {time_str}. Please mark it as taken on the app."


def build_reminder_email_html(user_name: str, medicine_name: str, dosage: str,
                               food_instruction: str, time_str: str, repeat_num: int = 0) -> str:
    repeat_banner = ""
    if repeat_num == 1:
        repeat_banner = '<div style="background:#fff3cd;padding:10px;border-radius:8px;margin-bottom:16px;"><b>⚠️ This is a follow-up reminder (10 minutes overdue)</b></div>'
    elif repeat_num >= 2:
        repeat_banner = '<div style="background:#f8d7da;padding:10px;border-radius:8px;margin-bottom:16px;"><b>🚨 FINAL REMINDER — Medicine is 30 minutes overdue!</b></div>'

    return f"""
    <div style="font-family:Arial,sans-serif;max-width:600px;margin:auto;background:#f8f9fa;padding:24px;border-radius:12px;">
      <div style="background:linear-gradient(135deg,#667eea,#764ba2);padding:20px;border-radius:10px;text-align:center;margin-bottom:24px;">
        <h1 style="color:#fff;margin:0;font-size:24px;">💊 CareSync</h1>
        <p style="color:rgba(255,255,255,0.85);margin:4px 0 0 0;">Medicine Reminder</p>
      </div>
      {repeat_banner}
      <p style="color:#333;font-size:16px;">Hello <b>{user_name}</b>,</p>
      <p style="color:#555;">This is your scheduled reminder to take your medicine.</p>
      <div style="background:#fff;border:2px solid #667eea;border-radius:10px;padding:20px;margin:20px 0;">
        <table style="width:100%;border-collapse:collapse;">
          <tr><td style="padding:8px;color:#888;width:40%;">💊 Medicine</td><td style="padding:8px;font-weight:bold;color:#333;">{medicine_name}</td></tr>
          <tr style="background:#f8f9fa;"><td style="padding:8px;color:#888;">📏 Dosage</td><td style="padding:8px;font-weight:bold;color:#333;">{dosage}</td></tr>
          <tr><td style="padding:8px;color:#888;">⏰ Time</td><td style="padding:8px;font-weight:bold;color:#667eea;">{time_str}</td></tr>
          <tr style="background:#f8f9fa;"><td style="padding:8px;color:#888;">🍽️ Instruction</td><td style="padding:8px;color:#333;">{food_instruction or 'As prescribed'}</td></tr>
        </table>
      </div>
      <div style="text-align:center;margin:24px 0;">
        <a href="http://127.0.0.1:5000/patient_dashboard" style="background:linear-gradient(135deg,#667eea,#764ba2);color:#fff;padding:14px 32px;border-radius:25px;text-decoration:none;font-weight:bold;font-size:16px;">Mark as Taken ✓</a>
      </div>
      <p style="color:#999;font-size:12px;text-align:center;">CareSync Health Platform • Your Health, Our Priority</p>
    </div>
    """


def build_sos_message(user_name: str, location_link: str = None) -> str:
    loc = f"\n📍 Location: {location_link}" if location_link else ""
    return f"🚨 EMERGENCY SOS from CareSync!\n\n{user_name} needs immediate help.{loc}\n\nPlease contact them immediately or call emergency services."


def build_sos_email_html(user_name: str, user_email: str, user_phone: str, location_link: str = None) -> str:
    loc_row = f'<tr><td style="padding:8px;color:#888;">📍 Location</td><td style="padding:8px;"><a href="{location_link}">{location_link}</a></td></tr>' if location_link else ""
    return f"""
    <div style="font-family:Arial,sans-serif;max-width:600px;margin:auto;background:#fff5f5;padding:24px;border-radius:12px;border:2px solid #dc3545;">
      <div style="background:#dc3545;padding:20px;border-radius:10px;text-align:center;margin-bottom:24px;">
        <h1 style="color:#fff;margin:0;font-size:28px;">🚨 EMERGENCY SOS</h1>
        <p style="color:rgba(255,255,255,0.9);margin:4px 0 0 0;">CareSync Health Platform</p>
      </div>
      <p style="color:#333;font-size:18px;font-weight:bold;">Immediate assistance required!</p>
      <p style="color:#555;">{user_name} has triggered an Emergency SOS alert and may need immediate help.</p>
      <div style="background:#fff;border:1px solid #dc3545;border-radius:10px;padding:20px;margin:20px 0;">
        <table style="width:100%;border-collapse:collapse;">
          <tr><td style="padding:8px;color:#888;">👤 Name</td><td style="padding:8px;font-weight:bold;color:#333;">{user_name}</td></tr>
          <tr style="background:#fff5f5;"><td style="padding:8px;color:#888;">📧 Email</td><td style="padding:8px;">{user_email}</td></tr>
          <tr><td style="padding:8px;color:#888;">📞 Phone</td><td style="padding:8px;">{user_phone or 'Not provided'}</td></tr>
          {loc_row}
        </table>
      </div>
      <p style="color:#dc3545;font-weight:bold;text-align:center;font-size:16px;">Please contact them immediately or call emergency services.</p>
    </div>
    """


# ─── SOS Dispatcher ────────────────────────────────────────────────────────
def send_sos_alert(user_id: int, location_link: str = None, db_conn=None) -> list:
    """
    Dispatch SOS alerts to all emergency contacts of the user.
    Returns list of delivery results.
    """
    if db_conn is None:
        logger.error("No DB connection provided to send_sos_alert")
        return []

    cursor = db_conn.cursor()
    user = cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    contacts = cursor.execute(
        "SELECT * FROM emergency_contacts WHERE user_id = ? ORDER BY priority_order ASC",
        (user_id,)
    ).fetchall()

    if not user:
        return []

    user_name = user["name"]
    user_email = user["email"]
    user_phone = user["phone_number"] or ""

    results = []
    sms_msg = build_sos_message(user_name, location_link)
    email_html = build_sos_email_html(user_name, user_email, user_phone, location_link)

    for contact in contacts:
        phone = contact["phone_number"]
        # SMS to contact
        r = send_sms(phone, sms_msg)
        results.append({"contact": contact["name"], "channel": "sms", **r})

        # WhatsApp to contact
        r2 = send_whatsapp(phone, sms_msg)
        results.append({"contact": contact["name"], "channel": "whatsapp", **r2})

    # Also email the patient themselves as confirmation
    r3 = send_email(user_email, "🚨 SOS Alert Dispatched – CareSync", email_html)
    results.append({"contact": user_name, "channel": "email", **r3})

    logger.info(f"SOS dispatched for user {user_id}: {len(results)} messages sent")
    return results
