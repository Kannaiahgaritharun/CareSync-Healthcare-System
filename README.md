# CareSync - Healthcare System

CareSync is a comprehensive health platform designed to help patients manage their medication schedules while keeping their family members and primary caregivers in the loop. The platform offers multi-channel reminders, real-time in-app notifications, advanced adherence reporting, and a robust Emergency SOS system.

## 🚀 Features

- **Dual Dashboards:** Separate experiences for Patients (medication management) and Family/Caregivers (monitoring adherence).
- **Automated Reminders:** Background scheduling system that automatically dispatches SMS, WhatsApp, and Email reminders when a medication is due.
- **Repeat & Auto-Miss Logic:** Sends follow-up reminders at 10 and 30 minutes past due, and automatically marks a dose as "Missed" after 60 minutes.
- **Emergency SOS Center:** A dedicated dashboard for managing emergency contacts. Triggering an SOS dispatches immediate SMS, WhatsApp, and Email alerts containing the patient's live location.
- **Real-Time Notifications:** In-app toast notifications and alert badges powered by WebSockets (Socket.IO).
- **Adherence Reports:** Visual analytics and charts (powered by Chart.js) tracking 7-day adherence, missed doses, and overall completion rates.

## 🛠️ Tech Stack

- **Backend:** Python, Flask, Flask-SocketIO
- **Database:** SQLite3
- **Frontend:** HTML5, CSS3, JavaScript, Bootstrap 5, Chart.js
- **Background Tasks:** APScheduler
- **External Services:** Twilio API (SMS & WhatsApp), SMTP (Email)

## 📁 Project Structure

```text
CareSync/
│
├── app.py                      # Main Flask application and route definitions
├── auth.py                     # Authentication and role-based access control decorators
├── scheduler.py                # APScheduler background tasks (reminders, auto-miss)
├── services/
│   └── notifications.py        # Twilio and SMTP integrations (SMS, WhatsApp, Email, SOS)
├── static/
│   ├── style.css               # Custom CSS styles
│   └── js/
│       └── notifications.js    # Client-side Socket.IO and notification UI logic
├── templates/                  # Jinja2 HTML templates (Dashboards, Forms, Modals)
├── requirements.txt            # Python dependencies
└── .env.example                # Example environment variables file
```

## ⚙️ Installation

1. **Clone the repository:**
   ```bash
   git clone https://github.com/Kannaiahgaritharun/CareSync-Healthcare-System.git
   cd CareSync-Healthcare-System
   ```

2. **Set up a Virtual Environment:**
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows use: venv\Scripts\activate
   ```

3. **Install Dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

## 🔐 Environment Variables

Create a `.env` file in the root directory based on `.env.example`. You will need to configure the following keys:

```ini
FLASK_SECRET_KEY=your_secret_key_here

# Twilio Configuration (For SMS and WhatsApp)
TWILIO_ACCOUNT_SID=your_twilio_sid
TWILIO_AUTH_TOKEN=your_twilio_token
TWILIO_PHONE_NUMBER=your_twilio_number
TWILIO_WHATSAPP_FROM=whatsapp:+14155238886

# SMTP Configuration (For Emails)
SMTP_SERVER=smtp.gmail.com
SMTP_PORT=587
SMTP_EMAIL=your_email@gmail.com
SMTP_PASSWORD=your_app_password
SMTP_SENDER_NAME=CareSync Health

# Development Mode
MOCK_MODE=true  # Set to false to send real SMS/WhatsApp/Emails
```

## 🚀 Running the Project

1. **Start the Flask Server:**
   ```bash
   python app.py
   ```
   *Note: The background scheduler (APScheduler) and WebSockets will initialize automatically on startup.*

2. **Access the Application:**
   Open your web browser and navigate to: [http://127.0.0.1:5000](http://127.0.0.1:5000)

## 📸 Screenshots

*(Add screenshots of the Patient Dashboard, Family Dashboard, Reports, and Emergency Center here)*

## 🔮 Future Improvements

- Integration with physical IoT pill dispensers.
- AI-driven insights for medication interactions.
- Mobile application wrap (React Native/Flutter).
- Support for multiple languages (i18n).
