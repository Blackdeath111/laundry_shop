from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
import pymysql
import os

# --- Flask app ---
app = Flask(__name__, template_folder=os.path.join(os.path.dirname(__file__), 'templates'))
app.secret_key = "mysecretkey"

# --- MySQL driver ---
pymysql.install_as_MySQLdb()

# --- Database config ---
app.config['SQLALCHEMY_DATABASE_URI'] = "mysql+pymysql://root:@localhost/laundry_db"
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)


# --- DATABASE MODELS ---
class User(db.Model):
    __tablename__ = 'user'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), default="customer")
    laundry_orders = db.relationship('LaundryOrder', backref='user', lazy=True, cascade="all, delete")


class LaundryOrder(db.Model):
    __tablename__ = 'laundry_order'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    laundry_type = db.Column(db.String(100))
    weight_kg = db.Column(db.Float)
    price = db.Column(db.Float)
    status = db.Column(db.String(50), default="Pending")
    pickup_requested = db.Column(db.Boolean, default=False)
    floor_number = db.Column(db.String(10))
    unit_number = db.Column(db.String(10))
    date_created = db.Column(db.DateTime, default=datetime.now)
    date_updated = db.Column(db.DateTime, onupdate=datetime.now)


# --- Helper ---
def get_price_per_kg(laundry_type):
    mapping = {
        "Wash-Dry-Fold": 23,
        "Wash-Dry-Press": 60,
        "Press Only": 40,
        "Special Items": 70
    }
    return mapping.get(laundry_type, 23)


def order_to_dict(order):
    """Return a JSON-serializable dict for an order — safe when user is missing."""
    return {
        'id': order.id,
        'customer': order.user.username if getattr(order, 'user', None) else 'Deleted User',
        'type': order.laundry_type or 'N/A',
        'weight': float(order.weight_kg) if order.weight_kg is not None else 0,
        'price': float(order.price) if order.price is not None else 0.0,
        'pickup': bool(order.pickup_requested),
        'location': (f"Floor {order.floor_number or '-'}, Unit {order.unit_number or '-'}") if order.pickup_requested else '—',
        'status': order.status,
        'date_created': order.date_created.strftime('%Y-%m-%d %H:%M') if order.date_created else 'N/A'
    }


# --- ROUTES ---
@app.route('/')
def home():
    return redirect(url_for('login'))


# --- LOGIN ---
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password, password):
            session['user_id'] = user.id
            session['role'] = user.role
            if user.role == 'admin':
                return redirect(url_for('admin_dashboard'))
            else:
                return redirect(url_for('user_dashboard'))
        else:
            flash("Invalid username or password!", "danger")
    return render_template('login.html')


# --- REGISTER ---
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password']
        if not username or not password:
            flash("Username and password required.", "danger")
            return redirect(url_for('register'))
        if User.query.filter_by(username=username).first():
            flash("Username already exists!", "danger")
            return redirect(url_for('register'))
        hashed_pw = generate_password_hash(password)
        new_user = User(username=username, password=hashed_pw, role='customer')
        db.session.add(new_user)
        db.session.commit()
        flash("Account created successfully! Please log in.", "success")
        return redirect(url_for('login'))
    return render_template('register.html')


# --- USER DASHBOARD ---
@app.route('/user', methods=['GET', 'POST'])
def user_dashboard():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    user = User.query.get(session['user_id'])
    if not user:
        session.clear()
        return redirect(url_for('login'))

    if request.method == 'POST':
        laundry_type = request.form['laundry_type']
        weight = float(request.form['weight'])
        pickup_requested = 'pickup_requested' in request.form
        floor = request.form.get('floor_number') if pickup_requested else None
        unit = request.form.get('unit_number') if pickup_requested else None
        price = get_price_per_kg(laundry_type) * weight
        if pickup_requested:
            price += 20

        new_order = LaundryOrder(
            user_id=user.id,
            laundry_type=laundry_type,
            weight_kg=weight,
            price=price,
            pickup_requested=pickup_requested,
            floor_number=floor,
            unit_number=unit,
            status="Pending"
        )
        db.session.add(new_order)
        db.session.commit()
        flash("Order submitted successfully!", "success")
        return redirect(url_for('user_dashboard'))

    orders = LaundryOrder.query.filter_by(user_id=user.id).order_by(LaundryOrder.date_created.desc()).all()
    return render_template('user_dashboard.html', user=user, orders=orders)


# --- ADMIN DASHBOARD ---
@app.route('/admin')
def admin_dashboard():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    user = User.query.get(session['user_id'])
    if not user or user.role != 'admin':
        return redirect(url_for('user_dashboard'))

    try:
        pending_orders = LaundryOrder.query.filter_by(status="Pending").order_by(LaundryOrder.date_created.desc()).all()
        # outerjoin to avoid crashes if some orders point to deleted users
        all_orders = db.session.query(LaundryOrder).outerjoin(User).order_by(LaundryOrder.date_created.desc()).all()
        users = User.query.all()

        total_income = db.session.query(db.func.sum(LaundryOrder.price)).scalar() or 0

        monthly_income = db.session.query(
            db.func.date_format(LaundryOrder.date_created, '%Y-%m').label('month'),
            db.func.sum(LaundryOrder.price).label('total')
        ).group_by(db.func.date_format(LaundryOrder.date_created, '%Y-%m')).all()

        # total_orders for summary card
        total_orders = db.session.query(db.func.count(LaundryOrder.id)).scalar() or 0

        return render_template(
            'admin_dashboard.html',
            user=user,
            users=users,
            pending_orders=pending_orders,
            all_orders=all_orders,
            total_income=total_income,
            monthly_income=monthly_income,
            total_orders=total_orders,
            orders=all_orders  # keep variable name users template expects
        )
    except Exception as e:
        print("🔥 ADMIN DASHBOARD ERROR:", e)
        flash("Admin dashboard error — check console.", "danger")
        return redirect(url_for('login'))


# --- UPDATE ORDER STATUS (AJAX) ---
@app.route('/api/update_status/<int:order_id>', methods=['POST'])
def api_update_status(order_id):
    order = LaundryOrder.query.get(order_id)
    if not order:
        return jsonify({'success': False, 'error': 'Order not found'}), 404

    try:
        data = request.get_json(force=True)
        if not data or 'status' not in data:
            return jsonify({'success': False, 'error': 'No status provided'}), 400

        order.status = data['status']
        order.date_updated = datetime.now()
        db.session.commit()

        return jsonify({'success': True, 'order': order_to_dict(order)})
    except Exception as e:
        print("🔥 Update Error:", e)
        db.session.rollback()
        return jsonify({'success': False, 'error': 'Server error'}), 500


# --- DELETE ORDER (AJAX) ---
@app.route('/api/delete_order/<int:order_id>', methods=['DELETE'])
def api_delete_order(order_id):
    order = LaundryOrder.query.get(order_id)
    if not order:
        return jsonify({'success': False, 'error': 'Order not found'}), 404

    try:
        db.session.delete(order)
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        print("🔥 Delete Error:", e)
        db.session.rollback()
        return jsonify({'success': False, 'error': 'Server error'}), 500


# --- DELETE ORDER (form POST) ---
@app.route('/delete_order/<int:order_id>', methods=['POST'])
def delete_order(order_id):
    order = LaundryOrder.query.get(order_id)
    if not order:
        flash("Order not found!", "danger")
        return redirect(url_for('admin_dashboard'))

    db.session.delete(order)
    db.session.commit()
    flash("Order deleted successfully!", "info")
    return redirect(url_for('admin_dashboard'))


# --- INCOME BY MONTH (AJAX) ---
@app.route('/api/income_by_month', methods=['GET'])
def api_income_by_month():
    try:
        rows = db.session.query(
            db.func.date_format(LaundryOrder.date_created, '%Y-%m').label('month'),
            db.func.sum(LaundryOrder.price).label('total')
        ).group_by(db.func.date_format(LaundryOrder.date_created, '%Y-%m')).order_by(db.func.date_format(LaundryOrder.date_created, '%Y-%m').desc()).all()

        months = [{'month': r.month, 'total': float(r.total or 0)} for r in rows]
        return jsonify({'success': True, 'months': months})
    except Exception as e:
        print("🔥 Income Error:", e)
        return jsonify({'success': False, 'error': 'Server error'}), 500


# --- LOGOUT ---
@app.route('/logout')
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for('login'))


# --- Initialize DB ---
with app.app_context():
    db.create_all()
    if not User.query.filter_by(username='admin').first():
        admin = User(username='admin', password=generate_password_hash('admin123'), role='admin')
        db.session.add(admin)
        db.session.commit()
        print("✅ Default admin created: admin / admin123")


# --- Run the app ---
if __name__ == '__main__':
    app.run(debug=True)

