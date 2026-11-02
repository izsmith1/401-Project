import sys, random
from flask import Flask, render_template, redirect, url_for, flash, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from flask_bcrypt import Bcrypt
from datetime import datetime, timezone, timedelta
from decimal import Decimal  #use Decimal for money, floats cause rounding errors

def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)

app = Flask(__name__)
bcrypt = Bcrypt(app)

app.config.from_object('config')  #DB login and secret key live in config.py (gitignored)
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'


class User(UserMixin, db.Model):
    __tablename__ = 'users'
    #.id for Flask-Login
    id = db.Column('user_id', db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    first_name = db.Column(db.String(255))
    last_name = db.Column(db.String(255))
    email = db.Column(db.String(255), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    cash_balance = db.Column(db.Numeric(10, 2), default=Decimal('10000.00'), nullable=False)  #starting money
    created_at = db.Column(db.DateTime, default=utc_now, nullable=False)
    holdings = db.relationship('Holding', backref='user', lazy=True)
    transactions = db.relationship('Transaction', backref='user', lazy=True)
    # Only set if this user is an admin
    admin = db.relationship('Administrator', backref='user', uselist=False)
    
    # Replaces the old role column, a user is an admin if they have a row in administrators
    @property
    def is_admin(self):
        return self.admin is not None


class Administrator(db.Model):
    __tablename__='administrators'
    id = db.Column('admin_id', db.Integer, primary_key=True)
    # One admin row per user
    user_id = db.Column(db.Integer, db.ForeignKey('users.user_id'), unique=True, nullable=False)
    # When they were made an admin
    granted_at = db.Column(db.DateTime, default=utc_now, nullable=False)


class Stock(db.Model):
    __tablename__ = 'stocks'
    id = db.Column('stock_id', db.Integer, primary_key=True)
    company_name = db.Column(db.String(255), nullable=False)
    ticker = db.Column(db.String(10), unique=True, nullable=False)  # Example: AAPL
    shares_available = db.Column(db.Integer, nullable=False)  # Buyable supply, starts == shares_outstanding
    current_price = db.Column(db.Numeric(10, 2), nullable=False)  # Current price per share
    created_by = db.Column(db.Integer, db.ForeignKey('administrators.admin_id'), nullable=True) # Admin who added it, null for seeded stocks
    initial_price = db.Column(db.Numeric(10, 2), nullable=False)  # Starting price set at creation
    shares_outstanding = db.Column(db.Integer, nullable=False)  # Total shares defined at creation


class Holding(db.Model):
    __tablename__ = 'holdings'
    id = db.Column('holding_id', db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.user_id'), nullable=False)
    stock_id = db.Column(db.Integer, db.ForeignKey('stocks.stock_id'), nullable=False)
    quantity = db.Column(db.Integer, default=0, nullable=False)
    stock = db.relationship('Stock')
    __table_args__ = (db.UniqueConstraint('user_id', 'stock_id'),)  # One row per user per stock

class Transaction(db.Model):
    __tablename__ = 'transactions'
    id = db.Column('transaction_id', db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.user_id'), nullable=False)
    stock_id = db.Column(db.Integer, db.ForeignKey('stocks.stock_id'), nullable=True)  # Null for Deposit / Withdraw
    transaction_type = db.Column(db.Enum('BUY', 'SELL', 'DEPOSIT', 'WITHDRAW', name='transaction_type_enum'), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)  # Shares for trades; 0 for cash events
    price = db.Column(db.Numeric(10, 2), nullable=True)  # Price per share for trades; null for cash events
    amount = db.Column(db.Numeric(10, 2), nullable=False)  # Total cash moved, filled in for every row
    transaction_date = db.Column(db.DateTime, default=utc_now, nullable=False)
    stock = db.relationship('Stock')


class PriceHistory(db.Model):
    __tablename__ = 'price_history'
    id = db.Column('price_history_id', db.Integer, primary_key=True)
    stock_id = db.Column(db.Integer, db.ForeignKey('stocks.stock_id'), nullable=False)
    price = db.Column(db.Numeric(10, 2), nullable=False)
    timestamp = db.Column(db.DateTime, default=utc_now, nullable=False)
    stock = db.relationship('Stock')


class EventLog(db.Model):
    __tablename__ = 'event_log'
    id = db.Column('event_id', db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.user_id'), nullable=True)  # Nullable for system events
    event_type = db.Column(db.String(100), nullable=False)   #ACCOUNT_CREATED | BUY | SELL | DEPOSIT | WITHDRAW | PRICE_UPDATE_ERROR
    description = db.Column(db.String(255), nullable=False)      # Summary for Auditing from admins
    event_date = db.Column(db.DateTime, default=utc_now, nullable=False)
    user = db.relationship('User')

class MarketSchedule(db.Model):
    __tablename__ = 'market_schedule'
    id = db.Column('schedule_id', db.Integer, primary_key=True)
    schedule_date = db.Column(db.Date, unique=True, nullable=False) # One row per date, overrides the default weekday 9-5
    open_time = db.Column(db.Time, nullable=True) # Null when market is closed
    close_time = db.Column(db.Time, nullable=True)
    is_closed = db.Column(db.Boolean, default=False, nullable=False) # True for holidays
    updated_by = db.Column(db.Integer, db.ForeignKey('administrators.admin_id'), nullable=True) # Admin who set it


with app.app_context():
    db.create_all()


def init_db():
    with app.app_context():
        db.drop_all()
        db.create_all()
    print("Database schema dropped and recreated. Now run: python seed_stocks.py")


#User loader - required by Flask-Login
@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        hashed_password = bcrypt.generate_password_hash(request.form.get('password')).decode('utf-8')
        user = User(
            username=request.form.get('username'),
            email=request.form.get('email'),
            first_name=request.form.get('first_name'),
            last_name=request.form.get('last_name'),
            password_hash=hashed_password,
        )
        db.session.add(user)
        db.session.commit()
        flash('User registered successfully!', 'success')
        return redirect(url_for('login'))

    return render_template('add_user.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user = User.query.filter_by(username=request.form.get('username')).first()
        if user and bcrypt.check_password_hash( 
            user.password_hash, request.form.get("password")
        ):
            login_user(user)
            flash('Logged in successfully!', 'success')
            return redirect(url_for('index'))
        else:
            flash('Invalid username or password', 'error')

    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))

@app.route('/')
def index():
    return render_template("home.html")


# READ - Show the user's portfolio (cash, holdings, recent transactions)
@app.route('/portfolio')
@login_required
def portfolio():
    holdings = Holding.query.filter_by(user_id=current_user.id).filter(Holding.quantity > 0).all()
    transactions = Transaction.query.filter_by(user_id=current_user.id).order_by(Transaction.transaction_date.desc()).all()
    return render_template('portfolio.html', holdings=holdings, transactions=transactions)

# READ - List all stocks with buy/sell forms
@app.route('/stocks')
@login_required
def stocks():
    return render_template('stocks.html', stocks=Stock.query.all())

# How many recent prices to send for the graph
HISTORY_POINTS = 100 

# Build the stats for one stock's dropdown - shared by the stats and tick route
def build_stats(stock):
    history = PriceHistory.query.filter_by(stock_id=stock.id)
    week_ago = utc_now() - timedelta(days=7)

    # Let MySQL fin the lowest / highest instead of loading every row into Python
    all_low, all_high = history.with_entities(
        db.func.min(PriceHistory.price), db.func.max(PriceHistory.price)).one()
    week_low, week_high = history.filter(PriceHistory.timestamp >= week_ago).with_entities(
        db.func.min(PriceHistory.price), db.func.max(PriceHistory.price)).one()

    change = stock.current_price - stock.initial_price
    change_pct = (change / stock.initial_price * 100).quantize(Decimal('0.01'))

    # Newest points first so LIMIT keeps the most recent, then flip to oldest-first for the graph
    points = history.order_by(PriceHistory.timestamp.desc()).limit(HISTORY_POINTS).all()
    points.reverse()

    return {
        'ticker': stock.ticker,
        'price': str(stock.current_price),
        'initial_price': str(stock.initial_price),
        'change': str(change),
        'change_pct': str(change_pct),
        'all_time_high': str(all_high or stock.current_price),
        'all_time_low': str(all_low or stock.current_price),
        'week_high': str(week_high or stock.current_price),
        'week_low': str(week_low or stock.current_price),
        'market_cap': str(stock.current_price * stock.shares_outstanding),
        'shares_outstanding': stock.shares_outstanding,
        'available_shares': stock.shares_available,
        'history': [{'time': p.timestamp.isoformat() + 'Z', 'price': str(p.price)} for p in points],
    }

# STATS - Price stats and history for on stock's dropdown
@app.route('/stocks/<int:stock_id>/stats')
@login_required
def stock_stats(stock_id):
    stock = Stock.query.get_or_404(stock_id)
    return jsonify(build_stats(stock))

# A stock can move at most once every 2 seconds, no matter how many people are watching
MIN_TICK_SECONDS = 2 
# Each tick moves the price up to 1% up or down
MAX_TICK_CHANGE = 0.01


# TICK - Randomly move a stock's price (called every few seconds while its dropdown is open)
@app.route('/stocks/<int:stock_id>/tick', methods=['POST'])
@login_required
def tick(stock_id):
    # Lock the stock row so two viewers ticking at the same moment can't both move it
    stock = Stock.query.with_for_update().get_or_404(stock_id)
    last = PriceHistory.query.filter_by(stock_id=stock.id).order_by(PriceHistory.timestamp.desc()).first()

    # Only move the price if it hasn't moved in the last few seconds
    if last is None or utc_now() - last.timestamp >= timedelta(seconds=MIN_TICK_SECONDS):
        change = Decimal(str(random.uniform(-MAX_TICK_CHANGE, MAX_TICK_CHANGE)))
        stock.current_price = max((stock.current_price * (1 + change)).quantize(Decimal('0.01')), Decimal('0.01'))
        db.session.add(PriceHistory(stock_id=stock.id, price=stock.current_price))

    # Saves the new price and releases the lock (even if nothing changed)
    db.session.commit()
    return jsonify(build_stats(stock))

#PREVIEW - Show what a buy would cost without changing anything
@app.route('/buy/<int:stock_id>/preview')
@login_required
def buy_preview(stock_id):
    stock = Stock.query.get_or_404(stock_id)
    quantity = request.args.get('quantity', default=0, type=int)
    cost = stock.current_price * quantity
    cash_after = current_user.cash_balance - cost

    return jsonify(
        ticker=stock.ticker,
        price=str(stock.current_price),
        cash_now=str(current_user.cash_balance),
        cost=str(cost),
        cash_after=str(cash_after),
        can_afford=quantity > 0 and cash_after >= 0
    )

# BUY - Purchase shares of a stock
@app.route('/buy/<int:stock_id>', methods=['POST'])
@login_required
def buy(stock_id):
    stock = Stock.query.get_or_404(stock_id)
    quantity = request.form.get('quantity', type=int)

# Validate input
    if not quantity or quantity <= 0:
        flash('Enter a valid number of shares', 'error')
        return redirect(url_for('stocks'))

# Lock the user's row so two trades at once can't both spend the same cash
    user = User.query.with_for_update().get(current_user.id)
    total_cost = stock.current_price * quantity

    if user.cash_balance < total_cost:
        flash('Not enough cash for this purchase', 'error')
        return redirect(url_for('stocks'))

# 1. take the money out
    user.cash_balance -= total_cost

# 2. add the shares (create a holding row if this is the first time buying)
    holding = Holding.query.filter_by(user_id=user.id, stock_id=stock.id).first()
    if holding:
        holding.quantity += quantity
    else:
        db.session.add(Holding(user_id=user.id, stock_id=stock.id, quantity=quantity))

# 3. record the transaction
    db.session.add(Transaction(user_id=user.id, stock_id=stock.id, transaction_type='BUY',
                               quantity=quantity, price=stock.current_price, amount=total_cost))

# Save all 3 changes together - if anything fails, none of them are saved
    try:
        db.session.commit()
        flash(f'Bought {quantity} shares of {stock.ticker}', 'success')
    except Exception:
        db.session.rollback()
        flash('Trade failed, please try again', 'error')

    return redirect(url_for('portfolio'))

@app.route('/sell/<int:stock_id>/preview')
@login_required
def sell_preview(stock_id):
    stock = Stock.query.get_or_404(stock_id)
    quantity = request.args.get('quantity', default=0, type=int)
    holding = Holding.query.filter_by(user_id=current_user.id, stock_id=stock.id).first()
    owned = holding.quantity if holding else 0
    proceeds = stock.current_price * quantity

    return jsonify(
        ticker=stock.ticker,
        price=str(stock.current_price),
        cash_now=str(current_user.cash_balance),
        shares_owned=owned,
        proceeds=str(proceeds),
        cash_after=str(current_user.cash_balance + proceeds),
        can_sell=0 < quantity <= owned,
    )

# SELL - Sell shares of a stock
@app.route('/sell/<int:stock_id>', methods=['POST'])
@login_required
def sell(stock_id):
    stock = Stock.query.get_or_404(stock_id)
    quantity = request.form.get('quantity', type=int)

    #validate input
    if not quantity or quantity <= 0:
        flash('Enter a valid number of shares', 'error')
        return redirect(url_for('portfolio'))

# Lock the user's row and holding so the same shares can't be sold twice
    user = User.query.with_for_update().get(current_user.id)
    holding = Holding.query.filter_by(user_id=user.id, stock_id=stock.id).with_for_update().first()

    if not holding or holding.quantity < quantity:
        flash('You do not own enough shares to sell', 'error')
        return redirect(url_for('portfolio'))

# 1. Remove the shares
    holding.quantity -= quantity

# 2. Add the money
    proceeds = stock.current_price * quantity
    user.cash_balance += proceeds

# 3. Record the transaction
    db.session.add(Transaction(user_id=user.id, stock_id=stock.id, transaction_type='SELL',
                               quantity=quantity, price=stock.current_price, amount=proceeds))

# Save all 3 changes together
    try:
        db.session.commit()
        flash(f'Sold {quantity} shares of {stock.ticker}', 'success')
    except Exception:
        db.session.rollback()
        flash('Trade failed, please try again', 'error')

    return redirect(url_for('portfolio'))

if __name__ == "__main__":
    if "--init-db" in sys.argv:
        init_db()
    else:
        app.run(host="0.0.0.0", port=8000)
