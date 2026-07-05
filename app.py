from flask import Flask, render_template, request, redirect, url_for, session, flash, g, jsonify
from flask_socketio import SocketIO, emit
import sqlite3
import os
import logging
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import datetime, date, timedelta
from dotenv import load_dotenv
from auth import login_required, role_required, api_login_required

load_dotenv()

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(levelname)s: %(message)s')
logger = logging.getLogger('caresync')

app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET_KEY', 'super_secret_health_key_2024')
socketio = SocketIO(app, cors_allowed_origins="*")
import os
DATABASE = '/tmp/database.db' if os.environ.get('VERCEL') else 'database.db'

UPLOAD_FOLDER = 'static/uploads'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
        db.row_factory = sqlite3.Row
    return db

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

def init_db():
    with app.app_context():
        db = get_db()
        cursor = db.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                role TEXT NOT NULL,
                patient_id INTEGER,
                phone_number TEXT,
                prescription_image TEXT,
                age INTEGER
            )
        ''')
        
        try:
            cursor.execute('ALTER TABLE users ADD COLUMN phone_number TEXT')
        except sqlite3.OperationalError:
            pass # Column likely exists
            
        try:
            cursor.execute('ALTER TABLE users ADD COLUMN prescription_image TEXT')
        except sqlite3.OperationalError:
            pass # Column likely exists
            
        try:
            cursor.execute('ALTER TABLE users ADD COLUMN age INTEGER')
        except sqlite3.OperationalError:
            pass # Column likely exists

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS medicines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                medicine_name TEXT NOT NULL,
                dosage TEXT NOT NULL,
                time TEXT NOT NULL,
                repeat_type TEXT NOT NULL,
                prescription_id INTEGER,
                FOREIGN KEY (user_id) REFERENCES users (id),
                FOREIGN KEY (prescription_id) REFERENCES prescriptions (id)
            )
        ''')
        
        for col in ['food_instruction', 'start_date', 'end_date', 'notes']:
            try:
                cursor.execute(f'ALTER TABLE medicines ADD COLUMN {col} TEXT')
            except sqlite3.OperationalError:
                pass
        
        try:
            cursor.execute("ALTER TABLE medicines ADD COLUMN status TEXT DEFAULT 'active'")
        except sqlite3.OperationalError:
            pass
        
        try:
            cursor.execute('ALTER TABLE medicines ADD COLUMN remaining_tablets INTEGER')
        except sqlite3.OperationalError:
            pass
        
        try:
            cursor.execute('ALTER TABLE medicines ADD COLUMN prescription_id INTEGER')
        except sqlite3.OperationalError:
            pass # Column likely exists

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS prescriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                image_path TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        ''')
        
        try:
            cursor.execute('ALTER TABLE prescriptions ADD COLUMN doctor_name TEXT')
        except sqlite3.OperationalError:
            pass
            
        try:
            cursor.execute('ALTER TABLE prescriptions ADD COLUMN hospital_name TEXT')
        except sqlite3.OperationalError:
            pass

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                medicine_id INTEGER NOT NULL,
                date TEXT NOT NULL,
                status TEXT NOT NULL,
                FOREIGN KEY (medicine_id) REFERENCES medicines (id)
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                type TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL,
                is_read INTEGER DEFAULT 0,
                channel TEXT DEFAULT 'in-app',
                delivery_status TEXT DEFAULT 'sent',
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        ''')

        # Migrate alerts table if needed
        for col, definition in [
            ('is_read', 'INTEGER DEFAULT 0'),
            ('channel', "TEXT DEFAULT 'in-app'"),
            ('delivery_status', "TEXT DEFAULT 'sent'")
        ]:
            try:
                cursor.execute(f'ALTER TABLE alerts ADD COLUMN {col} {definition}')
            except sqlite3.OperationalError:
                pass

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS emergency_contacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                relation TEXT NOT NULL,
                phone_number TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        ''')
        
        try:
            cursor.execute('ALTER TABLE emergency_contacts ADD COLUMN alternate_number TEXT')
        except sqlite3.OperationalError:
            pass
            
        try:
            cursor.execute('ALTER TABLE emergency_contacts ADD COLUMN priority_order INTEGER DEFAULT 0')
        except sqlite3.OperationalError:
            pass

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS history_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                action_type TEXT NOT NULL,
                item_type TEXT NOT NULL,
                item_id INTEGER,
                item_name TEXT NOT NULL,
                details TEXT,
                timestamp TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        ''')

        # Notification tracking table (for repeat reminder logic)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS notification_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                medicine_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                date TEXT NOT NULL,
                attempt_num INTEGER DEFAULT 0,
                sent_at TEXT NOT NULL,
                sms_status TEXT DEFAULT 'not_sent',
                whatsapp_status TEXT DEFAULT 'not_sent',
                email_status TEXT DEFAULT 'not_sent',
                FOREIGN KEY (medicine_id) REFERENCES medicines (id),
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        ''')

        db.commit()

@app.route('/')
def index():
    if 'user_id' in session:
        if session.get('role') == 'patient':
            return redirect(url_for('patient_dashboard'))
        elif session.get('role') == 'family':
            return redirect(url_for('family_dashboard'))
    return render_template('index.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    import re
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        phone_number = request.form.get('phone_number', '').strip()
        role = request.form.get('role', 'patient')
        patient_email = request.form.get('patient_email', '').strip()

        # Input Validation
        if phone_number and not re.match(r"^[6-9]\d{9}$", phone_number):
            flash('Invalid phone number. Must be a valid 10-digit Indian mobile number.', 'danger')
            return render_template('register.html', form=request.form)
        if not re.match(r"[^@]+@[^@]+\.[^@]+", email):
            flash('Invalid email format.', 'danger')
            return render_template('register.html', form=request.form)
        if len(password) < 6:
            flash('Password must be at least 6 characters long.', 'danger')
            return render_template('register.html', form=request.form)

        db = get_db()
        cursor = db.cursor()

        # Check if email exists
        user = cursor.execute('SELECT * FROM users WHERE email = ?', (email,)).fetchone()
        if user:
            flash('Email already registered!', 'danger')
            return render_template('register.html', form=request.form)

        patient_id = None
        if role == 'family':
            if not patient_email:
                flash('Patient Email is required for Family role.', 'danger')
                return render_template('register.html', form=request.form)
            patient = cursor.execute('SELECT id FROM users WHERE email = ? AND role = "patient"', (patient_email,)).fetchone()
            if not patient:
                flash('Patient not found with that email. Please ensure patient is registered first.', 'danger')
                return render_template('register.html', form=request.form)
            patient_id = patient['id']

        hashed_pw = generate_password_hash(password)
        cursor.execute('INSERT INTO users (name, email, password, role, patient_id, phone_number) VALUES (?, ?, ?, ?, ?, ?)',
                       (name, email, hashed_pw, role, patient_id, phone_number))
        db.commit()
        flash('Registration successful! Please login.', 'success')
        return redirect(url_for('login'))

    return render_template('register.html', form={})

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        
        db = get_db()
        cursor = db.cursor()
        user = cursor.execute('SELECT * FROM users WHERE email = ?', (email,)).fetchone()

        if user and check_password_hash(user['password'], password):
            session['user_id'] = user['id']
            session['name'] = user['name']
            session['role'] = user['role']
            session['patient_id'] = user['patient_id']
            session['profile_image'] = user['prescription_image']
            
            if user['role'] == 'patient':
                return redirect(url_for('patient_dashboard'))
            else:
                return redirect(url_for('family_dashboard'))
        else:
            flash('Invalid email or password.', 'danger')
            return render_template('login.html', form={'email': email})
            
    return render_template('login.html', form={})

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/patient_dashboard')
@role_required('patient')
def patient_dashboard():    
    db = get_db()
    cursor = db.cursor()
    medicines = cursor.execute('''
        SELECT m.*, p.image_path as prescription_image 
        FROM medicines m 
        LEFT JOIN prescriptions p ON m.prescription_id = p.id 
        WHERE m.user_id = ? AND (m.status = 'active' OR m.status IS NULL) ORDER BY m.time ASC
    ''', (session['user_id'],)).fetchall()
    
    # Check what was taken today
    today = date.today().strftime('%Y-%m-%d')
    logs = cursor.execute('SELECT medicine_id, status FROM logs WHERE date = ?', (today,)).fetchall()
    log_dict = {log['medicine_id']: log['status'] for log in logs}
    
    # Get all-time stats
    all_logs = cursor.execute('''
        SELECT l.status, COUNT(*) as cnt 
        FROM logs l JOIN medicines m ON l.medicine_id = m.id 
        WHERE m.user_id = ? GROUP BY l.status
    ''', (session['user_id'],)).fetchall()
    
    taken_count = 0
    missed_count = 0
    for row in all_logs:
        if row['status'] == 'taken':
            taken_count = row['cnt']
        elif row['status'] == 'missed':
            missed_count = row['cnt']
            
    # Calculate Pending for Today
    today_taken_missed = cursor.execute('SELECT medicine_id FROM logs WHERE date = ?', (today,)).fetchall()
    logged_ids = [row['medicine_id'] for row in today_taken_missed]
    pending_count = 0
    for med in medicines:
        if med['id'] not in logged_ids:
            pending_count += 1

    total_logs = taken_count + missed_count
    adherence_score = round((taken_count / total_logs) * 100) if total_logs > 0 else 100
            
    missed_details = cursor.execute('''
        SELECT m.medicine_name, l.date, m.time
        FROM logs l
        JOIN medicines m ON l.medicine_id = m.id
        WHERE m.user_id = ? AND l.status = 'missed'
        ORDER BY l.date DESC, m.time DESC
    ''', (session['user_id'],)).fetchall()
    
    user = cursor.execute('SELECT * FROM users WHERE id = ?', (session['user_id'],)).fetchone()
    contacts = cursor.execute('SELECT * FROM emergency_contacts WHERE user_id = ? ORDER BY id ASC', (session['user_id'],)).fetchall()

    return render_template('patient_dashboard.html', medicines=medicines, log_dict=log_dict, today=today, user=user, contacts=contacts, taken_count=taken_count, missed_count=missed_count, missed_details=missed_details, adherence_score=adherence_score, pending_count=pending_count)

@app.route('/family_dashboard')
@role_required('family')
def family_dashboard():        
    patient_id = session.get('patient_id')
    db = get_db()
    cursor = db.cursor()
    
    patient = cursor.execute('SELECT * FROM users WHERE id = ?', (patient_id,)).fetchone()
    
    # Get patient's medicines and logs
    medicines = cursor.execute('''
        SELECT m.*, p.image_path as prescription_image 
        FROM medicines m 
        LEFT JOIN prescriptions p ON m.prescription_id = p.id 
        WHERE m.user_id = ? ORDER BY m.time ASC
    ''', (patient_id,)).fetchall()
    today = date.today().strftime('%Y-%m-%d')
    logs = cursor.execute('SELECT m.medicine_name, l.status, m.time FROM logs l JOIN medicines m ON l.medicine_id = m.id WHERE m.user_id = ? AND l.date = ?', (patient_id, today)).fetchall()
    
    alerts = cursor.execute('SELECT * FROM alerts WHERE user_id = ? ORDER BY id DESC LIMIT 10', (patient_id,)).fetchall()

    return render_template('family_dashboard.html', patient=patient, medicines=medicines, logs=logs, alerts=alerts, today=today)

@app.route('/add_medicine', methods=['GET', 'POST'])
@role_required('patient')
def add_medicine():        
    if request.method == 'POST':
        file = request.files.get('prescription_image')
        if file and file.filename != '':
            filename = secure_filename(file.filename)
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(filepath)
            
            doctor_name = request.form.get('doctor_name', '')
            hospital_name = request.form.get('hospital_name', '')
            
            db = get_db()
            cursor = db.cursor()
            cursor.execute('INSERT INTO prescriptions (user_id, image_path, created_at, doctor_name, hospital_name) VALUES (?, ?, ?, ?, ?)',
                           (session['user_id'], filename, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), doctor_name, hospital_name))
            db.commit()
            new_id = cursor.lastrowid
            return redirect(url_for('add_medicine_details', prescription_id=new_id))
        else:
            return redirect(url_for('add_medicine_details'))
            
    return render_template('add_medicine_step1.html')

@app.route('/add_medicine_details', methods=['GET', 'POST'])
@role_required('patient')
def add_medicine_details():        
    db = get_db()
    cursor = db.cursor()
    
    prescription_id = request.args.get('prescription_id')
    prescription = None
    if prescription_id:
        prescription = cursor.execute('SELECT * FROM prescriptions WHERE id = ? AND user_id = ?', (prescription_id, session['user_id'])).fetchone()

    if request.method == 'POST':
        name = request.form.get('medicine_name', '').strip()
        dosage = request.form.get('dosage', '').strip()
        time = request.form.get('time', '').strip()
        repeat_type = request.form.get('repeat_type', 'daily')
        p_id = request.form.get('prescription_id')
        p_id = None if not p_id else int(p_id)
            
        food_instruction = request.form.get('food_instruction', '').strip()
        start_date = request.form.get('start_date', '').strip()
        end_date = request.form.get('end_date', '').strip()
        notes = request.form.get('notes', '').strip()
        remaining = request.form.get('remaining_tablets', '').strip()
        remaining = int(remaining) if remaining else None
        
        # Validations
        if not name or not dosage or not time:
            flash('Name, Dosage, and Time are required.', 'danger')
            return redirect(request.url)
            
        if start_date and end_date and start_date > end_date:
            flash('End Date cannot be before Start Date.', 'danger')
            return redirect(request.url)
            
        # Duplicate check
        dup = cursor.execute('SELECT id FROM medicines WHERE user_id = ? AND medicine_name = ? AND time = ? AND status = "active"', (session['user_id'], name, time)).fetchone()
        if dup:
            flash('An active medicine with this name and time already exists.', 'warning')
            return redirect(request.url)
        
        cursor.execute('''
            INSERT INTO medicines 
            (user_id, medicine_name, dosage, time, repeat_type, prescription_id, food_instruction, start_date, end_date, notes, remaining_tablets, status) 
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active')
        ''', (session['user_id'], name, dosage, time, repeat_type, p_id, food_instruction, start_date, end_date, notes, remaining))
        med_id = cursor.lastrowid
        
        cursor.execute('INSERT INTO history_logs (user_id, action_type, item_type, item_id, item_name, details, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?)',
                       (session['user_id'], 'added', 'medicine', med_id, name, f"Added to schedule. Dose: {dosage}", datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
        
        db.commit()
        flash(f'Tablet "{name}" added successfully!', 'success')
        
        action = request.form.get('action')
        if action == 'add_another':
            return redirect(url_for('add_medicine_details', prescription_id=p_id) if p_id else url_for('add_medicine_details'))
                
        return redirect(url_for('patient_dashboard'))
        
    return render_template('add_medicine_details.html', prescription=prescription, prescription_id=prescription_id)

@app.route('/delete_medicine/<int:id>', methods=['POST'])
@role_required('patient')
def delete_medicine(id):    
    db = get_db()
    cursor = db.cursor()
    cursor.execute('DELETE FROM medicines WHERE id = ? AND user_id = ?', (id, session['user_id']))
    db.commit()
    flash('Medicine deleted.', 'success')
    return redirect(url_for('patient_dashboard'))

@app.route('/log_medicine/<int:id>/<status>', methods=['POST'])
@role_required('patient')
def log_medicine(id, status):        
    today = date.today().strftime('%Y-%m-%d')
    db = get_db()
    cursor = db.cursor()
    
    med = cursor.execute('SELECT * FROM medicines WHERE id = ? AND user_id = ?', (id, session['user_id'])).fetchone()
    if not med:
        return redirect(url_for('patient_dashboard'))
    
    existing = cursor.execute('SELECT id, status FROM logs WHERE medicine_id = ? AND date = ?', (id, today)).fetchone()
    
    if existing:
        cursor.execute('UPDATE logs SET status = ? WHERE id = ?', (status, existing['id']))
        # Adjust remaining tablets if changed from missed -> taken or taken -> missed
        if existing['status'] != 'taken' and status == 'taken':
            cursor.execute('UPDATE medicines SET remaining_tablets = remaining_tablets - 1 WHERE id = ? AND remaining_tablets IS NOT NULL AND remaining_tablets > 0', (id,))
        elif existing['status'] == 'taken' and status != 'taken':
            cursor.execute('UPDATE medicines SET remaining_tablets = remaining_tablets + 1 WHERE id = ? AND remaining_tablets IS NOT NULL', (id,))
    else:
        cursor.execute('INSERT INTO logs (medicine_id, date, status) VALUES (?, ?, ?)', (id, today, status))
        if status == 'taken':
            cursor.execute('UPDATE medicines SET remaining_tablets = remaining_tablets - 1 WHERE id = ? AND remaining_tablets IS NOT NULL AND remaining_tablets > 0', (id,))
    
    # Check if medicine is now completed
    updated_med = cursor.execute('SELECT remaining_tablets, end_date FROM medicines WHERE id = ?', (id,)).fetchone()
    is_completed = False
    if updated_med['remaining_tablets'] == 0:
        is_completed = True
    elif updated_med['end_date'] and updated_med['end_date'] != '' and updated_med['end_date'] < today:
        is_completed = True
        
    if is_completed:
        cursor.execute("UPDATE medicines SET status = 'completed' WHERE id = ?", (id,))
        cursor.execute('INSERT INTO history_logs (user_id, action_type, item_type, item_id, item_name, details, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?)',
                       (session['user_id'], 'completed', 'medicine', id, med['medicine_name'], "Course completed.", datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
    
    if status == 'missed':
        message = f"Missed medicine: {med['medicine_name']} today."
        cursor.execute('INSERT INTO alerts (user_id, type, message, created_at, is_read, channel, delivery_status) VALUES (?, ?, ?, ?, 0, ?, ?)',
                       (session['user_id'], 'Missed Dose', message, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), 'in-app', 'sent'))

    cursor.execute('INSERT INTO history_logs (user_id, action_type, item_type, item_id, item_name, details, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?)',
                   (session['user_id'], 'logged', 'medicine', id, med['medicine_name'], f"Marked as {status}", datetime.now().strftime('%Y-%m-%d %H:%M:%S')))

    db.commit()
    socketio.emit('dashboard_update', {'medicine_id': id, 'status': status}, to=str(session['user_id']))
    if is_completed:
        flash(f"Medicine '{med['medicine_name']}' course is completed!", "success")
        
    return redirect(url_for('patient_dashboard'))

@app.route('/sos', methods=['POST'])
@role_required('patient')
def sos():        
    db = get_db()
    cursor = db.cursor()
    
    lat = request.form.get('latitude')
    lng = request.form.get('longitude')
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    location_link = None
    message = "[SOS] Emergency SOS Triggered!"
    if lat and lng:
        location_link = f"https://www.google.com/maps?q={lat},{lng}"
        message += f" Location: {location_link}"
        
    cursor.execute(
        'INSERT INTO alerts (user_id, type, message, created_at, is_read, channel, delivery_status) VALUES (?, ?, ?, ?, 0, ?, ?)' ,
        (session['user_id'], 'SOS Emergency', message, now_str, 'in-app', 'sent')
    )
    db.commit()

    # Dispatch real alerts to all emergency contacts
    try:
        from services.notifications import send_sos_alert
        results = send_sos_alert(session['user_id'], location_link, db)
        sent_count = sum(1 for r in results if r.get('success'))
        flash(f'SOS Alert dispatched! {sent_count} message(s) sent to your emergency contacts.', 'danger')
    except Exception as e:
        logger.error(f'SOS dispatch error: {e}')
        flash('SOS Alert logged. Please check your emergency contacts are set up.', 'warning')

    return redirect(url_for('patient_dashboard'))

@app.route('/add_contact', methods=['POST'])
@role_required('patient')
def add_contact():        
    name = request.form['name']
    relation = request.form['relation']
    phone = request.form['phone_number']
    alternate = request.form.get('alternate_number', '')
    
    db = get_db()
    cursor = db.cursor()
    cursor.execute('INSERT INTO emergency_contacts (user_id, name, relation, phone_number, alternate_number) VALUES (?, ?, ?, ?, ?)',
                   (session['user_id'], name, relation, phone, alternate))
    db.commit()
    flash(f'Emergency contact {name} added.', 'success')
    return redirect(url_for('emergency_dashboard'))

@app.route('/edit_contact/<int:id>', methods=['POST'])
@role_required('patient')
def edit_contact(id):        
    name = request.form['name']
    relation = request.form['relation']
    phone = request.form['phone_number']
    alternate = request.form.get('alternate_number', '')
    
    db = get_db()
    cursor = db.cursor()
    cursor.execute('UPDATE emergency_contacts SET name = ?, relation = ?, phone_number = ?, alternate_number = ? WHERE id = ? AND user_id = ?',
                   (name, relation, phone, alternate, id, session['user_id']))
    db.commit()
    flash('Emergency contact updated.', 'success')
    return redirect(url_for('patient_dashboard'))

@app.route('/delete_contact/<int:id>', methods=['POST'])
@role_required('patient')
def delete_contact(id):        
    db = get_db()
    cursor = db.cursor()
    cursor.execute('DELETE FROM emergency_contacts WHERE id = ? AND user_id = ?', (id, session['user_id']))
    db.commit()
    flash('Emergency contact deleted.', 'success')
    return redirect(url_for('patient_dashboard'))

@app.route('/reports')
@login_required
def reports():        
    db = get_db()
    cursor = db.cursor()
    
    target_id = session['user_id']
    if session.get('role') == 'family' and session.get('patient_id'):
        target_id = session.get('patient_id')
        
    prescriptions = cursor.execute('''
        SELECT p.*, COUNT(m.id) as med_count 
        FROM prescriptions p 
        LEFT JOIN medicines m ON p.id = m.prescription_id 
        WHERE p.user_id = ? 
        GROUP BY p.id 
        ORDER BY p.created_at DESC
    ''', (target_id,)).fetchall()
    
    # Analytics Data
    today = datetime.now().strftime('%Y-%m-%d')
    seven_days_ago = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
    
    # 1. Total Stats Today
    logs_today = cursor.execute('''
        SELECT l.status as action_type, COUNT(*) as count 
        FROM logs l
        JOIN medicines m ON l.medicine_id = m.id
        WHERE m.user_id = ? AND l.date = ?
        GROUP BY l.status
    ''', (target_id, today)).fetchall()
    
    today_stats = {'taken': 0, 'missed': 0, 'pending': 0}
    for log in logs_today:
        if log['action_type'] in today_stats:
            today_stats[log['action_type']] = log['count']
            
    # Calculate pending (active medicines - taken - missed today)
    total_active = cursor.execute("SELECT COUNT(*) FROM medicines WHERE user_id = ? AND status = 'active'", (target_id,)).fetchone()[0]
    today_stats['pending'] = max(0, total_active - today_stats['taken'] - today_stats['missed'])
    
    # 2. Weekly Adherence Data (last 7 days)
    weekly_logs = cursor.execute('''
        SELECT l.date as log_date, l.status as action_type, COUNT(*) as count
        FROM logs l
        JOIN medicines m ON l.medicine_id = m.id
        WHERE m.user_id = ? AND l.date >= ?
        GROUP BY l.date, l.status
        ORDER BY l.date ASC
    ''', (target_id, seven_days_ago)).fetchall()
    
    # Process weekly data into format for Chart.js
    dates = []
    for i in range(6, -1, -1):
        dates.append((datetime.now() - timedelta(days=i)).strftime('%Y-%m-%d'))
        
    weekly_stats = {
        'labels': dates,
        'taken': [0] * 7,
        'missed': [0] * 7
    }
    
    for log in weekly_logs:
        if log['log_date'] in dates:
            idx = dates.index(log['log_date'])
            if log['action_type'] == 'taken':
                weekly_stats['taken'][idx] = log['count']
            elif log['action_type'] == 'missed':
                weekly_stats['missed'][idx] = log['count']
                
    # Calculate Weekly Adherence %
    total_taken_week = sum(weekly_stats['taken'])
    total_missed_week = sum(weekly_stats['missed'])
    total_logs_week = total_taken_week + total_missed_week
    adherence_pct = round((total_taken_week / total_logs_week * 100) if total_logs_week > 0 else 0)
    
    return render_template('reports.html', 
                           prescriptions=prescriptions, 
                           today_stats=today_stats, 
                           weekly_stats=weekly_stats,
                           adherence_pct=adherence_pct)

@app.route('/delete_prescription/<int:id>', methods=['POST'])
@login_required
def delete_prescription(id):        
    db = get_db()
    cursor = db.cursor()
    
    presc = cursor.execute('SELECT * FROM prescriptions WHERE id = ? AND user_id = ?', (id, session['user_id'])).fetchone()
    if presc:
        # Log to history
        item_name = 'Prescription'
        if presc['doctor_name']:
            item_name += f" - {presc['doctor_name']}"
        cursor.execute('INSERT INTO history_logs (user_id, action_type, item_type, item_id, item_name, details, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?)',
                       (session['user_id'], 'deleted', 'prescription', id, item_name, f"Image: {presc['image_path']}", datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
        try:
            os.remove(os.path.join(app.config['UPLOAD_FOLDER'], presc['image_path']))
        except Exception:
            pass 
            
    cursor.execute('DELETE FROM prescriptions WHERE id = ? AND user_id = ?', (id, session['user_id']))
    cursor.execute('UPDATE medicines SET prescription_id = NULL WHERE prescription_id = ?', (id,))
    db.commit()
    flash('Prescription deleted.', 'success')
    return redirect(url_for('reports'))

@app.route('/medicine/<int:id>/move', methods=['POST'])
@role_required('patient')
def move_medicine(id):        
    db = get_db()
    cursor = db.cursor()
    med = cursor.execute('SELECT * FROM medicines WHERE id = ? AND user_id = ?', (id, session['user_id'])).fetchone()
    if med:
        cursor.execute("UPDATE medicines SET status = 'archived' WHERE id = ?", (id,))
        cursor.execute('INSERT INTO history_logs (user_id, action_type, item_type, item_id, item_name, details, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?)',
                       (session['user_id'], 'moved', 'medicine', id, med['medicine_name'], f"Moved to My Medicines. Dose: {med['dosage']}", datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
        db.commit()
        flash(f'"{med["medicine_name"]}" moved to My Medicines.', 'success')
    return redirect(url_for('patient_dashboard'))

@app.route('/medicine/<int:id>/remove', methods=['POST'])
@role_required('patient')
def remove_medicine(id):        
    db = get_db()
    cursor = db.cursor()
    med = cursor.execute('SELECT * FROM medicines WHERE id = ? AND user_id = ?', (id, session['user_id'])).fetchone()
    if med:
        cursor.execute("UPDATE medicines SET status = 'removed' WHERE id = ?", (id,))
        cursor.execute('INSERT INTO history_logs (user_id, action_type, item_type, item_id, item_name, details, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?)',
                       (session['user_id'], 'removed', 'medicine', id, med['medicine_name'], f"Removed from schedule. Dose: {med['dosage']}", datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
        db.commit()
        flash(f'"{med["medicine_name"]}" removed from schedule.', 'success')
    return redirect(url_for('patient_dashboard'))

@app.route('/history')
@login_required
def history():        
    db = get_db()
    cursor = db.cursor()
    
    filter_type = request.args.get('filter', 'all')
    
    if filter_type == 'medicines':
        logs = cursor.execute('SELECT * FROM history_logs WHERE user_id = ? AND item_type = ? ORDER BY timestamp DESC', (session['user_id'], 'medicine')).fetchall()
    elif filter_type == 'prescriptions':
        logs = cursor.execute('SELECT * FROM history_logs WHERE user_id = ? AND item_type = ? ORDER BY timestamp DESC', (session['user_id'], 'prescription')).fetchall()
    elif filter_type == 'deleted':
        logs = cursor.execute('SELECT * FROM history_logs WHERE user_id = ? AND action_type = ? ORDER BY timestamp DESC', (session['user_id'], 'deleted')).fetchall()
    elif filter_type == 'removed':
        logs = cursor.execute('SELECT * FROM history_logs WHERE user_id = ? AND action_type = ? ORDER BY timestamp DESC', (session['user_id'], 'removed')).fetchall()
    elif filter_type == 'moved':
        logs = cursor.execute('SELECT * FROM history_logs WHERE user_id = ? AND action_type = ? ORDER BY timestamp DESC', (session['user_id'], 'moved')).fetchall()
    else:
        logs = cursor.execute('SELECT * FROM history_logs WHERE user_id = ? ORDER BY timestamp DESC', (session['user_id'],)).fetchall()
    
    return render_template('history.html', logs=logs, current_filter=filter_type)

@app.route('/history/<int:id>/delete', methods=['POST'])
@login_required
def delete_history(id):        
    db = get_db()
    cursor = db.cursor()
    cursor.execute('DELETE FROM history_logs WHERE id = ? AND user_id = ?', (id, session['user_id']))
    db.commit()
    flash('History record deleted permanently.', 'success')
    return redirect(url_for('history'))

@app.route('/history/<int:id>/restore', methods=['POST'])
@login_required
def restore_history(id):        
    db = get_db()
    cursor = db.cursor()
    
    log = cursor.execute('SELECT * FROM history_logs WHERE id = ? AND user_id = ?', (id, session['user_id'])).fetchone()
    if log and log['item_type'] == 'medicine':
        cursor.execute("UPDATE medicines SET status = 'active' WHERE id = ? AND user_id = ?", (log['item_id'], session['user_id']))
        cursor.execute('INSERT INTO history_logs (user_id, action_type, item_type, item_id, item_name, details, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?)',
                       (session['user_id'], 'restored', 'medicine', log['item_id'], log['item_name'], 'Restored back to active schedule', datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
        cursor.execute('DELETE FROM history_logs WHERE id = ?', (id,))
        db.commit()
        flash(f'"{log["item_name"]}" restored to Today\'s Schedule.', 'success')
    else:
        flash('This item cannot be restored.', 'warning')
    return redirect(url_for('history'))

@app.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():        
    import re
    db = get_db()
    cursor = db.cursor()
    
    if request.method == 'POST':
        name = request.form.get('name')
        phone = request.form.get('phone_number')
        if phone and not re.match(r"^[6-9]\d{9}$", phone):
            flash('Invalid phone number. Must be a valid 10-digit Indian mobile number.', 'danger')
            return redirect(url_for('settings'))
        age = request.form.get('age')
        if age == '':
            age = None
            
        # Handle profile image upload
        file = request.files.get('profile_image')
        filename = None
        if file and file.filename != '':
            filename = secure_filename(file.filename)
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(filepath)
            session['profile_image'] = filename
        
        if filename:
            cursor.execute('UPDATE users SET name = ?, phone_number = ?, age = ?, prescription_image = ? WHERE id = ?', 
                           (name, phone, age, filename, session['user_id']))
        else:
            cursor.execute('UPDATE users SET name = ?, phone_number = ?, age = ? WHERE id = ?', 
                           (name, phone, age, session['user_id']))
        
        db.commit()
        session['name'] = name
        flash('Settings updated successfully!', 'success')
        return redirect(url_for('settings'))
        
    user = cursor.execute('SELECT * FROM users WHERE id = ?', (session['user_id'],)).fetchone()
    return render_template('settings.html', user=user)

@app.route('/my_medicines')
@role_required('patient')
def my_medicines():        
    db = get_db()
    cursor = db.cursor()
    medicines = cursor.execute('''
        SELECT m.*, p.image_path as prescription_image 
        FROM medicines m 
        LEFT JOIN prescriptions p ON m.prescription_id = p.id 
        WHERE m.user_id = ? ORDER BY m.time ASC
    ''', (session['user_id'],)).fetchall()
    return render_template('my_medicines.html', medicines=medicines)

@app.route('/edit_medicine/<int:id>', methods=['GET', 'POST'])
@role_required('patient')
def edit_medicine(id):        
    db = get_db()
    cursor = db.cursor()
    med = cursor.execute('SELECT * FROM medicines WHERE id = ? AND user_id = ?', (id, session['user_id'])).fetchone()
    
    if not med:
        flash('Medicine not found.', 'danger')
        return redirect(url_for('my_medicines'))

    if request.method == 'POST':
        name = request.form.get('medicine_name', '').strip()
        dosage = request.form.get('dosage', '').strip()
        time = request.form.get('time', '').strip()
        repeat = request.form.get('repeat_type', 'daily')
        food = request.form.get('food_instruction', '').strip()
        start_date = request.form.get('start_date', '').strip()
        end_date = request.form.get('end_date', '').strip()
        notes = request.form.get('notes', '').strip()
        remaining = request.form.get('remaining_tablets', '').strip()
        remaining = int(remaining) if remaining else None
        
        if not name or not dosage or not time:
            flash('Name, Dosage, and Time are required.', 'danger')
            return redirect(request.url)
            
        if start_date and end_date and start_date > end_date:
            flash('End Date cannot be before Start Date.', 'danger')
            return redirect(request.url)
            
        dup = cursor.execute('SELECT id FROM medicines WHERE user_id = ? AND medicine_name = ? AND time = ? AND status = "active" AND id != ?', (session['user_id'], name, time, id)).fetchone()
        if dup:
            flash('Another active medicine with this name and time already exists.', 'warning')
            return redirect(request.url)
        
        cursor.execute('''
            UPDATE medicines 
            SET medicine_name = ?, dosage = ?, time = ?, repeat_type = ?, food_instruction = ?, start_date = ?, end_date = ?, notes = ?, remaining_tablets = ?
            WHERE id = ? AND user_id = ?
        ''', (name, dosage, time, repeat, food, start_date, end_date, notes, remaining, id, session['user_id']))
        
        cursor.execute('INSERT INTO history_logs (user_id, action_type, item_type, item_id, item_name, details, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?)',
                       (session['user_id'], 'edited', 'medicine', id, name, f"Updated details. Dose: {dosage}", datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
        db.commit()
        flash('Medicine updated successfully!', 'success')
        return redirect(url_for('my_medicines'))
        
    return render_template('edit_medicine.html', med=med)

@app.route('/snooze_medicine/<int:id>', methods=['POST'])
@role_required('patient')
def snooze_medicine(id):        
    db = get_db()
    cursor = db.cursor()
    med = cursor.execute('SELECT * FROM medicines WHERE id = ? AND user_id = ?', (id, session['user_id'])).fetchone()
    
    if med:
        try:
            t = datetime.strptime(med['time'], '%H:%M')
            new_t = (t + timedelta(minutes=15)).strftime('%H:%M')
            cursor.execute('UPDATE medicines SET time = ? WHERE id = ?', (new_t, id))
            cursor.execute('INSERT INTO history_logs (user_id, action_type, item_type, item_id, item_name, details, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?)',
                           (session['user_id'], 'snoozed', 'medicine', id, med['medicine_name'], f"Snoozed for 15 mins. New time: {new_t}", datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
            db.commit()
            flash(f'"{med["medicine_name"]}" snoozed for 15 minutes.', 'info')
        except Exception as e:
            flash('Could not snooze this medicine.', 'danger')
            
    return redirect(request.referrer or url_for('patient_dashboard'))

@app.route('/reminders')
@login_required
def reminders():    
    db = get_db()
    cursor = db.cursor()
    # Filter for active medicines
    medicines = cursor.execute('''
        SELECT * FROM medicines WHERE user_id = ? AND status = 'active' ORDER BY time ASC
    ''', (session['user_id'],)).fetchall()
    
    today = date.today().strftime('%Y-%m-%d')
    logs = cursor.execute('SELECT medicine_id, status FROM logs WHERE date = ?', (today,)).fetchall()
    log_dict = {log['medicine_id']: log['status'] for log in logs}
    
    return render_template('reminders.html', medicines=medicines, log_dict=log_dict)

@app.route('/emergency_dashboard')
@login_required
def emergency_dashboard():    
    db = get_db()
    cursor = db.cursor()
    contacts = cursor.execute('SELECT * FROM emergency_contacts WHERE user_id = ? ORDER BY id ASC', (session['user_id'],)).fetchall()
    return render_template('emergency.html', contacts=contacts)

# ─── Notification API Endpoints ───────────────────────────────────────────

@app.route('/api/notifications')
@api_login_required
def api_notifications():
    """Return unread in-app alerts for the logged-in user (JSON)."""    
    db = get_db()
    cursor = db.cursor()
    alerts = cursor.execute("""
        SELECT id, type, message, created_at, is_read, channel
        FROM alerts
        WHERE user_id = ?
        ORDER BY created_at DESC
        LIMIT 50
    """, (session['user_id'],)).fetchall()
    
    unread_count = cursor.execute(
        "SELECT COUNT(*) FROM alerts WHERE user_id = ? AND is_read = 0",
        (session['user_id'],)
    ).fetchone()[0]
    
    return jsonify({
        'notifications': [
            {
                'id': a['id'],
                'type': a['type'],
                'message': a['message'],
                'created_at': a['created_at'],
                'is_read': a['is_read'],
                'channel': a['channel']
            } for a in alerts
        ],
        'unread_count': unread_count
    })


@app.route('/api/notifications/read/<int:alert_id>', methods=['POST'])
@api_login_required
def mark_notification_read(alert_id):
    """Mark a specific notification as read."""    
    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        "UPDATE alerts SET is_read = 1 WHERE id = ? AND user_id = ?",
        (alert_id, session['user_id'])
    )
    db.commit()
    return jsonify({'success': True})


@app.route('/api/notifications/read_all', methods=['POST'])
@api_login_required
def mark_all_notifications_read():
    """Mark all notifications as read."""    
    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        "UPDATE alerts SET is_read = 1 WHERE user_id = ?",
        (session['user_id'],)
    )
    db.commit()
    return jsonify({'success': True})


@app.route('/api/notifications/snooze/<int:alert_id>', methods=['POST'])
@api_login_required
def snooze_notification(alert_id):
    """Mark a notification as read (snooze action)."""    
    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        "UPDATE alerts SET is_read = 1 WHERE id = ? AND user_id = ?",
        (alert_id, session['user_id'])
    )
    db.commit()
    return jsonify({'success': True, 'snoozed': True})


@socketio.on('connect')
def handle_connect():
    if 'user_id' in session:
        from flask_socketio import join_room
        join_room(str(session['user_id']))

if __name__ == '__main__':
    init_db()
    import os
    if os.environ.get('WERKZEUG_RUN_MAIN') == 'true' or not app.debug:
        from scheduler import start_scheduler
        start_scheduler(socketio)
    
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host="0.0.0.0", port=port, allow_unsafe_werkzeug=True)
