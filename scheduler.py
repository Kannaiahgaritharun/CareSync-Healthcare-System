"""
CareSync Background Scheduler
Runs APScheduler in the background to:
  - Check medicines due within the last 5 minutes (every 60 seconds)
  - Resend reminders for missed doses at 10 and 30 minutes
  - Auto-mark as missed after 60 minutes (even if no reminder was ever sent)
"""

import sqlite3
import logging
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from services.notifications import (
    send_sms, send_whatsapp, send_email,
    build_reminder_sms, build_reminder_email_html
)

DATABASE = 'database.db'
logger = logging.getLogger("caresync.scheduler")

scheduler = None


def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


# ─── Job 1: Check medicines due in the last 5 minutes (every 60 s) ────────────
def check_due_medicines():
    now = datetime.now()
    current_time = now.strftime('%H:%M')
    window_start  = (now - timedelta(minutes=5)).strftime('%H:%M')
    today    = now.strftime('%Y-%m-%d')
    now_str  = now.strftime('%Y-%m-%d %H:%M:%S')

    logger.info(f"[Scheduler] Checking due medicines at {current_time} (window {window_start}–{current_time})")

    conn = get_db()
    cursor = conn.cursor()

    try:
        # Find active medicines due within the last 5-minute window
        medicines = cursor.execute("""
            SELECT m.*, u.name AS user_name, u.email AS user_email,
                   u.phone_number AS user_phone
            FROM medicines m
            JOIN users u ON m.user_id = u.id
            WHERE m.status = 'active'
              AND m.time >= ? AND m.time <= ?
              AND (m.start_date IS NULL OR m.start_date <= ?)
              AND (m.end_date   IS NULL OR m.end_date   >= ?)
        """, (window_start, current_time, today, today)).fetchall()

        for med in medicines:
            med_id  = med['id']
            user_id = med['user_id']

            # Skip if already logged today (taken / missed)
            if cursor.execute(
                "SELECT id FROM logs WHERE medicine_id = ? AND date = ?",
                (med_id, today)
            ).fetchone():
                continue

            # Skip if attempt-0 reminder already sent today (prevents duplicates)
            if cursor.execute("""
                SELECT id FROM notification_logs
                WHERE medicine_id = ? AND date = ? AND attempt_num = 0
            """, (med_id, today)).fetchone():
                continue

            # ── Build & send notifications ──────────────────────────────
            med_name   = med['medicine_name']
            dosage     = med['dosage']
            food_inst  = med['food_instruction'] or ''
            time_str   = med['time']
            user_name  = med['user_name']
            user_email = med['user_email']
            user_phone = med['user_phone'] or ''

            sms_msg    = build_reminder_sms(med_name, dosage, time_str, 0)
            email_html = build_reminder_email_html(user_name, med_name, dosage, food_inst, time_str, 0)

            sms_result   = send_sms(user_phone, sms_msg)       if user_phone else {"success": False}
            wa_result    = send_whatsapp(user_phone, sms_msg)  if user_phone else {"success": False}
            email_result = send_email(user_email, f"💊 Time to take {med_name} – CareSync Reminder", email_html)

            # ── In-app alert ────────────────────────────────────────────
            in_app_msg = f"⏰ Time to take {med_name} {dosage} now ({time_str}). {food_inst}".strip()
            cursor.execute("""
                INSERT INTO alerts (user_id, type, message, created_at, is_read, channel, delivery_status)
                VALUES (?, 'Medicine Reminder', ?, ?, 0, 'in-app', 'sent')
            """, (user_id, in_app_msg, now_str))

            # ── Log notification attempt ────────────────────────────────
            cursor.execute("""
                INSERT INTO notification_logs
                (medicine_id, user_id, date, attempt_num, sent_at, sms_status, whatsapp_status, email_status)
                VALUES (?, ?, ?, 0, ?, ?, ?, ?)
            """, (med_id, user_id, today, now_str,
                  'sent' if sms_result.get('success')   else 'failed',
                  'sent' if wa_result.get('success')    else 'failed',
                  'sent' if email_result.get('success') else 'failed'))

            conn.commit()
            logger.info(f"[Scheduler] Reminder sent for '{med_name}' (user {user_id})")

    except Exception as e:
        logger.error(f"[Scheduler] Error in check_due_medicines: {e}")
    finally:
        conn.close()


# ─── Job 2: Repeat reminders & auto-miss (every 5 minutes) ───────────────────
def check_repeat_reminders():
    now     = datetime.now()
    today   = now.strftime('%Y-%m-%d')
    now_str = now.strftime('%Y-%m-%d %H:%M:%S')

    logger.info(f"[Scheduler] Checking repeat reminders at {now.strftime('%H:%M')}")

    conn   = get_db()
    cursor = conn.cursor()

    try:
        # ── Part A: Medicines with notification_logs but not yet taken ──
        # Get the LATEST attempt per medicine (avoids processing stale rows)
        pending_logs = cursor.execute("""
            SELECT nl.medicine_id, nl.user_id, nl.attempt_num, nl.sent_at,
                   m.medicine_name, m.dosage, m.food_instruction, m.time AS med_time,
                   u.name AS user_name, u.email AS user_email, u.phone_number AS user_phone
            FROM notification_logs nl
            JOIN medicines m ON nl.medicine_id = m.id
            JOIN users u ON nl.user_id = u.id
            WHERE nl.date = ?
              AND nl.attempt_num = (
                  SELECT MAX(nl2.attempt_num)
                  FROM notification_logs nl2
                  WHERE nl2.medicine_id = nl.medicine_id AND nl2.date = nl.date
              )
              AND nl.attempt_num < 3
        """, (today,)).fetchall()

        for log in pending_logs:
            med_id  = log['medicine_id']
            user_id = log['user_id']
            attempt = log['attempt_num']

            # Skip if already logged (taken or missed)
            if cursor.execute(
                "SELECT id FROM logs WHERE medicine_id = ? AND date = ?",
                (med_id, today)
            ).fetchone():
                continue

            # Get time of the very first notification sent
            first_row = cursor.execute("""
                SELECT sent_at FROM notification_logs
                WHERE medicine_id = ? AND date = ? AND attempt_num = 0
                LIMIT 1
            """, (med_id, today)).fetchone()

            if not first_row:
                continue

            first_sent_dt  = datetime.strptime(first_row['sent_at'], '%Y-%m-%d %H:%M:%S')
            mins_since_first = (now - first_sent_dt).total_seconds() / 60

            # Cooldown: don't re-send within 60 s of last attempt
            last_sent_dt = datetime.strptime(log['sent_at'], '%Y-%m-%d %H:%M:%S')
            if (now - last_sent_dt).total_seconds() < 60:
                continue

            med_name   = log['medicine_name']
            dosage     = log['dosage']
            food_inst  = log['food_instruction'] or ''
            time_str   = log['med_time']
            user_name  = log['user_name']
            user_email = log['user_email']
            user_phone = log['user_phone'] or ''

            # ── Auto-miss after 60 minutes from first notification ──────
            if mins_since_first >= 60:
                cursor.execute(
                    "INSERT INTO logs (medicine_id, date, status) VALUES (?, ?, 'missed')",
                    (med_id, today)
                )
                miss_msg = (f"❌ {med_name} {dosage} marked as MISSED. "
                            f"Scheduled for {time_str} — no action taken for 60 min.")
                cursor.execute("""
                    INSERT INTO alerts (user_id, type, message, created_at, is_read, channel, delivery_status)
                    VALUES (?, 'Missed Dose', ?, ?, 0, 'in-app', 'sent')
                """, (user_id, miss_msg, now_str))
                # Mark all notification_log rows for this medicine as closed
                cursor.execute("""
                    UPDATE notification_logs SET attempt_num = 3
                    WHERE medicine_id = ? AND date = ?
                """, (med_id, today))
                conn.commit()
                logger.info(f"[Scheduler] Auto-missed '{med_name}' (user {user_id}) after 60 min")
                continue

            # ── Decide next repeat attempt ──────────────────────────────
            next_attempt = None
            if attempt == 0 and mins_since_first >= 10:
                next_attempt = 1
            elif attempt == 1 and mins_since_first >= 30:
                next_attempt = 2

            if next_attempt is None:
                continue

            # Send repeat reminder
            sms_msg    = build_reminder_sms(med_name, dosage, time_str, next_attempt)
            email_html = build_reminder_email_html(user_name, med_name, dosage, food_inst, time_str, next_attempt)

            sms_result   = send_sms(user_phone, sms_msg)      if user_phone else {"success": False}
            wa_result    = send_whatsapp(user_phone, sms_msg) if user_phone else {"success": False}
            email_result = send_email(
                user_email,
                f"⚠️ Reminder #{next_attempt+1}: Take {med_name} – CareSync",
                email_html
            )

            label      = "⚠️ Follow-up" if next_attempt == 1 else "🚨 Final"
            in_app_msg = (f"{label} Reminder: Still time to take {med_name} {dosage}. "
                          f"Scheduled {time_str}.")
            cursor.execute("""
                INSERT INTO alerts (user_id, type, message, created_at, is_read, channel, delivery_status)
                VALUES (?, 'Repeat Reminder', ?, ?, 0, 'in-app', 'sent')
            """, (user_id, in_app_msg, now_str))

            cursor.execute("""
                INSERT INTO notification_logs
                (medicine_id, user_id, date, attempt_num, sent_at, sms_status, whatsapp_status, email_status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (med_id, user_id, today, next_attempt, now_str,
                  'sent' if sms_result.get('success')   else 'failed',
                  'sent' if wa_result.get('success')    else 'failed',
                  'sent' if email_result.get('success') else 'failed'))

            conn.commit()
            logger.info(f"[Scheduler] Repeat reminder #{next_attempt} sent for '{med_name}' (user {user_id})")

        # ── Part B: Medicines past due >60 min with NO notification sent ──
        # Catches cases where the scheduler was down during the reminder window
        overdue_threshold = (now - timedelta(minutes=60)).strftime('%H:%M')

        overdue_meds = cursor.execute("""
            SELECT m.*, u.name AS user_name, u.email AS user_email,
                   u.phone_number AS user_phone
            FROM medicines m
            JOIN users u ON m.user_id = u.id
            WHERE m.status = 'active'
              AND m.time <= ?
              AND (m.start_date IS NULL OR m.start_date <= ?)
              AND (m.end_date   IS NULL OR m.end_date   >= ?)
              AND NOT EXISTS (
                  SELECT 1 FROM logs WHERE medicine_id = m.id AND date = ?
              )
              AND NOT EXISTS (
                  SELECT 1 FROM notification_logs WHERE medicine_id = m.id AND date = ?
              )
        """, (overdue_threshold, today, today, today, today)).fetchall()

        for med in overdue_meds:
            med_id  = med['id']
            user_id = med['user_id']

            # Mark as missed
            cursor.execute(
                "INSERT INTO logs (medicine_id, date, status) VALUES (?, ?, 'missed')",
                (med_id, today)
            )
            miss_msg = (f"❌ {med['medicine_name']} {med['dosage']} marked as MISSED. "
                        f"Was scheduled for {med['time']} — no reminder could be delivered.")
            cursor.execute("""
                INSERT INTO alerts (user_id, type, message, created_at, is_read, channel, delivery_status)
                VALUES (?, 'Missed Dose', ?, ?, 0, 'in-app', 'sent')
            """, (user_id, miss_msg, now_str))
            # Insert a sentinel notification_log row so we don't process it again
            cursor.execute("""
                INSERT INTO notification_logs
                (medicine_id, user_id, date, attempt_num, sent_at, sms_status, whatsapp_status, email_status)
                VALUES (?, ?, ?, 3, ?, 'not_sent', 'not_sent', 'not_sent')
            """, (med_id, user_id, today, now_str))
            conn.commit()
            logger.info(f"[Scheduler] Auto-missed (no prior notification) '{med['medicine_name']}' (user {user_id})")

    except Exception as e:
        logger.error(f"[Scheduler] Error in check_repeat_reminders: {e}")
    finally:
        conn.close()


# ─── Scheduler lifecycle ───────────────────────────────────────────────────────
def start_scheduler():
    global scheduler
    if scheduler and scheduler.running:
        return

    scheduler = BackgroundScheduler(daemon=True)

    # Job 1: Check due medicines every 60 seconds
    scheduler.add_job(
        check_due_medicines,
        trigger=IntervalTrigger(seconds=60),
        id='due_medicines',
        name='Check Due Medicines',
        replace_existing=True,
        misfire_grace_time=30
    )

    # Job 2: Repeat reminders + auto-miss every 5 minutes
    scheduler.add_job(
        check_repeat_reminders,
        trigger=IntervalTrigger(minutes=5),
        id='repeat_reminders',
        name='Repeat Reminder & Auto-Miss Logic',
        replace_existing=True,
        misfire_grace_time=60
    )

    scheduler.start()
    logger.info("CareSync background scheduler started.")
    print("\n[CareSync] Background scheduler started. Checking medicines every 60 seconds.\n")


def stop_scheduler():
    global scheduler
    if scheduler and scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped.")
