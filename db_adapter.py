import os
import logging

logger = logging.getLogger('caresync.db')

DATABASE_URL = os.environ.get('DATABASE_URL')
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
SQLITE_DB_PATH = os.environ.get('SQLITE_DB_PATH', os.path.join(BASE_DIR, 'database.db'))

def is_postgres():
    return bool(DATABASE_URL and DATABASE_URL.startswith('postgres'))

class DBPool:
    _conn = None

class PSCursorWrapper:
    def __init__(self, cursor):
        self.cursor = cursor
        self._lastrowid = None
        
    def execute(self, query, params=()):
        # Replace ? with %s
        query = query.replace('?', '%s')
        
        is_insert = query.strip().upper().startswith("INSERT")
        if is_insert and "RETURNING" not in query.upper():
            query += " RETURNING id"
            
        self.cursor.execute(query, params)
        
        if is_insert:
            try:
                row = self.cursor.fetchone()
                if row:
                    self._lastrowid = row['id'] if isinstance(row, dict) else row[0]
            except Exception as e:
                pass
                
        return self
        
    def fetchone(self): return self.cursor.fetchone()
    def fetchall(self): return self.cursor.fetchall()
    def close(self): self.cursor.close()
    
    @property
    def lastrowid(self):
        return self._lastrowid
        
    def __iter__(self):
        return iter(self.cursor)

class PSConnectionWrapper:
    def __init__(self, conn):
        self.conn = conn
        
    def cursor(self):
        return PSCursorWrapper(self.conn.cursor())
        
    def commit(self):
        self.conn.commit()
        
    def close(self):
        self.conn.close()

def get_connection():
    if is_postgres():
        import psycopg2
        import psycopg2.extras
        url = DATABASE_URL.replace("postgres://", "postgresql://", 1)
        conn = psycopg2.connect(url, cursor_factory=psycopg2.extras.DictCursor)
        return PSConnectionWrapper(conn)
    else:
        import sqlite3
        conn = sqlite3.connect(SQLITE_DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

def get_db():
    try:
        from flask import g
        has_app_context = True
    except RuntimeError:
        has_app_context = False

    if has_app_context:
        try:
            db = getattr(g, '_database', None)
            if db is None:
                db = g._database = get_connection()
            return db
        except Exception:
            pass # Fallback if out of context

    return get_connection()

def _run_query_ignore_error(cursor, query):
    try:
        cursor.execute(query)
    except Exception as e:
        # Ignore duplicate column / table errors
        pass

def init_db(app=None):
    logger.info(f"Initializing database... Postgres Mode: {is_postgres()}")
    if not is_postgres():
        logger.info(f"SQLite database path: {SQLITE_DB_PATH}")
    
    # We must format schema depending on the engine
    pk_type = "SERIAL PRIMARY KEY" if is_postgres() else "INTEGER PRIMARY KEY AUTOINCREMENT"
    
    db = get_db()
    cursor = db.cursor()
    
    # 1. users
    cursor.execute(f'''
        CREATE TABLE IF NOT EXISTS users (
            id {pk_type},
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
    _run_query_ignore_error(cursor, 'ALTER TABLE users ADD COLUMN phone_number TEXT')
    _run_query_ignore_error(cursor, 'ALTER TABLE users ADD COLUMN prescription_image TEXT')
    _run_query_ignore_error(cursor, 'ALTER TABLE users ADD COLUMN age INTEGER')
    
    # 2. medicines
    cursor.execute(f'''
        CREATE TABLE IF NOT EXISTS medicines (
            id {pk_type},
            user_id INTEGER NOT NULL,
            medicine_name TEXT NOT NULL,
            dosage TEXT NOT NULL,
            time TEXT NOT NULL,
            repeat_type TEXT NOT NULL,
            prescription_id INTEGER
            -- SQLite supports inline FOREIGN KEY, postgres does too but we skip it here for simplicity to avoid constraint issues during migrations
        )
    ''')
    for col in ['food_instruction', 'start_date', 'end_date', 'notes']:
        _run_query_ignore_error(cursor, f'ALTER TABLE medicines ADD COLUMN {col} TEXT')
    _run_query_ignore_error(cursor, "ALTER TABLE medicines ADD COLUMN status TEXT DEFAULT 'active'")
    _run_query_ignore_error(cursor, 'ALTER TABLE medicines ADD COLUMN remaining_tablets INTEGER')
    _run_query_ignore_error(cursor, 'ALTER TABLE medicines ADD COLUMN prescription_id INTEGER')
    
    # 3. prescriptions
    cursor.execute(f'''
        CREATE TABLE IF NOT EXISTS prescriptions (
            id {pk_type},
            user_id INTEGER NOT NULL,
            image_path TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    ''')
    _run_query_ignore_error(cursor, 'ALTER TABLE prescriptions ADD COLUMN doctor_name TEXT')
    _run_query_ignore_error(cursor, 'ALTER TABLE prescriptions ADD COLUMN hospital_name TEXT')
    
    # 4. logs
    cursor.execute(f'''
        CREATE TABLE IF NOT EXISTS logs (
            id {pk_type},
            medicine_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            status TEXT NOT NULL
        )
    ''')
    
    # 5. alerts
    cursor.execute(f'''
        CREATE TABLE IF NOT EXISTS alerts (
            id {pk_type},
            user_id INTEGER NOT NULL,
            type TEXT NOT NULL,
            message TEXT NOT NULL,
            created_at TEXT NOT NULL,
            is_read INTEGER DEFAULT 0,
            channel TEXT DEFAULT 'in-app',
            delivery_status TEXT DEFAULT 'sent'
        )
    ''')
    for col, definition in [
        ('is_read', 'INTEGER DEFAULT 0'),
        ('channel', "TEXT DEFAULT 'in-app'"),
        ('delivery_status', "TEXT DEFAULT 'sent'")
    ]:
        _run_query_ignore_error(cursor, f'ALTER TABLE alerts ADD COLUMN {col} {definition}')
        
    # 6. emergency_contacts
    cursor.execute(f'''
        CREATE TABLE IF NOT EXISTS emergency_contacts (
            id {pk_type},
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            relation TEXT NOT NULL,
            phone_number TEXT NOT NULL
        )
    ''')
    _run_query_ignore_error(cursor, 'ALTER TABLE emergency_contacts ADD COLUMN alternate_number TEXT')
    _run_query_ignore_error(cursor, 'ALTER TABLE emergency_contacts ADD COLUMN priority_order INTEGER DEFAULT 0')
    
    # 7. history_logs
    cursor.execute(f'''
        CREATE TABLE IF NOT EXISTS history_logs (
            id {pk_type},
            user_id INTEGER NOT NULL,
            action_type TEXT NOT NULL,
            item_type TEXT NOT NULL,
            item_id INTEGER,
            item_name TEXT NOT NULL,
            details TEXT,
            timestamp TEXT NOT NULL
        )
    ''')
    
    # 8. notification_logs
    cursor.execute(f'''
        CREATE TABLE IF NOT EXISTS notification_logs (
            id {pk_type},
            medicine_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            attempt_num INTEGER DEFAULT 0,
            sent_at TEXT NOT NULL,
            sms_status TEXT DEFAULT 'not_sent',
            whatsapp_status TEXT DEFAULT 'not_sent',
            email_status TEXT DEFAULT 'not_sent'
        )
    ''')

    db.commit()
    logger.info("Database initialization completed successfully.")
