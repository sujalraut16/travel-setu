from dotenv import load_dotenv

load_dotenv()

from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, send_file
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import date, datetime
from email.message import EmailMessage
from importlib import import_module, util
import smtplib
import secrets
import os
import io
import re
from types import SimpleNamespace

from sqlalchemy import inspect, text, update

_flight_service_path = os.path.join(os.path.dirname(__file__), 'flight_service.py')
if not os.path.exists(_flight_service_path):
    _flight_service_path = os.path.join(os.path.dirname(__file__), 'flight_service (3).py')
_flight_spec = util.spec_from_file_location('travelsetu_flight_service', _flight_service_path)
flight_service = util.module_from_spec(_flight_spec)
_flight_spec.loader.exec_module(flight_service)
if not os.path.exists(flight_service.DEMO_FILE):
    _sample_flights = os.path.join(os.path.dirname(__file__), 'demo_flights (1).json')
    if os.path.exists(_sample_flights):
        flight_service.DEMO_FILE = _sample_flights
search_flights_live = flight_service.search_flights_live

try:
    flask_mail = import_module('flask_mail')
    Mail = flask_mail.Mail
    Message = flask_mail.Message
except ImportError:
    Mail = None
    Message = None

app = Flask(__name__)
app.config['SECRET_KEY'] = 'TravelSetu_Prototype_Key_2024'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///travelsetu.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['MAIL_SERVER'] = os.getenv('MAIL_SERVER', 'smtp.gmail.com')
app.config['MAIL_PORT'] = int(os.getenv('MAIL_PORT', '587'))
app.config['MAIL_USE_TLS'] = os.getenv('MAIL_USE_TLS', 'true').lower() == 'true'
app.config['MAIL_USE_SSL'] = os.getenv('MAIL_USE_SSL', 'false').lower() == 'true'
app.config['MAIL_USERNAME'] = os.getenv('MAIL_USERNAME')
app.config['MAIL_PASSWORD'] = os.getenv('MAIL_PASSWORD', '').replace(' ', '')
app.config['MAIL_DEFAULT_SENDER'] = os.getenv('MAIL_DEFAULT_SENDER', app.config['MAIL_USERNAME'])
app.config['RAZORPAY_KEY_ID'] = os.getenv('RAZORPAY_KEY_ID')
app.config['RAZORPAY_KEY_SECRET'] = os.getenv('RAZORPAY_KEY_SECRET')

try:
    razorpay = import_module('razorpay')
except ImportError:
    razorpay = None

try:
    reportlab = import_module('reportlab')
except ImportError:
    reportlab = None

db = SQLAlchemy(app)
mail = Mail(app) if Mail else None
login_manager = LoginManager(app)
login_manager.login_view = 'login'


class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    full_name = db.Column(db.String(100))
    role = db.Column(db.String(20), default='user')
    is_verified = db.Column(db.Boolean, default=False, nullable=False)
    otp = db.Column(db.String(6), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class Destination(db.Model):
    __tablename__ = 'destinations'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    description = db.Column(db.Text)
    category = db.Column(db.String(50))
    location = db.Column(db.String(150))
    price_per_day = db.Column(db.Float, default=0)
    best_season = db.Column(db.String(50))
    image_url = db.Column(db.String(300))
    rating = db.Column(db.Float, default=4.0)
    total_visits = db.Column(db.Integer, default=0)
    airport_iata = db.Column(db.String(3), nullable=True)


class Favorite(db.Model):
    __tablename__ = 'favorites'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    destination_id = db.Column(db.Integer, db.ForeignKey('destinations.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    __table_args__ = (db.UniqueConstraint('user_id', 'destination_id', name='uq_user_destination'),)


class Review(db.Model):
    __tablename__ = 'reviews'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    destination_id = db.Column(db.Integer, db.ForeignKey('destinations.id'), nullable=False)
    rating = db.Column(db.Integer, nullable=False)
    comment = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Service(db.Model):
    __tablename__ = 'services'
    id = db.Column(db.Integer, primary_key=True)
    service_type = db.Column(db.String(30), nullable=False)  # hotel / transport
    name = db.Column(db.String(150), nullable=False)
    location = db.Column(db.String(150))
    details = db.Column(db.String(250))
    price = db.Column(db.Float, default=0)


class Booking(db.Model):
    __tablename__ = 'bookings'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    service_id = db.Column(db.Integer, db.ForeignKey('services.id'), nullable=False)
    service_name = db.Column(db.String(150), nullable=False)
    service_type = db.Column(db.String(30), nullable=False)
    travel_date = db.Column(db.Date, nullable=False)
    guests = db.Column(db.Integer, default=1)
    status = db.Column(db.String(30), default='Requested')
    payment_order_id = db.Column(db.String(100), nullable=True)
    payment_id = db.Column(db.String(100), nullable=True)
    payment_signature = db.Column(db.String(200), nullable=True)
    paid_at = db.Column(db.DateTime, nullable=True)
    outbound_flight = db.Column(db.String(200), nullable=True)
    return_flight = db.Column(db.String(200), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class FlightInventory(db.Model):
    __tablename__ = 'flight_inventory'
    id = db.Column(db.Integer, primary_key=True)
    flight_number = db.Column(db.String(30), nullable=False)
    date = db.Column(db.Date, nullable=False)
    price = db.Column(db.Float, nullable=False, default=0)
    total_seats = db.Column(db.Integer, nullable=False, default=60)
    seats_booked = db.Column(db.Integer, nullable=False, default=0)
    __table_args__ = (
        db.UniqueConstraint('flight_number', 'date', name='uq_flight_inventory_number_date'),
    )


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


def email_delivery_configured():
    password = app.config['MAIL_PASSWORD']
    return bool(
        app.config['MAIL_USERNAME']
        and password
        and not password.startswith('PASTE_')
        and 'your-' not in password.lower()
    )


def razorpay_configured():
    key_id = app.config['RAZORPAY_KEY_ID'] or ''
    key_secret = app.config['RAZORPAY_KEY_SECRET'] or ''
    return bool(
        razorpay and key_id.startswith('rzp_test_')
        and not key_id.endswith('paste_your_key_id_here')
        and key_secret
        and not key_secret.lower().startswith('paste_')
    )


def send_otp(email, otp_code):
    """Attempt Flask-Mail first, fall back to smtplib if needed."""
    if not email_delivery_configured():
        print(f"[MAIL CONFIG MISSING]: Set MAIL_USERNAME and MAIL_PASSWORD in .env")
        print(f"[DEBUG OTP]: Code for {email} is {otp_code}")
        return False

    # Try standard smtplib delivery
    return send_otp_email_smtp(email, otp_code)


def send_otp_email_smtp(email, otp_code):
    message = EmailMessage()
    message['Subject'] = 'TravelSetu - Verification Code'
    message['From'] = app.config['MAIL_DEFAULT_SENDER'] or app.config['MAIL_USERNAME']
    message['To'] = email
    message.set_content(
        f'Welcome to TravelSetu!\n\nYour account verification OTP code is: {otp_code}\n\n'
        'This code will expire when you request a new one.'
    )
    try:
        if app.config['MAIL_USE_SSL']:
            with smtplib.SMTP_SSL(app.config['MAIL_SERVER'], app.config['MAIL_PORT']) as smtp:
                smtp.login(app.config['MAIL_USERNAME'], app.config['MAIL_PASSWORD'])
                smtp.send_message(message)
        else:
            with smtplib.SMTP(app.config['MAIL_SERVER'], app.config['MAIL_PORT']) as smtp:
                if app.config['MAIL_USE_TLS']:
                    smtp.starttls()
                smtp.login(app.config['MAIL_USERNAME'], app.config['MAIL_PASSWORD'])
                smtp.send_message(message)
        print(f'[EMAIL SENT]: Delivered OTP successfully to {email}')
        return True
    except Exception as error:
        print(f'[EMAIL ERROR]: Could not deliver OTP to {email}: {error}')
        print(f"[DEBUG OTP]: Code for {email} is {otp_code}")
        return False


def send_booking_confirmation(user, booking):
    if not email_delivery_configured():
        print(f'[MAIL CONFIG MISSING]: Booking confirmation for {user.email}')
        return False
    message = EmailMessage()
    message['Subject'] = f'TravelSetu booking confirmed - #{booking.id}'
    message['From'] = app.config['MAIL_DEFAULT_SENDER'] or app.config['MAIL_USERNAME']
    message['To'] = user.email
    message.set_content(
        f'Your TravelSetu booking is confirmed.\n\n'
        f'Booking ID: {booking.id}\nService: {booking.service_name}\n'
        f'Travel date: {booking.travel_date}\nGuests: {booking.guests}\n\n'
        f'Download your invoice: {url_for("download_invoice", booking_id=booking.id, _external=True)}'
    )
    try:
        with smtplib.SMTP(app.config['MAIL_SERVER'], app.config['MAIL_PORT']) as smtp:
            if app.config['MAIL_USE_TLS']:
                smtp.starttls()
            smtp.login(app.config['MAIL_USERNAME'], app.config['MAIL_PASSWORD'])
            smtp.send_message(message)
        print(f'[EMAIL SENT]: Booking confirmation delivered to {user.email}')
        return True
    except Exception as error:
        print(f'[EMAIL ERROR]: Could not deliver booking confirmation: {error}')
        return False


def ensure_user_columns():
    columns = {column['name'] for column in inspect(db.engine).get_columns('users')}
    if 'is_verified' not in columns:
        db.session.execute(text('ALTER TABLE users ADD COLUMN is_verified BOOLEAN NOT NULL DEFAULT 0'))
    if 'otp' not in columns:
        db.session.execute(text('ALTER TABLE users ADD COLUMN otp VARCHAR(6)'))
    db.session.commit()


def ensure_destination_columns():
    columns = {column['name'] for column in inspect(db.engine).get_columns('destinations')}
    if 'airport_iata' not in columns:
        db.session.execute(text('ALTER TABLE destinations ADD COLUMN airport_iata VARCHAR(3)'))
    db.session.commit()

    default_airports = {
        'Leh Ladakh': 'IXL',
        'Goa': 'GOI',
        'Kerala': 'COK',
        'Manali': 'KUU',
        'Jaipur': 'JAI',
        'Andaman Islands': 'IXZ',
        'Andaman': 'IXZ',
        'Udaipur': 'UDR',
        'Mumbai': 'BOM',
        'Delhi': 'DEL',
        'Pune': 'PNQ',
        'Bengaluru': 'BLR',
        'Hyderabad': 'HYD',
    }

    for destination_name, airport_code in default_airports.items():
        destination = Destination.query.filter(db.func.lower(Destination.name) == destination_name.lower()).first()
        if destination and (not destination.airport_iata or destination.airport_iata.strip() == ''):
            destination.airport_iata = airport_code
    db.session.commit()


def ensure_booking_columns():
    columns = {column['name'] for column in inspect(db.engine).get_columns('bookings')}
    additions = {
        'payment_order_id': 'VARCHAR(100)',
        'payment_id': 'VARCHAR(100)',
        'payment_signature': 'VARCHAR(200)',
        'paid_at': 'DATETIME',
        'outbound_flight': 'VARCHAR(200)',
        'return_flight': 'VARCHAR(200)'
    }
    for name, column_type in additions.items():
        if name not in columns:
            db.session.execute(text(f'ALTER TABLE bookings ADD COLUMN {name} {column_type}'))
    db.session.commit()


def get_destination_image(destination, width=400, height=300):
    if destination.image_url and destination.image_url.startswith(('http://', 'https://', '/')):
        return destination.image_url
    category_images = {
        'beaches': 'photo-1507525428034-b723cf961d3e',
        'adventure': 'photo-1464822759023-fed622ff2c3b',
        'religious': 'photo-1548013146-72479768bada',
        'heritage': 'photo-1524492412937-b28074a5d7da'
    }
    image_id = category_images.get(destination.category, 'photo-1469474968028-56623f02e42e')
    return f'https://images.unsplash.com/{image_id}?auto=format&fit=crop&w={width}&h={height}&q=85'


@app.route('/')
def index():
    featured = Destination.query.order_by(Destination.rating.desc()).limit(3).all()
    return render_template(
        'index.html',
        featured=featured,
        total_destinations=Destination.query.count(),
        total_services=Service.query.count(),
        total_users=User.query.count(),
        categories=sorted({d.category for d in Destination.query.all() if d.category})
    )


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        full_name = request.form.get('full_name', '').strip() or username

        if not username or not email or not password:
            flash('Please complete all required fields.', 'danger')
            return redirect(url_for('register'))

        if User.query.filter((User.username == username) | (User.email == email)).first():
            flash('Username or email already exists.', 'danger')
            return redirect(url_for('register'))

        otp_code = f'{secrets.randbelow(900000) + 100000:06d}'
        user = User(username=username, email=email, full_name=full_name, otp=otp_code, is_verified=False)
        user.set_password(password)

        db.session.add(user)
        db.session.commit()

        print(f'[OTP EMAIL]: Sending verification code to {email}')
        sent = send_otp(email, otp_code)

        if sent:
            flash(f'Verification code sent to {email}. Please check your inbox.', 'success')
        else:
            flash('Registration created. Check terminal output for debug OTP if email delivery failed.', 'warning')

        return redirect(url_for('verify_otp', email=email))

    return render_template('register.html')


@app.route('/verify-otp/<email>', methods=['GET', 'POST'])
def verify_otp(email):
    email = email.strip().lower()
    user = User.query.filter_by(email=email).first_or_404()

    if user.is_verified and not user.otp:
        flash('Your email is already verified. Please login.', 'success')
        return redirect(url_for('login'))

    if request.method == 'POST':
        otp_code = request.form.get('otp', '').strip()
        if user.otp and otp_code == user.otp:
            user.is_verified = True
            user.otp = None
            db.session.commit()
            login_user(user)
            flash('Email verified successfully! Welcome to TravelSetu.', 'success')
            return redirect(url_for('index'))

        flash('Invalid verification code. Please try again.', 'danger')

    return render_template('verify_otp.html', email=email)


@app.route('/resend-otp/<email>', methods=['POST'])
def resend_otp(email):
    email = email.strip().lower()
    user = User.query.filter_by(email=email).first_or_404()
    if user.is_verified and not user.otp:
        flash('This email is already verified. Please login.', 'info')
        return redirect(url_for('login'))
    user.otp = f'{secrets.randbelow(900000) + 100000:06d}'
    db.session.commit()
    sent = send_otp(user.email, user.otp)
    if sent:
        flash('A new verification code was sent. Check Inbox, Spam, and Promotions.', 'success')
    else:
        flash('Email delivery failed. Use the OTP printed in the Flask terminal.', 'warning')
    return redirect(url_for('verify_otp', email=user.email))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')

        user = User.query.filter_by(username=username).first()

        if user and user.check_password(password):
            if not user.is_verified:
                flash('Your account is not verified. Please enter the OTP sent to your email.', 'danger')
                return redirect(url_for('verify_otp', email=user.email))

            user.otp = f'{secrets.randbelow(900000) + 100000:06d}'
            db.session.commit()
            sent = send_otp(user.email, user.otp)
            if sent:
                flash('A new verification code was sent to your email.', 'success')
            else:
                flash('Check the terminal for your verification code.', 'warning')
            return redirect(url_for('verify_otp', email=user.email))

        flash('Invalid username or password.', 'danger')

    return render_template('login.html')


@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('index'))


@app.route('/destinations')
def destinations():
    category = request.args.get('category', '').strip()
    current_sort = request.args.get('sort', 'rating')
    query = Destination.query
    if category:
        query = query.filter_by(category=category)
    sort_columns = {
        'rating': Destination.rating.desc(),
        'price_low': Destination.price_per_day.asc(),
        'price_high': Destination.price_per_day.desc(),
        'name': Destination.name.asc()
    }
    places = query.order_by(sort_columns.get(current_sort, sort_columns['rating'])).all()
    categories = sorted({d.category for d in Destination.query.all() if d.category})
    return render_template('destinations.html', destinations=places, categories=categories, current_category=category, current_sort=current_sort)


@app.route('/destination/<int:id>', methods=['GET', 'POST'])
def destination_detail(id):
    destination = Destination.query.get_or_404(id)
    destination.total_visits = (destination.total_visits or 0) + 1
    db.session.commit()
    if request.method == 'POST':
        if not current_user.is_authenticated:
            flash('Please login to add favorites or reviews.', 'danger')
            return redirect(url_for('login'))

        action = request.form.get('action')
        if action == 'favorite':
            existing = Favorite.query.filter_by(user_id=current_user.id, destination_id=id).first()
            if existing:
                db.session.delete(existing)
                flash('Removed from favorites.', 'success')
            else:
                db.session.add(Favorite(user_id=current_user.id, destination_id=id))
                flash('Destination saved to favorites.', 'success')
            db.session.commit()
            return redirect(url_for('destination_detail', id=id))

        if action == 'review':
            try:
                rating = int(request.form.get('rating', '5'))
            except ValueError:
                rating = 5
            rating = min(max(rating, 1), 5)
            comment = request.form.get('comment', '').strip()
            if not comment:
                flash('Please enter a review comment.', 'danger')
                return redirect(url_for('destination_detail', id=id))
            db.session.add(Review(user_id=current_user.id, destination_id=id, rating=rating, comment=comment))
            db.session.commit()
            flash('Review added.', 'success')
            return redirect(url_for('destination_detail', id=id))

    is_favorite = False
    if current_user.is_authenticated:
        is_favorite = Favorite.query.filter_by(user_id=current_user.id, destination_id=id).first() is not None
    reviews = Review.query.filter_by(destination_id=id).order_by(Review.created_at.desc()).all()
    review_users = {u.id: u for u in User.query.filter(User.id.in_([r.user_id for r in reviews])).all()} if reviews else {}
    return render_template(
        'destination_detail.html',
        destination=destination,
        is_favorite=is_favorite,
        reviews=reviews,
        review_users=review_users,
        avg_rating=sum(r.rating for r in reviews) / len(reviews) if reviews else destination.rating,
        similar=Destination.query.filter(Destination.id != id, Destination.category == destination.category).limit(3).all()
    )


@app.route('/destination/<int:dest_id>/review', methods=['POST'])
@login_required
def add_review(dest_id):
    destination = Destination.query.get_or_404(dest_id)
    try:
        rating = min(max(int(request.form.get('rating', '5')), 1), 5)
    except ValueError:
        rating = 5
    comment = request.form.get('comment', '').strip()
    if comment:
        db.session.add(Review(user_id=current_user.id, destination_id=destination.id, rating=rating, comment=comment))
        db.session.commit()
        flash('Review added.', 'success')
    else:
        flash('Please enter a review comment.', 'danger')
    return redirect(url_for('destination_detail', id=destination.id))


@app.route('/book-destination/<int:dest_id>', methods=['GET', 'POST'])
@login_required
def book_destination(dest_id):
    destination = Destination.query.get(dest_id)
    service = Service.query.first()
    if not destination and service:
        destination = SimpleNamespace(id=service.id, name=service.name, location=service.location, price_per_day=service.price)
    if not destination:
        return redirect(url_for('destinations'))
    if request.method == 'POST':
        if not service:
            flash('No booking service is configured yet.', 'danger')
            return redirect(url_for('destinations'))

        flight_departure_date = request.form.get('flight_departure_date', '').strip()
        origin = request.form.get('origin', '').strip().upper()
        if not flight_departure_date or not origin:
            flash('Please select a departure date and departure airport before continuing.', 'danger')
            return redirect(url_for('book_destination', dest_id=dest_id))

        check_in = request.form.get('check_in', '').strip()
        check_out = request.form.get('check_out', '').strip()
        if not check_in or not check_out:
            flash('Please confirm flight availability first, then choose the check-in and check-out dates.', 'danger')
            return redirect(url_for('book_destination', dest_id=dest_id))

        try:
            travel_date = datetime.strptime(check_in, '%Y-%m-%d').date()
            guests = max(1, int(request.form.get('guests', '1')))
        except (ValueError, TypeError):
            flash('Please enter a valid check-in date and guest count.', 'danger')
            return redirect(url_for('book_destination', dest_id=dest_id))

        booking = Booking(
            user_id=current_user.id,
            service_id=service.id,
            service_name=destination.name,
            service_type='destination',
            travel_date=travel_date,
            guests=guests,
            status='Requested',
            outbound_flight=' | '.join(filter(None, (
                request.form.get('outbound_flight_number', '').strip(),
                request.form.get('outbound_flight_date', '').strip(),
                request.form.get('outbound_flight_departure_time', '').strip(),
            ))) or None,
            return_flight=' | '.join(filter(None, (
                request.form.get('return_flight_number', '').strip(),
                request.form.get('return_flight_date', '').strip(),
                request.form.get('return_flight_departure_time', '').strip(),
            ))) or None,
        )
        db.session.add(booking)
        db.session.commit()
        flash('Destination booking request created.', 'success')
        return redirect(url_for('payment_page', booking_id=booking.id))
    return render_template('booking.html', destination=destination, today=datetime.now().date().isoformat())


@app.route('/api/flight-availability')
def flight_availability():
    origin = request.args.get('origin', '').strip().upper()
    destination = request.args.get('destination', '').strip().upper()
    travel_date = request.args.get('date', '').strip()

    # Reject invalid inputs before asking the flight service for results.
    if not re.fullmatch(r'[A-Z]{3}', origin) or not re.fullmatch(r'[A-Z]{3}', destination):
        return jsonify(flights=[], count=0, updated_at=None, source=None, error='Origin and destination must be 3-letter airport codes.'), 400
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', travel_date):
        return jsonify(flights=[], count=0, updated_at=None, source=None, error='Date must use YYYY-MM-DD format.'), 400
    try:
        parsed_date = date.fromisoformat(travel_date)
    except ValueError:
        return jsonify(flights=[], count=0, updated_at=None, source=None, error='Enter a valid date.'), 400
    if parsed_date < date.today():
        return jsonify(flights=[], count=0, updated_at=None, source=None, error='Date cannot be in the past.'), 400

    result = search_flights_live(origin, destination, travel_date)
    all_flights = result.get('flights') or []
    updated_at = result.get('updated_at')
    if updated_at is not None:
        updated_at = datetime.fromtimestamp(updated_at).strftime('%H:%M:%S')
    source = result.get('source') or next(
        (flight.get('source') for flight in all_flights if flight.get('source')),
        flight_service.PROVIDER,
    )

    return jsonify(
        flights=[{
            'airline': flight.get('airline'),
            'flight_number': flight.get('flight_number'),
            'departure_time': flight.get('departure_time'),
            'arrival_time': flight.get('arrival_time'),
            'status': flight.get('status'),
        } for flight in all_flights[:6]],
        count=len(all_flights),
        updated_at=updated_at,
        source=source,
        error=result.get('error'),
    )


@app.route('/search')
def search():
    term = request.args.get('q', '').strip()
    budget = request.args.get('budget', '').strip()
    location = request.args.get('location', '').strip()
    interests = request.args.get('interests', '').strip()
    category = request.args.get('category', '').strip()
    query = Destination.query
    terms = [term, location, interests]
    for value in terms:
        if value:
            like = f'%{value}%'
            query = query.filter((Destination.name.ilike(like)) | (Destination.location.ilike(like)) | (Destination.description.ilike(like)))
    if category:
        query = query.filter_by(category=category)
    if budget:
        try:
            query = query.filter(Destination.price_per_day <= float(budget))
        except ValueError:
            budget = ''
    return render_template('search.html', results=query.order_by(Destination.rating.desc()).all() if any(terms) or category or budget else [], term=term, budget=budget, location=location, interests=interests, category=category, all_categories=sorted({d.category for d in Destination.query.all() if d.category}))


@app.route('/favorites')
@login_required
def favorites():
    favorite_rows = Favorite.query.filter_by(user_id=current_user.id).order_by(Favorite.created_at.desc()).all()
    destination_map = {d.id: d for d in Destination.query.filter(Destination.id.in_([f.destination_id for f in favorite_rows])).all()} if favorite_rows else {}
    return render_template('favorites.html', favorites=favorite_rows, destination_map=destination_map)


@app.route('/services')
def services():
    hotels = Service.query.filter_by(service_type='hotel').all()
    transports = Service.query.filter_by(service_type='transport').all()
    return render_template('services.html', hotels=hotels, transports=transports)


@app.route('/book/<int:service_id>', methods=['GET', 'POST'])
@login_required
def create_booking(service_id):
    service = Service.query.get_or_404(service_id)
    if request.method == 'POST':
        date_text = request.form.get('travel_date', '').strip()
        guests_text = request.form.get('guests', '1').strip()
        try:
            travel_date = datetime.strptime(date_text, '%Y-%m-%d').date()
            guests = max(1, int(guests_text))
        except (ValueError, TypeError):
            flash('Please enter a valid date and number of guests.', 'danger')
            return redirect(url_for('create_booking', service_id=service_id))
        booking = Booking(
            user_id=current_user.id,
            service_id=service.id,
            service_name=service.name,
            service_type=service.service_type,
            travel_date=travel_date,
            guests=guests,
            status='Requested'
        )
        db.session.add(booking)
        db.session.commit()
        flash('Booking request created.', 'success')
        return redirect(url_for('my_bookings'))
    destination = SimpleNamespace(
        id=service.id,
        name=service.name,
        location=service.location,
        price_per_day=service.price
    )
    return render_template('booking.html', destination=destination, today=datetime.now().date().isoformat())


@app.route('/my-bookings')
@login_required
def my_bookings():
    bookings = Booking.query.filter_by(user_id=current_user.id).order_by(Booking.created_at.desc()).all()
    return render_template('my_bookings.html', bookings=bookings)


@app.route('/payment/<int:booking_id>')
@login_required
def payment_page(booking_id):
    booking = Booking.query.filter_by(id=booking_id, user_id=current_user.id).first_or_404()
    if booking.status.lower() == 'confirmed':
        flash('This booking is already confirmed and paid.', 'info')
        return redirect(url_for('download_invoice', booking_id=booking.id))
    amount = max(1, int((booking.service_id and Service.query.get(booking.service_id).price or 0) * (booking.guests or 1) * 100))
    order = None
    if razorpay_configured():
        client = razorpay.Client(auth=(app.config['RAZORPAY_KEY_ID'], app.config['RAZORPAY_KEY_SECRET']))
        order = client.order.create({'amount': amount, 'currency': 'INR', 'receipt': f'travelsetu_{booking.id}', 'notes': {'booking_id': str(booking.id)}})
        booking.payment_order_id = order['id']
        db.session.commit()
    payment_booking = SimpleNamespace(
        id=booking.id, booking_type=booking.service_type, booking_ref=booking.id,
        details=booking.service_name, check_in=booking.travel_date, guests=booking.guests,
        total_price=amount // 100
    )
    return render_template('payment.html', booking=payment_booking, razorpay_order=order, razorpay_key_id=app.config['RAZORPAY_KEY_ID'], payment_available=order is not None)


@app.route('/payment/<int:booking_id>/confirm', methods=['POST'])
@login_required
def confirm_payment(booking_id):
    booking = Booking.query.filter_by(id=booking_id, user_id=current_user.id).first_or_404()
    data = request.get_json(silent=True) or request.form
    payment_id = data.get('razorpay_payment_id')
    order_id = data.get('razorpay_order_id')
    signature = data.get('razorpay_signature')
    if not all((payment_id, order_id, signature)) or order_id != booking.payment_order_id:
        return jsonify({'success': False, 'error': 'Incomplete or mismatched payment details.'}), 400
    if not razorpay_configured():
        return jsonify({'success': False, 'error': 'Payment gateway is not configured.'}), 503
    client = razorpay.Client(auth=(app.config['RAZORPAY_KEY_ID'], app.config['RAZORPAY_KEY_SECRET']))
    try:
        client.utility.verify_payment_signature({'razorpay_order_id': order_id, 'razorpay_payment_id': payment_id, 'razorpay_signature': signature})
    except Exception:
        return jsonify({'success': False, 'error': 'Payment verification failed.'}), 400
    booking.status = 'Confirmed'
    booking.payment_id = payment_id
    booking.payment_signature = signature
    booking.paid_at = datetime.utcnow()
    db.session.commit()
    send_booking_confirmation(current_user, booking)
    return jsonify({'success': True, 'redirect': url_for('payment_success', booking_id=booking.id)})


@app.route('/payment/<int:booking_id>/success')
@login_required
def payment_success(booking_id):
    booking = Booking.query.filter_by(id=booking_id, user_id=current_user.id).first_or_404()
    flash('Payment confirmed. Your booking invoice is ready.', 'success')
    return render_template('payment_success.html', booking=booking)


@app.route('/booking/<int:booking_id>/invoice')
@login_required
def download_invoice(booking_id):
    booking = Booking.query.filter_by(id=booking_id, user_id=current_user.id).first_or_404()
    if not reportlab:
        return 'Invoice generation is unavailable. Install reportlab.', 503
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    output = io.BytesIO()
    pdf = canvas.Canvas(output, pagesize=A4)
    pdf.setTitle(f'TravelSetu Invoice {booking.id}')
    pdf.setFont('Helvetica-Bold', 20)
    pdf.drawString(60, 790, 'TravelSetu')
    pdf.setFont('Helvetica', 11)
    lines = [
        f'Invoice / Booking #{booking.id}', f'Customer: {current_user.full_name or current_user.username}',
        f'Email: {current_user.email}', f'Service: {booking.service_name}',
        f'Type: {booking.service_type}', f'Travel date: {booking.travel_date}',
        f'Guests: {booking.guests}', f'Status: {booking.status}',
        f'Payment ID: {booking.payment_id or "Pending"}',
    ]
    y = 745
    for line in lines:
        pdf.drawString(60, y, line)
        y -= 24
    pdf.setFont('Helvetica-Bold', 13)
    pdf.drawString(60, y - 10, 'Thank you for travelling with TravelSetu.')
    pdf.save()
    output.seek(0)
    return send_file(output, as_attachment=True, download_name=f'travelsetu-invoice-{booking.id}.pdf', mimetype='application/pdf')


@app.route('/booking/<int:booking_id>/cancel')
@login_required
def cancel_booking(booking_id):
    booking = Booking.query.filter_by(id=booking_id, user_id=current_user.id).first_or_404()
    booking.status = 'Cancelled'
    db.session.commit()
    flash('Booking cancelled.', 'success')
    return redirect(url_for('dashboard'))


def catalog_item(**values):
    return SimpleNamespace(**values)


def catalog_data():
    return {
        'flights': [catalog_item(id=1, icon='✈️', airline='TravelSetu Air', flight_no='TS101', **{'class': 'Economy'}, departure='08:10', arrival='10:20', duration='2h 10m', stops='Non-stop', baggage='15kg', meal='Included', refundable=True, seats=6, price=3499, from_city='Mumbai', to_city='Delhi', date='')],
        'hotels': [catalog_item(id=1, name='Lakeview Grand', stars=4, rating=4.6, amenities='Wi-Fi · Breakfast · Pool', price=2800)],
        'trains': [catalog_item(id=1, name='TravelSetu Express', number='TS-1201', type='AC Express', days='MTWTFSS', departure='06:30', arrival='14:45', duration='8h 15m', from_city='Mumbai', to_city='Delhi', classes=[catalog_item(name='3A', price=1450, available=18), catalog_item(name='2A', price=2200, available=6)])],
        'buses': [catalog_item(id=1, operator='TravelSetu Travels', type='AC Sleeper', rating=4.4, departure='21:00', arrival='06:30', duration='9h 30m', from_city='Delhi', to_city='Jaipur', amenities=['Wi-Fi', 'Water'], price=899, seats=12)],
        'cabs': [catalog_item(id=1, icon='🚙', type='Sedan', model='Comfort Sedan', capacity=4, per_km=14, price=850, pickup='', drop='')],
        'villas': [catalog_item(id=1, name='Aravali Retreat', location='Udaipur', bedrooms=3, guests=6, amenities='Pool · Kitchen · Wi-Fi', rating=4.8, price=6500)],
        'packages': [catalog_item(id=1, name='Golden Triangle Escape', destinations='Delhi · Agra · Jaipur', duration='5 Days / 4 Nights', includes='Hotels, breakfast and transfers', rating=4.7, price=24999)],
        'tours': [catalog_item(id=1, name='Heritage City Walk', location='Jaipur', duration='4 hours', includes='Local guide and refreshments', rating=4.8, price=1200)]
    }


def render_catalog(template, key, **context):
    values = catalog_data()[key]
    return render_template(template, **{key: values, **context})


@app.route('/dashboard')
@login_required
def dashboard():
    bookings = Booking.query.filter_by(user_id=current_user.id).order_by(Booking.created_at.desc()).all()
    booking_views = [SimpleNamespace(id=b.id, booking_ref=b.id, booking_type=b.service_type, details=b.service_name, total_price=0, status='pending' if b.status.lower() == 'requested' else b.status.lower(), destination=None) for b in bookings]
    return render_template(
        'dashboard.html', bookings=booking_views, total_bookings=len(bookings),
        confirmed_bookings=sum(b.status.lower() == 'confirmed' for b in bookings),
        total_spent=sum((b.guests or 1) * 0 for b in bookings),
        recommendations=Destination.query.order_by(Destination.rating.desc()).limit(4).all()
    )


@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    if request.method == 'POST':
        current_user.full_name = request.form.get('full_name', '').strip() or current_user.username
        db.session.commit()
        flash('Profile updated.', 'success')
    bookings = Booking.query.filter_by(user_id=current_user.id).order_by(Booking.created_at.desc()).all()
    booking_views = [SimpleNamespace(id=b.id, booking_type=b.service_type, total_price=0, status=b.status.lower(), destination=None, check_in=b.travel_date) for b in bookings]
    return render_template('profile.html', bookings=booking_views)


@app.route('/notifications')
@login_required
def notifications():
    return render_template('notifications.html', notifications=[])


@app.route('/notifications/clear')
@login_required
def clear_notifications():
    flash('Notifications cleared.', 'success')
    return redirect(url_for('notifications'))


@app.route('/itinerary', methods=['GET', 'POST'])
def itinerary():
    all_destinations = Destination.query.order_by(Destination.name).all()
    destination = None
    days = int(request.form.get('days', request.args.get('days', 3))) if request.method == 'POST' or request.args.get('days') else 3
    itinerary_items = []
    destination_id = request.form.get('destination_id', request.args.get('destination_id'))
    if destination_id:
        destination = Destination.query.get_or_404(int(destination_id))
        itinerary_items = [SimpleNamespace(day=day, day_name='Explore', date=f'Day {day}', activities=[SimpleNamespace(time='10:00', activity=f'Discover {destination.name}'), SimpleNamespace(time='18:00', activity='Local dinner and evening walk')]) for day in range(1, min(max(days, 1), 10) + 1)]
    return render_template('itinerary.html', all_destinations=all_destinations, destination=destination, days=days, itinerary=itinerary_items)


@app.route('/flights')
def flights():
    origin = request.args.get('origin', '').strip().upper()
    destination = request.args.get('destination', '').strip().upper()
    travel_date = request.args.get('date', '').strip()
    results = []
    error = None
    searched = bool(origin and destination and travel_date)

    if searched:
        try:
            flight_date = date.fromisoformat(travel_date)
            response = search_flights_live(origin, destination, travel_date)
            results = response.get('flights', [])
            error = response.get('error')

            for flight in results:
                inventory = FlightInventory.query.filter_by(
                    flight_number=flight['flight_number'], date=flight_date
                ).first()
                flight_price = flight.get('price')
                if inventory is None:
                    inventory = FlightInventory(
                        flight_number=flight['flight_number'], date=flight_date,
                        price=flight_price or 0, total_seats=60, seats_booked=0
                    )
                    db.session.add(inventory)
                    db.session.flush()
                elif flight_price is not None:
                    inventory.price = flight_price

                flight['price'] = inventory.price
                flight['seats_left'] = max(inventory.total_seats - inventory.seats_booked, 0)
            db.session.commit()
        except ValueError:
            error = 'Enter a valid travel date.'
        except Exception:
            db.session.rollback()
            error = 'Flight search is unavailable right now. Please try again.'

    return render_template(
        'flights.html', results=results, origin=origin,
        destination=destination, travel_date=travel_date,
        error=error, searched=searched
    )


@app.route('/flights/book', methods=['POST'])
@login_required
def book_flight():
    flight_number = request.form.get('flight_number', '').strip()
    origin = request.form.get('origin', '').strip().upper()
    destination = request.form.get('destination', '').strip().upper()
    try:
        flight_date = date.fromisoformat(request.form.get('date', ''))
        passengers = int(request.form.get('passengers', '1'))
    except (TypeError, ValueError):
        flash('Enter a valid date and passenger count.', 'danger')
        return redirect(url_for('flights'))

    inventory = FlightInventory.query.filter_by(
        flight_number=flight_number, date=flight_date
    ).first()
    if not inventory or passengers < 1 or passengers > 9:
        flash('That flight or passenger count is not valid. Search again.', 'danger')
        return redirect(url_for('flights', origin=origin, destination=destination, date=flight_date.isoformat()))

    # The conditional update and booking insert commit together, preventing overbooking.
    seat_update = db.session.execute(
        update(FlightInventory)
        .where(
            FlightInventory.id == inventory.id,
            FlightInventory.total_seats - FlightInventory.seats_booked >= passengers
        )
        .values(seats_booked=FlightInventory.seats_booked + passengers)
    )
    if seat_update.rowcount != 1:
        db.session.rollback()
        flash('Not enough seats remain on this flight.', 'danger')
        return redirect(url_for('flights', origin=origin, destination=destination, date=flight_date.isoformat()))

    service_name = f'{flight_number}: {origin} to {destination}'
    service = Service.query.filter_by(
        service_type='flight', name=flight_number, location=f'{origin}-{destination}'
    ).first()
    if service is None:
        service = Service(
            service_type='flight', name=flight_number, location=f'{origin}-{destination}',
            details=service_name, price=inventory.price
        )
        db.session.add(service)
        db.session.flush()

    booking = Booking(
        user_id=current_user.id, service_id=service.id,
        service_name=service_name, service_type='flight',
        travel_date=flight_date, guests=passengers, status='Requested'
    )
    db.session.add(booking)
    db.session.commit()
    flash('Your flight booking was received.', 'success')
    return redirect(url_for('flights', origin=origin, destination=destination, date=flight_date.isoformat()))


@app.route('/hotels', methods=['GET', 'POST'])
def hotels():
    city = request.form.get('city', '') if request.method == 'POST' else request.args.get('city', '')
    return render_catalog('hotels.html', 'hotels', hotels=catalog_data()['hotels'] if city else [], city=city)


@app.route('/trains', methods=['GET', 'POST'])
def trains():
    from_city = request.form.get('from', '') if request.method == 'POST' else ''
    to_city = request.form.get('to', '') if request.method == 'POST' else ''
    return render_catalog('trains.html', 'trains', trains=catalog_data()['trains'] if from_city and to_city else [], from_city=from_city, to_city=to_city)


@app.route('/buses', methods=['GET', 'POST'])
def buses():
    from_city = request.form.get('from', '') if request.method == 'POST' else ''
    to_city = request.form.get('to', '') if request.method == 'POST' else ''
    return render_catalog('buses.html', 'buses', buses=catalog_data()['buses'] if from_city and to_city else [], from_city=from_city, to_city=to_city)


@app.route('/cabs', methods=['GET', 'POST'])
def cabs():
    pickup = request.form.get('pickup', '') if request.method == 'POST' else ''
    drop = request.form.get('drop', '') if request.method == 'POST' else ''
    results = []
    if pickup and drop:
        for cab in catalog_data()['cabs']:
            values = vars(cab).copy()
            values.update(pickup=pickup, drop=drop)
            results.append(catalog_item(**values))
    return render_template('cabs.html', cabs=results, pickup=pickup, drop=drop)


@app.route('/villas')
def villas():
    return render_catalog('villas.html', 'villas')


@app.route('/packages')
def packages():
    return render_catalog('packages.html', 'packages')


@app.route('/tours')
def tours():
    return render_catalog('tours.html', 'tours')


def catalog_booking(label, price):
    if not current_user.is_authenticated:
        flash('Please login to book this service.', 'danger')
        return redirect(url_for('login'))
    service = Service.query.first()
    if service:
        db.session.add(Booking(user_id=current_user.id, service_id=service.id, service_name=label, service_type='travel', travel_date=datetime.now().date(), guests=1, status='Requested'))
        db.session.commit()
    flash(f'{label} booking request created.', 'success')
    return redirect(url_for('my_bookings'))


@app.route('/book-hotel/<int:hotel_id>', methods=['POST'])
def book_hotel(hotel_id): return catalog_booking(request.form.get('hotel_name', 'Hotel'), request.form.get('price', 0))

@app.route('/book-train', methods=['POST'])
def book_train(): return catalog_booking(request.form.get('train_name', 'Train'), request.form.get('price', 0))

@app.route('/book-bus/<int:bus_id>', methods=['POST'])
def book_bus(bus_id): return catalog_booking(request.form.get('operator', 'Bus'), request.form.get('price', 0))

@app.route('/book-cab/<int:cab_id>', methods=['POST'])
def book_cab(cab_id): return catalog_booking(request.form.get('cab_type', 'Cab'), request.form.get('price', 0))

@app.route('/book-villa/<int:villa_id>', methods=['POST'])
def book_villa(villa_id): return catalog_booking(request.form.get('villa_name', 'Villa'), request.form.get('price', 0))

@app.route('/book-package/<int:package_id>', methods=['POST'])
def book_package(package_id): return catalog_booking(request.form.get('package_name', 'Package'), request.form.get('price', 0))

@app.route('/book-tour/<int:tour_id>', methods=['POST'])
def book_tour(tour_id): return catalog_booking(request.form.get('tour_name', 'Tour'), request.form.get('price', 0))


@app.route('/blog')
def blog():
    return render_template('blog.html', blogs=[])


@app.route('/blog/<int:id>')
def blog_detail(id):
    blog_post = SimpleNamespace(id=id, title='TravelSetu travel guide', excerpt='Helpful ideas for your next journey.', content='Plan your journey with TravelSetu destinations, services, and recommendations.', image_url='https://picsum.photos/seed/travel-blog/1200/500', author=SimpleNamespace(full_name='TravelSetu Team'), created_at=datetime.now(), views=0, tags='travel,planning')
    return render_template('blog_detail.html', blog=blog_post)


@app.route('/admin')
@login_required
def admin_dashboard():
    bookings = Booking.query.order_by(Booking.created_at.desc()).limit(10).all()
    booking_views = [SimpleNamespace(id=b.id, user=User.query.get(b.user_id), booking_type=b.service_type, total_price=0, status=b.status.lower(), created_at=b.created_at) for b in bookings]
    stats = SimpleNamespace(total_users=User.query.count(), total_destinations=Destination.query.count(), total_bookings=Booking.query.count(), total_revenue=0, all_destinations=Destination.query.all(), recent_bookings=booking_views)
    return render_template('admin_dashboard.html', stats=stats)


@app.route('/admin/destination/add', methods=['POST'])
@login_required
def add_destination():
    destination = Destination(name=request.form.get('name', 'New Destination'), description=request.form.get('description', ''), category=request.form.get('category', 'other'), location=request.form.get('location', ''), price_per_day=float(request.form.get('price', 0) or 0), best_season=request.form.get('season', 'All Year'), rating=4.0)
    db.session.add(destination)
    db.session.commit()
    flash('Destination added.', 'success')
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/destination/<int:id>/delete')
@login_required
def delete_destination(id):
    destination = Destination.query.get_or_404(id)
    db.session.delete(destination)
    db.session.commit()
    flash('Destination deleted.', 'success')
    return redirect(url_for('admin_dashboard'))


@app.context_processor
def inject_globals():
    favorites_count = 0
    bookings_count = 0
    if current_user.is_authenticated:
        favorites_count = Favorite.query.filter_by(user_id=current_user.id).count()
        bookings_count = Booking.query.filter_by(user_id=current_user.id).count()
    return {
        'current_year': datetime.now().year,
        'favorites_count': favorites_count,
        'bookings_count': bookings_count,
        'get_destination_image': get_destination_image
    }


def seed_services():
    if Service.query.count() > 0:
        return
    services = [
        Service(service_type='hotel', name='Lakeview Stay', location='Udaipur, Rajasthan', details='Comfortable room near major attractions', price=2500),
        Service(service_type='hotel', name='Mountain Nest', location='Manali, Himachal Pradesh', details='Budget-friendly stay with mountain views', price=2200),
        Service(service_type='transport', name='City Cab', location='Udaipur', details='Local cab for sightseeing', price=1200),
        Service(service_type='transport', name='Intercity Bus', location='Delhi to Jaipur', details='AC bus service', price=800),
        Service(service_type='transport', name='Tourist Car', location='Leh Ladakh', details='Driver-assisted local travel', price=3000),
    ]
    db.session.add_all(services)
    db.session.commit()


if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        ensure_user_columns()
        ensure_destination_columns()
        ensure_booking_columns()
        seed_services()
    print('✅ TravelSetu database ready!')
    print('🌐 Open: http://127.0.0.1:6001')
    app.run(debug=True, port=6001, use_reloader=False)