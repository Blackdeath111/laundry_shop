from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask import redirect, url_for, session, flash
from datetime import datetime, date
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


# New: persistent Income table — income entries are independent of users/orders
class Income(db.Model):
    __tablename__ = 'income'
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False, index=True)  # date-only
    total = db.Column(db.Float, default=0.0)

    def __repr__(self):
        return f"<Income {self.date} ₱{self.total}>"


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


def add_income_entry(entry_date: date, amount: float):
    """
    Add amount to Income table for the given date.
    If an Income row for the date exists, increment it; otherwise create it.
    """
    if amount is None:
        return
    try:
        inc = Income.query.filter_by(date=entry_date).first()
        if inc:
            inc.total = (inc.total or 0.0) + float(amount)
        else:
            inc = Income(date=entry_date, total=float(amount))
            db.session.add(inc)
        db.session.commit()
    except Exception as e:
        print("🔥 add_income_entry error:", e)
        db.session.rollback()


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

        # Persist income at creation time so deleting the user/order later won't remove the income record.
        try:
            order_date = (new_order.date_created.date() if new_order.date_created else datetime.now().date())
        except Exception:
            order_date = datetime.now().date()
        add_income_entry(order_date, price)

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

        # total_income now comes from Income table so it stays even after deleting users/orders
        total_income_row = db.session.query(db.func.sum(Income.total)).scalar()
        total_income = float(total_income_row or 0.0)

        # monthly_income used by server-side template if needed (kept in case template uses it)
        from sqlalchemy import func
        monthly_income = db.session.query(
            func.date_format(Income.date, '%Y-%m').label('month'),
            func.sum(Income.total).label('total')
        ).group_by(func.date_format(Income.date, '%Y-%m')).all()

        # total_orders for summary card (counts current orders)
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
        # note: we DO NOT remove income entries here (income is persistent)
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

    try:
        db.session.delete(order)
        db.session.commit()
        # income stays — we intentionally do not touch Income rows
        flash("Order deleted successfully!", "info")
    except Exception as e:
        print("🔥 Delete (form) Error:", e)
        db.session.rollback()
        flash("Could not delete order (server error).", "danger")
    return redirect(url_for('admin_dashboard'))


# --- INCOME BY MONTH & DAY (from Income table) ---
@app.route('/api/income_by_month')
def api_income_by_month():
    try:
        from collections import defaultdict
        import calendar

        # read from Income table
        incomes = Income.query.order_by(Income.date.desc()).all()
        monthly = defaultdict(float)
        daily = defaultdict(float)
        overall_total = 0.0

        for inc in incomes:
            total = float(inc.total or 0.0)
            overall_total += total
            month_label = f"{calendar.month_name[inc.date.month]} {inc.date.year}"
            monthly[month_label] += total
            day_label = inc.date.strftime("%B %d, %Y")
            daily[day_label] += total

        # sort months descending
        def month_sort_key(m):
            parts = m.split()
            mon = list(calendar.month_name).index(parts[0]) if parts else 0
            yr = int(parts[1]) if len(parts) > 1 else 0
            return (yr, mon)

        month_items = sorted(monthly.items(), key=lambda kv: month_sort_key(kv[0]), reverse=True)
        day_items = sorted(daily.items(), key=lambda kv: datetime.strptime(kv[0], "%B %d, %Y"), reverse=True)

        months_list = [{"month": k, "total": v} for k, v in month_items]
        days_list = [{"day": k, "total": v} for k, v in day_items]

        return jsonify({
            "success": True,
            "months": months_list,
            "days": days_list,
            "overall_total": overall_total
        })
    except Exception as e:
        print("🔥 Income Error:", e)
        return jsonify({"success": False, "error": "Server error"}), 500


# --- INCOME BY WEEK (Mon → Sun) ---
@app.route('/api/income_by_week')
def api_income_by_week():
    try:
        from collections import defaultdict
        from datetime import timedelta, datetime

        incomes = Income.query.order_by(Income.date).all()
        daily_map = {inc.date: float(inc.total or 0.0) for inc in incomes}

        if not daily_map:
            return jsonify({"success": True, "weeks": []})

        min_date = min(daily_map.keys())
        max_date = max(daily_map.keys())

        # adjust to full weeks (Monday → Sunday)
        min_monday = min_date - timedelta(days=min_date.weekday())
        max_sunday = max_date + timedelta(days=(6 - max_date.weekday()))

        weeks = []
        current = min_monday
        while current <= max_sunday:
            week = []
            for i in range(7):
                day = current + timedelta(days=i)
                week.append({
                    "date": day.strftime("%A %b %d, %Y"),
                    "total": daily_map.get(day, 0.0)
                })
            weeks.append(week)
            current += timedelta(days=7)

        return jsonify({"success": True, "weeks": weeks})
    except Exception as e:
        print("🔥 Weekly Income Error:", e)
        return jsonify({"success": False, "error": "Server error"}), 500


# --- DELETE MONTH INCOME ---
@app.route('/api/delete_income_month', methods=['POST'])
def delete_income_month():
    try:
        payload = request.get_json(force=True)
        month_label = payload.get('month')
        if not month_label:
            return jsonify(success=False, error="No month provided"), 400

        import calendar
        parts = month_label.split()
        if len(parts) != 2:
            return jsonify(success=False, error="Invalid month format"), 400
        month_num = list(calendar.month_name).index(parts[0])
        year = int(parts[1])

        # delete rows
        rows = Income.query.filter(db.extract('year', Income.date) == year,
                                   db.extract('month', Income.date) == month_num).all()
        deleted = 0
        for r in rows:
            db.session.delete(r)
            deleted += 1
        db.session.commit()

        # recalc total income
        total_income = db.session.query(db.func.sum(Income.amount)).scalar() or 0

        return jsonify(success=True, deleted=deleted, total_income=total_income)
    except Exception as e:
        print("🔥 Delete month income error:", e)
        db.session.rollback()
        return jsonify(success=False, error="Server error"), 500

# --- DELETE DAILY INCOME ---
@app.route('/api/delete_income_day', methods=['POST'])
def delete_income_day():
    try:
        payload = request.get_json(force=True)
        day_label = payload.get('day')
        if not day_label:
            return jsonify(success=False, error="No day provided"), 400

        from datetime import datetime as _dt
        target_date = _dt.strptime(day_label, "%A %b %d, %Y").date()  # match weekly API format

        # delete rows
        rows = Income.query.filter_by(date=target_date).all()
        deleted = 0
        for r in rows:
            db.session.delete(r)
            deleted += 1
        db.session.commit()

        # recalc total income
        total_income = db.session.query(db.func.sum(Income.amount)).scalar() or 0

        return jsonify(success=True, deleted=deleted, total_income=total_income)
    except Exception as e:
        print("🔥 Delete day income error:", e)
        db.session.rollback()
        return jsonify(success=False, error="Server error"), 500


# --- DELETE WEEKLY INCOME (triggered from "daily" button) ---
@app.route('/api/delete_income_week', methods=['POST'])
def delete_income_week():
    try:
        payload = request.get_json(force=True)
        day_label = payload.get('day')
        if not day_label:
            return jsonify(success=False, error="No day provided"), 400

        from datetime import datetime, timedelta

        # Parse the day
        target_date = datetime.strptime(day_label, "%A %b %d, %Y").date()

        # Calculate the Monday and Sunday of that week
        start_of_week = target_date - timedelta(days=target_date.weekday())  # Monday
        end_of_week = start_of_week + timedelta(days=6)  # Sunday

        # Delete all Income entries within that week
        rows = Income.query.filter(Income.date >= start_of_week, Income.date <= end_of_week).all()
        deleted = 0
        for r in rows:
            db.session.delete(r)
            deleted += 1

        db.session.commit()
        return jsonify(success=True, deleted=deleted)

    except Exception as e:
        print("🔥 Delete week income error:", e)
        db.session.rollback()
        return jsonify(success=False, error="Server error"), 500
    
# --- Initialize DB ---
with app.app_context():
    db.create_all()
    if not User.query.filter_by(username='admin').first():
        admin = User(username='admin', password=generate_password_hash('admin123'), role='admin')
        db.session.add(admin)
        db.session.commit()
        print("✅ Default admin created: admin / admin123")


from flask import redirect, url_for, session, flash

@app.route('/logout', methods=['GET', 'POST'])
def logout():
    session.clear()  # clear all session data
    flash("You have been logged out.", "success")
    return redirect(url_for('login'))  # redirect to login page


# --- Run the app ---
if __name__ == '__main__':
    app.run(debug=True)


