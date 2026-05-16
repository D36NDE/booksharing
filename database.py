import sqlite3
import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DB_FILE = os.path.join(BASE_DIR, 'booksharing.db')

def get_db_connection():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

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

    conn.commit()
    conn.close()

if __name__ == '__main__':
    init_db()
    print("Database initialized.")
