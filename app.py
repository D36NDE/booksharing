import logging
import os
import re
import secrets
import uuid

from flask import Flask, render_template, request, redirect, url_for, session, flash, g, Response, abort
from flask_wtf import CSRFProtect
from flask_wtf.csrf import CSRFError
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from PIL import Image
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

import database

app = Flask(__name__)

_secret_key = os.environ.get('SECRET_KEY')
if not _secret_key:
    # Kein fest codierter Fallback: Ohne SECRET_KEY-Env-Var wird pro Prozessstart
    # ein zufälliger Schlüssel erzeugt. Das invalidiert bestehende Sessions bei
    # jedem Neustart, verhindert aber gefälschte/entschlüsselbare Cookies.
    _secret_key = secrets.token_hex(32)
    logging.getLogger(__name__).warning(
        'SECRET_KEY ist nicht gesetzt! Es wird ein zufälliger, temporärer Schlüssel '
        'verwendet - alle Sessions gehen beim nächsten Neustart verloren. '
        'Für den Produktivbetrieb bitte die Umgebungsvariable SECRET_KEY setzen.'
    )
app.secret_key = _secret_key

# --- CSRF-Schutz ---
csrf = CSRFProtect(app)

# --- Rate-Limiting (Brute-Force-Schutz z.B. beim Login) ---
limiter = Limiter(get_remote_address, app=app, default_limits=['200 per day', '50 per hour'])

UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static/uploads')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}

BOOK_GENRES = (
    'Roman & Belletristik',
    'Krimi & Thriller',
    'Fantasy & Sci-Fi',
    'Sachbuch & Ratgeber',
    'Biografie & Geschichte',
    'Kinder- & Jugendbuch',
    'Klassiker',
    'Sonstiges'
)

# --- Status-Konstanten (statt verstreuter Magic Strings) ---
BOOK_AVAILABLE = 'AVAILABLE'
BOOK_PENDING = 'PENDING'
BOOK_EXCHANGED = 'EXCHANGED'

REQUEST_PENDING = 'PENDING'
REQUEST_ACCEPTED = 'ACCEPTED'
REQUEST_REJECTED = 'REJECTED'
REQUEST_CANCELLED = 'CANCELLED'

BOOKS_PER_PAGE = 12
EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')

# Ensure database schema is migrated
with app.app_context():
    _migration_conn = database.get_db_connection()
    database.check_and_migrate_db(_migration_conn)
    _migration_conn.close()

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 32 * 1024 * 1024  # 32 MB limit

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def validate_image_file(file_storage):
    """Prüft anhand des tatsächlichen Dateiinhalts (nicht nur der Endung), ob
    es sich um ein valides Bild handelt. Setzt den Stream danach zurück, damit
    er anschließend noch mit .save() geschrieben werden kann."""
    try:
        file_storage.stream.seek(0)
        img = Image.open(file_storage.stream)
        img.verify()
        return True
    except Exception:
        # PIL kann bei kaputten/manipulierten Dateien diverse Fehlertypen werfen
        # (UnidentifiedImageError, OSError, SyntaxError, struct.error, ...).
        # Sicherheitshalber gilt: alles, was sich nicht sauber verifizieren
        # lässt, wird als ungültiges Bild abgelehnt statt einen 500er zu werfen.
        return False
    finally:
        file_storage.stream.seek(0)

def delete_book_image(filename):
    if filename:
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        if os.path.exists(filepath):
            try:
                os.remove(filepath)
            except OSError:
                app.logger.warning('Konnte Bilddatei nicht löschen: %s', filepath, exc_info=True)

@app.errorhandler(413)
def request_entity_too_large(error):
    flash('Das hochgeladene Bild ist zu groß. Bitte wähle ein Bild unter 32 MB.', 'error')
    return redirect(request.referrer or url_for('my_books')), 413

@app.errorhandler(CSRFError)
def handle_csrf_error(error):
    flash('Deine Sitzung ist abgelaufen oder das Formular war ungültig. Bitte versuche es erneut.', 'error')
    return redirect(request.referrer or url_for('index')), 400

@app.errorhandler(429)
def handle_rate_limit(error):
    flash('Zu viele Versuche. Bitte warte kurz und versuche es dann erneut.', 'error')
    return redirect(request.referrer or url_for('login')), 429

@app.errorhandler(404)
def not_found(error):
    return render_template('404.html'), 404

@app.errorhandler(500)
def internal_server_error(error):
    return render_template('500.html'), 500

# Ensure upload directory exists
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# --- Database Hook ---
@app.before_request
def before_request():
    g.db = database.get_db_connection()
    if 'user_id' in session:
        user = g.db.execute('SELECT * FROM users WHERE id = ?', (session['user_id'],)).fetchone()
        if user:
            g.user = user
            pending_count = g.db.execute('''
                SELECT COUNT(*) as count
                FROM exchange_requests er
                JOIN books tb ON er.target_book_id = tb.id
                WHERE tb.owner_id = ? AND er.status = ?
            ''', (user['id'], REQUEST_PENDING)).fetchone()
            g.pending_requests_count = pending_count['count'] if pending_count else 0
        else:
            session.pop('user_id', None)
            g.user = None
            g.pending_requests_count = 0
    else:
        g.user = None
        g.pending_requests_count = 0

@app.teardown_request
def teardown_request(exception):
    db = getattr(g, 'db', None)
    if db is not None:
        db.close()

# --- Auth Routes ---
@app.route('/login', methods=['GET', 'POST'])
@limiter.limit('10 per minute')
def login():
    if g.user:
        return redirect(url_for('index'))

    next_url = request.args.get('next') or request.form.get('next')

    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')

        user = g.db.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
        if user and check_password_hash(user['password'], password):
            session['user_id'] = user['id']
            return redirect(next_url or url_for('index'))
        flash('Falscher Benutzername oder Passwort.', 'error')

    return render_template('login.html', next_url=next_url)

@app.route('/register', methods=['GET', 'POST'])
@limiter.limit('10 per hour')
def register():
    if g.user:
        return redirect(url_for('index'))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')

        if not username or not email or not password:
            flash('Bitte fülle alle Felder aus.', 'error')
            return render_template('register.html')

        if not EMAIL_RE.match(email):
            flash('Bitte gib eine gültige E-Mail-Adresse an.', 'error')
            return render_template('register.html')

        if len(password) < 8:
            flash('Das Passwort muss mindestens 8 Zeichen lang sein.', 'error')
            return render_template('register.html')

        user = g.db.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
        existing_email = g.db.execute('SELECT id FROM users WHERE email = ?', (email,)).fetchone()
        if user:
            flash('Benutzername existiert bereits.', 'error')
        elif existing_email:
            flash('Für diese E-Mail-Adresse existiert bereits ein Konto.', 'error')
        else:
            hashed = generate_password_hash(password, method='pbkdf2:sha256')
            g.db.execute('INSERT INTO users (username, email, password) VALUES (?, ?, ?)', (username, email, hashed))
            g.db.commit()
            flash('Erfolgreich registriert. Du kannst dich jetzt einloggen!', 'success')
            return redirect(url_for('login'))

    return render_template('register.html')


@app.route('/logout')
def logout():
    session.pop('user_id', None)
    return redirect(url_for('login'))

# --- Main Routes ---
@app.route('/')
def index():
    q = request.args.get('q', '').strip()
    condition = request.args.get('condition', '').strip()
    genre = request.args.get('genre', '').strip()
    try:
        page = max(1, int(request.args.get('page', 1)))
    except ValueError:
        page = 1

    base_sql = '''
        FROM books b
        JOIN users u ON b.owner_id = u.id
        WHERE b.status = ?
    '''
    params = [BOOK_AVAILABLE]

    if g.user:
        base_sql += ' AND b.owner_id != ?'
        params.append(g.user['id'])

    if q:
        base_sql += ' AND (b.title LIKE ? OR b.author LIKE ? OR u.username LIKE ? OR b.description LIKE ?)'
        search_pattern = f'%{q}%'
        params.extend([search_pattern, search_pattern, search_pattern, search_pattern])

    if condition:
        base_sql += ' AND b.condition = ?'
        params.append(condition)

    if genre:
        base_sql += ' AND b.genre = ?'
        params.append(genre)

    total_count = g.db.execute(f'SELECT COUNT(*) as count {base_sql}', params).fetchone()['count']
    total_pages = max(1, (total_count + BOOKS_PER_PAGE - 1) // BOOKS_PER_PAGE)
    page = min(page, total_pages)
    offset = (page - 1) * BOOKS_PER_PAGE

    list_sql = f'''
        SELECT b.id, b.title, b.author, b.condition, b.genre, b.description, b.image_filename, u.username as owner_name
        {base_sql}
        ORDER BY b.id DESC
        LIMIT ? OFFSET ?
    '''
    books = g.db.execute(list_sql, params + [BOOKS_PER_PAGE, offset]).fetchall()

    return render_template('index.html', books=books, q=q, condition=condition, genre=genre, genres=BOOK_GENRES,
                            page=page, total_pages=total_pages, total_count=total_count)

@app.route('/my-books', methods=['GET', 'POST'])
def my_books():
    if not g.user:
        flash('Bitte melde dich an, um ein Buch hochzuladen.', 'info')
        return redirect(url_for('login', next=url_for('my_books')))
        
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        author = request.form.get('author', '').strip()
        condition = request.form.get('condition', '').strip()
        genre = request.form.get('genre', 'Sonstiges').strip() or 'Sonstiges'
        description = request.form.get('description', '').strip() or None
        
        if not (title and author and condition):
            flash('Bitte alle Pflichtfelder (Titel, Autor, Zustand) ausfüllen.', 'error')
            return redirect(url_for('my_books'))

        image_filename = None
        if 'image' in request.files:
            file = request.files['image']
            if file and file.filename != '':
                if not allowed_file(file.filename) or not validate_image_file(file):
                    flash('Die hochgeladene Datei ist kein gültiges Bild (erlaubt: PNG, JPG, GIF, WEBP).', 'error')
                    return redirect(url_for('my_books'))
                filename = secure_filename(file.filename)
                unique_filename = f"{uuid.uuid4().hex}_{filename}"
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], unique_filename))
                image_filename = unique_filename

        g.db.execute('INSERT INTO books (title, author, condition, genre, description, owner_id, image_filename) VALUES (?, ?, ?, ?, ?, ?, ?)',
                     (title, author, condition, genre, description, g.user['id'], image_filename))
        g.db.commit()
        flash('Buch erfolgreich hinzugefügt!', 'success')
        return redirect(url_for('my_books'))
        
    books = g.db.execute('SELECT * FROM books WHERE owner_id = ? ORDER BY id DESC', (g.user['id'],)).fetchall()
    return render_template('my_books.html', books=books, genres=BOOK_GENRES)

@app.route('/my-books/edit/<int:book_id>', methods=['GET', 'POST'])
def edit_book(book_id):
    if not g.user:
        flash('Bitte melde dich an, um Bücher zu bearbeiten.', 'info')
        return redirect(url_for('login'))

    book = g.db.execute('SELECT * FROM books WHERE id = ?', (book_id,)).fetchone()
    if not book:
        flash('Buch nicht gefunden.', 'error')
        return redirect(url_for('my_books'))

    if book['owner_id'] != g.user['id']:
        flash('Du bist nicht berechtigt, dieses Buch zu bearbeiten.', 'error')
        return redirect(url_for('my_books'))

    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        author = request.form.get('author', '').strip()
        condition = request.form.get('condition', '').strip()
        genre = request.form.get('genre', 'Sonstiges').strip() or 'Sonstiges'
        description = request.form.get('description', '').strip() or None
        remove_image = request.form.get('remove_image') == '1'

        if not title or not author or not condition:
            flash('Bitte alle Pflichtfelder (Titel, Autor, Zustand) ausfüllen.', 'error')
            return render_template('edit_book.html', book=book, genres=BOOK_GENRES)

        image_filename = book['image_filename']

        # Handle removing image
        if remove_image and image_filename:
            delete_book_image(image_filename)
            image_filename = None

        # Handle uploading a new image
        if 'image' in request.files:
            file = request.files['image']
            if file and file.filename != '':
                if not allowed_file(file.filename) or not validate_image_file(file):
                    flash('Die hochgeladene Datei ist kein gültiges Bild (erlaubt: PNG, JPG, GIF, WEBP).', 'error')
                    return render_template('edit_book.html', book=book, genres=BOOK_GENRES)
                if image_filename:
                    delete_book_image(image_filename)
                filename = secure_filename(file.filename)
                unique_filename = f"{uuid.uuid4().hex}_{filename}"
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], unique_filename))
                image_filename = unique_filename

        g.db.execute(
            'UPDATE books SET title = ?, author = ?, condition = ?, genre = ?, description = ?, image_filename = ? WHERE id = ?',
            (title, author, condition, genre, description, image_filename, book_id)
        )
        g.db.commit()
        flash('Buch erfolgreich aktualisiert!', 'success')
        return redirect(url_for('my_books'))

    return render_template('edit_book.html', book=book, genres=BOOK_GENRES)

@app.route('/my-books/delete/<int:book_id>', methods=['POST'])
def delete_book(book_id):
    if not g.user:
        flash('Bitte melde dich an.', 'info')
        return redirect(url_for('login'))

    book = g.db.execute('SELECT * FROM books WHERE id = ?', (book_id,)).fetchone()
    if not book:
        flash('Buch nicht gefunden.', 'error')
        return redirect(url_for('my_books'))

    if book['owner_id'] != g.user['id']:
        flash('Du bist nicht berechtigt, dieses Buch zu löschen.', 'error')
        return redirect(url_for('my_books'))

    if book['status'] == BOOK_PENDING:
        flash('Dieses Buch ist aktuell Teil einer laufenden Tauschanfrage und kann nicht gelöscht werden.', 'error')
        return redirect(url_for('my_books'))

    # Clean up non-active exchange requests for this book
    g.db.execute('DELETE FROM exchange_requests WHERE target_book_id = ? OR offered_book_id = ?', (book_id, book_id))

    # Delete cover image if present
    if book['image_filename']:
        delete_book_image(book['image_filename'])

    g.db.execute('DELETE FROM books WHERE id = ?', (book_id,))
    g.db.commit()

    flash(f'Das Buch "{book["title"]}" wurde erfolgreich gelöscht.', 'success')
    return redirect(url_for('my_books'))

@app.route('/request-book/<int:book_id>', methods=['GET', 'POST'])
def request_book(book_id):
    if not g.user:
        return redirect(url_for('login'))
        
    # Check if book exists and is available
    target_book = g.db.execute('SELECT * FROM books WHERE id = ? AND status = ?', (book_id, BOOK_AVAILABLE)).fetchone()
    if not target_book:
        flash('Dieses Buch ist nicht verfügbar.', 'error')
        return redirect(url_for('index'))

    if target_book['owner_id'] == g.user['id']:
        flash('Du kannst nicht dein eigenes Buch anfragen.', 'error')
        return redirect(url_for('index'))

    # Check if user already has an active pending request for this target book
    existing_req = g.db.execute(
        'SELECT id FROM exchange_requests WHERE requester_id = ? AND target_book_id = ? AND status = ?',
        (g.user['id'], book_id, REQUEST_PENDING)
    ).fetchone()
    if existing_req:
        flash('Du hast für dieses Buch bereits eine offene Tauschanfrage gestellt.', 'info')
        return redirect(url_for('requests_page'))

    if request.method == 'POST':
        offered_book_id = request.form.get('offered_book_id')
        if offered_book_id:
            # Check if user owns the offered book and it's available
            offered_book = g.db.execute('SELECT * FROM books WHERE id = ? AND owner_id = ? AND status = ?',
                                        (offered_book_id, g.user['id'], BOOK_AVAILABLE)).fetchone()
            if not offered_book:
                flash('Ungültiges Buch angeboten oder nicht mehr verfügbar.', 'error')
            else:
                g.db.execute('INSERT INTO exchange_requests (requester_id, target_book_id, offered_book_id) VALUES (?, ?, ?)',
                             (g.user['id'], book_id, offered_book_id))
                g.db.execute('UPDATE books SET status = ? WHERE id IN (?, ?)', (BOOK_PENDING, book_id, offered_book_id))
                g.db.commit()
                flash('Tauschanfrage erfolgreich gesendet!', 'success')
                return redirect(url_for('requests_page'))

    # Get user's available books to offer
    available_books = g.db.execute('SELECT * FROM books WHERE owner_id = ? AND status = ?', (g.user['id'], BOOK_AVAILABLE)).fetchall()
    return render_template('request_form.html', target_book=target_book, available_books=available_books)

@app.route('/requests', methods=['GET', 'POST'])
def requests_page():
    if not g.user:
        return redirect(url_for('login'))
        
    if request.method == 'POST':
        action = request.form.get('action')
        req_id = request.form.get('request_id')
        
        req = g.db.execute('''
            SELECT er.*, tb.owner_id as target_owner_id, tb.id as t_id, ob.id as o_id
            FROM exchange_requests er
            JOIN books tb ON er.target_book_id = tb.id
            JOIN books ob ON er.offered_book_id = ob.id
            WHERE er.id = ?
        ''', (req_id,)).fetchone()

        if not req:
            flash('Tauschanfrage nicht gefunden.', 'error')
            return redirect(url_for('requests_page'))

        # Jede Aktion beansprucht die Anfrage zunächst atomar per
        # "UPDATE ... WHERE status = PENDING" - das verhindert, dass zwei
        # parallele Requests (z.B. zwei offene Tabs) dieselbe Anfrage doppelt
        # verarbeiten (TOCTOU-Schutz statt reinem Vorab-SELECT).

        # Case A: Target owner accepts the request
        if action == 'accept' and req['target_owner_id'] == g.user['id']:
            claimed = g.db.execute(
                'UPDATE exchange_requests SET status = ? WHERE id = ? AND status = ?',
                (REQUEST_ACCEPTED, req_id, REQUEST_PENDING)
            ).rowcount
            if not claimed:
                g.db.rollback()
                flash('Diese Anfrage wurde bereits bearbeitet.', 'info')
                return redirect(url_for('requests_page'))

            g.db.execute('UPDATE books SET status = ? WHERE id IN (?, ?)', (BOOK_EXCHANGED, req['t_id'], req['o_id']))

            # Auto-reject competing pending requests and release their books
            competing_reqs = g.db.execute('''
                SELECT id, target_book_id, offered_book_id
                FROM exchange_requests
                WHERE id != ? AND status = ?
                  AND (target_book_id IN (?, ?) OR offered_book_id IN (?, ?))
            ''', (req_id, REQUEST_PENDING, req['t_id'], req['o_id'], req['t_id'], req['o_id'])).fetchall()

            for c_req in competing_reqs:
                g.db.execute('UPDATE exchange_requests SET status = ? WHERE id = ? AND status = ?',
                             (REQUEST_REJECTED, c_req['id'], REQUEST_PENDING))
                if c_req['offered_book_id'] not in (req['t_id'], req['o_id']):
                    g.db.execute('UPDATE books SET status = ? WHERE id = ?', (BOOK_AVAILABLE, c_req['offered_book_id']))
                if c_req['target_book_id'] not in (req['t_id'], req['o_id']):
                    g.db.execute('UPDATE books SET status = ? WHERE id = ?', (BOOK_AVAILABLE, c_req['target_book_id']))

            g.db.commit()
            flash('Tauschanfrage akzeptiert! Die Kontaktdaten wurden freigeschaltet.', 'success')

        # Case B: Target owner rejects the request
        elif action == 'reject' and req['target_owner_id'] == g.user['id']:
            claimed = g.db.execute(
                'UPDATE exchange_requests SET status = ? WHERE id = ? AND status = ?',
                (REQUEST_REJECTED, req_id, REQUEST_PENDING)
            ).rowcount
            if not claimed:
                g.db.rollback()
                flash('Diese Anfrage wurde bereits bearbeitet.', 'info')
                return redirect(url_for('requests_page'))
            g.db.execute('UPDATE books SET status = ? WHERE id IN (?, ?)', (BOOK_AVAILABLE, req['t_id'], req['o_id']))
            g.db.commit()
            flash('Anfrage abgelehnt. Beide Bücher sind wieder verfügbar.', 'info')

        # Case C: Requester cancels their own pending request
        elif action == 'cancel' and req['requester_id'] == g.user['id']:
            claimed = g.db.execute(
                'UPDATE exchange_requests SET status = ? WHERE id = ? AND status = ?',
                (REQUEST_CANCELLED, req_id, REQUEST_PENDING)
            ).rowcount
            if not claimed:
                g.db.rollback()
                flash('Diese Anfrage wurde bereits bearbeitet.', 'info')
                return redirect(url_for('requests_page'))
            g.db.execute('UPDATE books SET status = ? WHERE id IN (?, ?)', (BOOK_AVAILABLE, req['t_id'], req['o_id']))
            g.db.commit()
            flash('Tauschanfrage erfolgreich zurückgezogen. Beide Bücher stehen wieder zur Verfügung.', 'success')

        return redirect(url_for('requests_page'))

    incoming_requests = g.db.execute('''
        SELECT er.id, er.status, u.username as requester_name, u.email as requester_email,
               tb.title as target_title, ob.title as offered_title
        FROM exchange_requests er
        JOIN users u ON er.requester_id = u.id
        JOIN books tb ON er.target_book_id = tb.id
        JOIN books ob ON er.offered_book_id = ob.id
        WHERE tb.owner_id = ?
        ORDER BY er.id DESC
    ''', (g.user['id'],)).fetchall()

    outgoing_requests = g.db.execute('''
        SELECT er.id, er.status, u.username as target_owner_name, u.email as target_owner_email,
               tb.title as target_title, ob.title as offered_title
        FROM exchange_requests er
        JOIN books tb ON er.target_book_id = tb.id
        JOIN users u ON tb.owner_id = u.id
        JOIN books ob ON er.offered_book_id = ob.id
        WHERE er.requester_id = ?
        ORDER BY er.id DESC
    ''', (g.user['id'],)).fetchall()
    
    return render_template('requests.html', incoming=incoming_requests, outgoing=outgoing_requests)

@app.route('/impressum')
def impressum():
    return render_template('impressum.html')

@app.route('/book/<int:book_id>')
def book_detail(book_id):
    book = g.db.execute('''
        SELECT b.id, b.title, b.author, b.condition, b.genre, b.description, b.image_filename, b.status, u.username as owner_name 
        FROM books b 
        JOIN users u ON b.owner_id = u.id 
        WHERE b.id = ?
    ''', (book_id,)).fetchone()
    
    if not book:
        flash('Das angeforderte Buch wurde nicht gefunden.', 'error')
        return redirect(url_for('index'))
        
    return render_template('book_detail.html', book=book)

@app.route('/robots.txt')
def robots():
    sitemap_url = url_for('sitemap', _external=True)
    content = f"""User-agent: *
Allow: /
Disallow: /my-books
Disallow: /requests
Disallow: /request-book/
Disallow: /logout

Sitemap: {sitemap_url}
"""
    return Response(content, mimetype='text/plain')

@app.route('/sitemap.xml')
def sitemap():
    pages = [
        {'loc': url_for('index', _external=True), 'changefreq': 'daily', 'priority': '1.0'},
        {'loc': url_for('impressum', _external=True), 'changefreq': 'monthly', 'priority': '0.3'},
        {'loc': url_for('login', _external=True), 'changefreq': 'monthly', 'priority': '0.5'},
        {'loc': url_for('register', _external=True), 'changefreq': 'monthly', 'priority': '0.5'}
    ]
    
    available_books = g.db.execute('SELECT id FROM books WHERE status = ?', (BOOK_AVAILABLE,)).fetchall()
    for book in available_books:
        pages.append({
            'loc': url_for('book_detail', book_id=book['id'], _external=True),
            'changefreq': 'weekly',
            'priority': '0.8'
        })

    xml = ['<?xml version="1.0" encoding="UTF-8"?>']
    xml.append('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">')
    for page in pages:
        xml.append('  <url>')
        xml.append(f"    <loc>{page['loc']}</loc>")
        xml.append(f"    <changefreq>{page['changefreq']}</changefreq>")
        xml.append(f"    <priority>{page['priority']}</priority>")
        xml.append('  </url>')
    xml.append('</urlset>')
    
    return Response('\n'.join(xml), mimetype='application/xml')

if __name__ == '__main__':
    app.run(debug=True, port=3000)

