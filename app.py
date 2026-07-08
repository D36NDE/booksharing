from flask import Flask, render_template, request, redirect, url_for, session, flash, g
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import database
import os
import uuid

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'super_secret_dev_key_for_booksharing')

UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static/uploads')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 32 * 1024 * 1024  # 32 MB limit

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.errorhandler(413)
def request_entity_too_large(error):
    flash('Das hochgeladene Bild ist zu groß. Bitte wähle ein Bild unter 32 MB.', 'error')
    return redirect(request.referrer or url_for('my_books')), 413

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
        else:
            session.pop('user_id', None)
            g.user = None
    else:
        g.user = None

@app.teardown_request
def teardown_request(exception):
    db = getattr(g, 'db', None)
    if db is not None:
        db.close()

# --- Auth Routes ---
@app.route('/login', methods=['GET', 'POST'])
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
def register():
    if g.user:
        return redirect(url_for('index'))
        
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')
        
        if not email:
            flash('Bitte gib eine E-Mail Adresse für die Registrierung an.', 'error')
            return render_template('register.html')
            
        user = g.db.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
        if user:
            flash('Benutzername existiert bereits.', 'error')
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
    # Alle Bücher anzeigen, wenn nicht eingeloggt
    if g.user:
        books = g.db.execute('''
            SELECT b.id, b.title, b.author, b.condition, b.image_filename, u.username as owner_name 
            FROM books b 
            JOIN users u ON b.owner_id = u.id 
            WHERE b.owner_id != ? AND b.status = 'AVAILABLE'
        ''', (g.user['id'],)).fetchall()
    else:
        books = g.db.execute('''
            SELECT b.id, b.title, b.author, b.condition, b.image_filename, u.username as owner_name 
            FROM books b 
            JOIN users u ON b.owner_id = u.id 
            WHERE b.status = 'AVAILABLE'
        ''').fetchall()
    
    return render_template('index.html', books=books)

@app.route('/my-books', methods=['GET', 'POST'])
def my_books():
    if not g.user:
        flash('Bitte melde dich an, um ein Buch hochzuladen.', 'info')
        return redirect(url_for('login', next=url_for('my_books')))
        
    if request.method == 'POST':
        title = request.form.get('title')
        author = request.form.get('author')
        condition = request.form.get('condition')
        
        image_filename = None
        if 'image' in request.files:
            file = request.files['image']
            if file and file.filename != '' and allowed_file(file.filename):
                filename = secure_filename(file.filename)
                unique_filename = f"{uuid.uuid4().hex}_{filename}"
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], unique_filename))
                image_filename = unique_filename
        
        if title and author and condition:
            g.db.execute('INSERT INTO books (title, author, condition, owner_id, image_filename) VALUES (?, ?, ?, ?, ?)',
                         (title, author, condition, g.user['id'], image_filename))
            g.db.commit()
            flash('Buch erfolgreich hinzugefügt!', 'success')
        else:
            flash('Bitte alle Felder ausfüllen.', 'error')
            
        return redirect(url_for('my_books'))
        
    books = g.db.execute('SELECT * FROM books WHERE owner_id = ?', (g.user['id'],)).fetchall()
    return render_template('my_books.html', books=books)

@app.route('/request-book/<int:book_id>', methods=['GET', 'POST'])
def request_book(book_id):
    if not g.user:
        return redirect(url_for('login'))
        
    # Check if book exists and is available
    target_book = g.db.execute('SELECT * FROM books WHERE id = ? AND status = "AVAILABLE"', (book_id,)).fetchone()
    if not target_book:
        flash('Dieses Buch ist nicht verfügbar.', 'error')
        return redirect(url_for('index'))
        
    if target_book['owner_id'] == g.user['id']:
        flash('Du kannst nicht dein eigenes Buch anfragen.', 'error')
        return redirect(url_for('index'))

    if request.method == 'POST':
        offered_book_id = request.form.get('offered_book_id')
        if offered_book_id:
            # Check if user owns the offered book and it's available
            offered_book = g.db.execute('SELECT * FROM books WHERE id = ? AND owner_id = ? AND status = "AVAILABLE"',
                                        (offered_book_id, g.user['id'])).fetchone()
            if not offered_book:
                flash('Ungültiges Buch angeboten.', 'error')
            else:
                g.db.execute('INSERT INTO exchange_requests (requester_id, target_book_id, offered_book_id) VALUES (?, ?, ?)',
                             (g.user['id'], book_id, offered_book_id))
                # Optionally mark books as 'PENDING'
                g.db.execute('UPDATE books SET status = "PENDING" WHERE id IN (?, ?)', (book_id, offered_book_id))
                g.db.commit()
                flash('Tauschanfrage erfolgreich gesendet!', 'success')
                return redirect(url_for('index'))
                
    # Get user's available books to offer
    my_books = g.db.execute('SELECT * FROM books WHERE owner_id = ? AND status = "AVAILABLE"', (g.user['id'],)).fetchall()
    return render_template('request_form.html', target_book=target_book, my_books=my_books)

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
        
        if req and req['target_owner_id'] == g.user['id'] and req['status'] == 'PENDING':
            if action == 'accept':
                g.db.execute('UPDATE exchange_requests SET status = "ACCEPTED" WHERE id = ?', (req_id,))
                g.db.execute('UPDATE books SET status = "EXCHANGED" WHERE id IN (?, ?)', (req['t_id'], req['o_id']))
                # Any other pending requests involving these books should probably be rejected, but skipping for simplicity
                g.db.commit()
                flash('Anfrage akzeptiert!', 'success')
            elif action == 'reject':
                g.db.execute('UPDATE exchange_requests SET status = "REJECTED" WHERE id = ?', (req_id,))
                g.db.execute('UPDATE books SET status = "AVAILABLE" WHERE id IN (?, ?)', (req['t_id'], req['o_id']))
                g.db.commit()
                flash('Anfrage abgelehnt.', 'info')
                
        return redirect(url_for('requests_page'))

    incoming_requests = g.db.execute('''
        SELECT er.id, er.status, u.username as requester_name, u.email as requester_email,
               tb.title as target_title, ob.title as offered_title
        FROM exchange_requests er
        JOIN users u ON er.requester_id = u.id
        JOIN books tb ON er.target_book_id = tb.id
        JOIN books ob ON er.offered_book_id = ob.id
        WHERE tb.owner_id = ?
    ''', (g.user['id'],)).fetchall()

    outgoing_requests = g.db.execute('''
        SELECT er.id, er.status, u.username as target_owner_name, u.email as target_owner_email,
               tb.title as target_title, ob.title as offered_title
        FROM exchange_requests er
        JOIN books tb ON er.target_book_id = tb.id
        JOIN users u ON tb.owner_id = u.id
        JOIN books ob ON er.offered_book_id = ob.id
        WHERE er.requester_id = ?
    ''', (g.user['id'],)).fetchall()
    
    return render_template('requests.html', incoming=incoming_requests, outgoing=outgoing_requests)

@app.route('/impressum')
def impressum():
    return render_template('impressum.html')

if __name__ == '__main__':
    app.run(debug=True, port=3000)
