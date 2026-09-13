import sqlite3
import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DB_FILE = os.path.join(BASE_DIR, 'booksharing.db')

def get_db_connection():
    conn = sqlite3.connect(DB_FILE)
    conn.execute('PRAGMA foreign_keys = ON')
    conn.row_factory = sqlite3.Row
    return conn

def check_and_migrate_db(conn):
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(books)")
    columns = [row[1] for row in cursor.fetchall()]
    if columns:
        if 'genre' not in columns:
            cursor.execute("ALTER TABLE books ADD COLUMN genre TEXT DEFAULT 'Sonstiges'")
        if 'description' not in columns:
            cursor.execute("ALTER TABLE books ADD COLUMN description TEXT")
        conn.commit()

    # Neue Tabellen, die bei init_db() schon über CREATE TABLE IF NOT EXISTS
    # entstehen, müssen hier zusätzlich erstellt werden, da bei bestehenden
    # Deployments nur check_and_migrate_db() beim App-Start läuft, nicht init_db().
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS password_reset_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            token_hash TEXT UNIQUE NOT NULL,
            expires_at TEXT NOT NULL,
            used INTEGER DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')
    conn.commit()

    create_indexes(conn)

    # Best-effort: verhindert neue Accounts mit doppelter E-Mail, ohne bei
    # bereits vorhandenen Duplikaten die komplette Migration scheitern zu lassen.
    try:
        cursor.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email_unique "
            "ON users (email) WHERE email IS NOT NULL AND email != ''"
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.rollback()


def create_indexes(conn):
    cursor = conn.cursor()
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_books_owner_id ON books (owner_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_books_status ON books (status)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_exchange_requester_id ON exchange_requests (requester_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_exchange_target_book_id ON exchange_requests (target_book_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_exchange_offered_book_id ON exchange_requests (offered_book_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_exchange_status ON exchange_requests (status)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_reset_tokens_user_id ON password_reset_tokens (user_id)')
    conn.commit()

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()

    # Users Table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            email TEXT,
            password TEXT NOT NULL
        )
    ''')

    # Books Table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS books (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            author TEXT NOT NULL,
            condition TEXT NOT NULL,
            genre TEXT DEFAULT 'Sonstiges',
            description TEXT,
            owner_id INTEGER NOT NULL,
            status TEXT DEFAULT 'AVAILABLE',
            image_filename TEXT,
            FOREIGN KEY (owner_id) REFERENCES users (id)
        )
    ''')

    # Exchange Requests Table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS exchange_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            requester_id INTEGER NOT NULL,
            target_book_id INTEGER NOT NULL,
            offered_book_id INTEGER NOT NULL,
            status TEXT DEFAULT 'PENDING',
            FOREIGN KEY (requester_id) REFERENCES users (id),
            FOREIGN KEY (target_book_id) REFERENCES books (id),
            FOREIGN KEY (offered_book_id) REFERENCES books (id)
        )
    ''')

    # Password-Reset-Tokens
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS password_reset_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            token_hash TEXT UNIQUE NOT NULL,
            expires_at TEXT NOT NULL,
            used INTEGER DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')

    check_and_migrate_db(conn)
    conn.commit()
    conn.close()

if __name__ == '__main__':
    init_db()
    print("Database initialized.")
