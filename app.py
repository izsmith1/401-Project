import sys
import random
from datetime import datetime, timezone, timedelta, time, date
from decimal import Decimal, InvalidOperation
from flask import Flask, render_template, redirect, url_for, flash, request, jsonify, abort
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.exc import SQLAlchemyError
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from flask_bcrypt import Bcrypt

def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)

app = Flask(__name__)
bcrypt = Bcrypt(app)

# DB login and secret key live in config.py (gitignored)
app.config.from_object("config")  
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"

class User(UserMixin, db.Model):
    __tablename__ = "users"
    # .id for Flask-Login
    id = db.Column("user_id", db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    first_name = db.Column(db.String(255))
    last_name = db.Column(db.String(255))
    email = db.Column(db.String(255), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    # Starting money
    cash_balance = db.Column(db.Numeric(10, 2), default=Decimal("10000.00"), nullable=False)  
    created_at = db.Column(db.DateTime, default=utc_now, nullable=False)
    holdings = db.relationship("Holding", backref="user", lazy=True)
    transactions = db.relationship("Transaction", backref="user", lazy=True)
    # Only set if this user is an admin
    admin = db.relationship("Administrator", backref="user", uselist=False)

    # Replaces the old role column, a user is an admin if they have a row in administrators
    @property
    def is_admin(self):
        return self.admin is not None

class Administrator(db.Model):
    __tablename__ = "administrators"
    id = db.Column("admin_id", db.Integer, primary_key=True)
    # One admin row per user
    user_id = db.Column(db.Integer, db.ForeignKey("users.user_id"), unique=True, nullable=False)
    # When they were made an admin
    granted_at = db.Column(db.DateTime, default=utc_now, nullable=False)

class Stock(db.Model):
    __tablename__ = "stocks"
    id = db.Column("stock_id", db.Integer, primary_key=True)
    company_name = db.Column(db.String(255), nullable=False)
    # Example: AAPL
    ticker = db.Column(db.String(10), unique=True, nullable=False)
    # Buyable supply, starts == shares_outstanding
    shares_available = db.Column(db.Integer, nullable=False)
    # Current price per share
    current_price = db.Column(db.Numeric(10, 2), nullable=False)
    # Admin who added it, null for seeded stocks
    created_by = db.Column(db.Integer, db.ForeignKey("administrators.admin_id"), nullable=True)
    # Starting price set at creation
    initial_price = db.Column(db.Numeric(10, 2), nullable=False)
    # Total shares defined at creation
    shares_outstanding = db.Column(db.Integer, nullable=False)

class Holding(db.Model):
    __tablename__ = "holdings"
    id = db.Column("holding_id", db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.user_id"), nullable=False)
    stock_id = db.Column(db.Integer, db.ForeignKey("stocks.stock_id"), nullable=False)
    quantity = db.Column(db.Integer, default=0, nullable=False)
    stock = db.relationship("Stock")
    __table_args__ = (db.UniqueConstraint("user_id", "stock_id"),)  # One row per user per stock

class Transaction(db.Model):
    __tablename__ = "transactions"
    id = db.Column("transaction_id", db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.user_id"), nullable=False)
    # Null for Deposit / Withdraw events
    stock_id = db.Column(db.Integer, db.ForeignKey("stocks.stock_id"), nullable=True)
    # BUY / SELL for trades, DEPOSIT / WITHDRAW for cash
    transaction_type = db.Column(db.Enum("BUY", "SELL", "DEPOSIT", "WITHDRAW", name="transaction_type_enum"),nullable=False)
    # Shares for trades; 0 for cash events
    quantity = db.Column(db.Integer, nullable=False)
    # Price per share for trades; null for cash events
    price = db.Column(db.Numeric(10, 2), nullable=True)
    # Total cash moved, filled in for every row
    amount = db.Column(db.Numeric(10, 2), nullable=False)
    transaction_date = db.Column(db.DateTime, default=utc_now, nullable=False)
    stock = db.relationship("Stock")

class PriceHistory(db.Model):
    __tablename__ = "price_history"
    __table_args__ = (db.Index("ix_price_history_stock_time", "stock_id", "timestamp"),)
    id = db.Column("price_history_id", db.Integer, primary_key=True)
    stock_id = db.Column(db.Integer, db.ForeignKey("stocks.stock_id"), nullable=False)
    price = db.Column(db.Numeric(10, 2), nullable=False)
    timestamp = db.Column(db.DateTime, default=utc_now, nullable=False)
    stock = db.relationship("Stock")

class EventLog(db.Model):
    __tablename__ = "event_log"
    id = db.Column("event_id", db.Integer, primary_key=True)
    # Nullable for system events
    user_id = db.Column(db.Integer, db.ForeignKey("users.user_id"), nullable=True)
    # ACCOUNT_CREATED | BUY | SELL | DEPOSIT | WITHDRAW | PRICE_UPDATE_ERROR
    # | MARKET_CLOSED | MARKET_HOURS_SET | SCHEDULE_REMOVED
    event_type = db.Column(db.String(100), nullable=False)
    # Summary for Auditing from admins
    description = db.Column(db.String(255), nullable=False)
    event_date = db.Column(db.DateTime, default=utc_now, nullable=False)
    user = db.relationship("User")

class MarketSchedule(db.Model):
    __tablename__ = "market_schedule"
    id = db.Column("schedule_id", db.Integer, primary_key=True)
    # One row per date, overrides the default weekday 9-5
    schedule_date = db.Column(db.Date, unique=True, nullable=False)
    # Null when market is closed
    open_time = db.Column(db.Time, nullable=True)
    close_time = db.Column(db.Time, nullable=True)
    # True for holidays
    is_closed = db.Column(db.Boolean, default=False, nullable=False)
    # Admin who set it
    updated_by = db.Column(db.Integer, db.ForeignKey("administrators.admin_id"), nullable=True)

# Drops all tables and rows (Uncomment db.drop_all to drop all tables and rows)
# python app.py --init-db
def init_db():
    with app.app_context():
#        db.drop_all()
        db.create_all()
    print("Database schema dropped and recreated. Now run: python seed_stocks.py") 

def log_event(user_id, event_type, description):
    # Records one line in the event log (who did what, and when). We only add it to the
    # session here; it gets saved by the same db.session.commit() as the action that called
    # it, so if that action is rolled back the log line is dropped too.
    db.session.add(
        EventLog(user_id=user_id, event_type=event_type, description=description)
    )

def record_trade(user_id, stock, quantity, transaction_type, amount):
    db.session.add(
        Transaction(
            user_id=user_id,
            stock_id=stock.id,
            transaction_type=transaction_type,
            quantity=quantity,
            price=stock.current_price,
            amount=amount
        )
    )

def positive_quantity(values):
    quantity = values.get("quantity", default=0, type=int)
    return quantity if quantity > 0 else 0 

def commit_trade(success_message):
    try:
        db.session.commit()
        flash(success_message, "success")
        return True
    except SQLAlchemyError as error:
        db.session.rollback()
        app.logger.exception("Trade failed", exc_info=error)
        flash("Trade failed, please try again", "error")
        return False

# Default market window used when no per-date override row exists.
DEFAULT_OPEN = time(9, 0)
DEFAULT_CLOSE = time(17, 0)
# Mon-Fri
DEFAULT_TRADING_WEEKDAYS = {0, 1, 2, 3, 4}

def is_market_open():
    # Plain server-local time, no timezone machinery (student-project style).
    now = datetime.now()
    today = now.date()

    # A per-date row (holiday or special hours) overrides the weekly default.
    override = MarketSchedule.query.filter_by(schedule_date=today).first()
    if override is not None:
        if override.is_closed or override.open_time is None or override.close_time is None:
            return False
        return override.open_time <= now.time() < override.close_time

    # No override: fall back to the default weekday window.
    if now.weekday() not in DEFAULT_TRADING_WEEKDAYS:
        return False
    return DEFAULT_OPEN <= now.time() < DEFAULT_CLOSE

# User loader - required by Flask-Login
@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username=request.form.get("username", "").strip()
        email=request.form.get("email", "").strip()
        first_name=request.form.get("first_name", "").strip()
        last_name=request.form.get("last_name", "").strip()
        password = request.form.get("password", "")

        if not username or not email or not password:
            flash("Username, email, and password are required", "error")
            return render_template("add_user.html")       

        existing_user = User.query.filter(
            db.or_(User.username == username, User.email == email)
        ).first()

        if existing_user:
            flash("Username or email already exists", "error")
            return render_template("add_user.html")

        hashed_password = bcrypt.generate_password_hash(password).decode("utf-8")        
        
        user = User(
            username=username,
            email=email,
            first_name=first_name,
            last_name=last_name,
            password_hash=hashed_password,
        )
        db.session.add(user)
        # Assigns user.id so we can log who was created
        db.session.flush()
        
        log_event(user.id, "ACCOUNT_CREATED", f"Account created for {user.username}")
        db.session.commit()
        
        flash("User registered successfully!", "success")
        return redirect(url_for("login"))

    return render_template("add_user.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user = User.query.filter_by(username=request.form.get("username")).first()
        if user and bcrypt.check_password_hash(
            user.password_hash, request.form.get("password")
        ):
            login_user(user)
            flash("Logged in successfully!", "success")
            return redirect(url_for("index"))
        else:
            flash("Invalid username or password", "error")

    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


@app.route("/")
def index():
    return render_template("home.html")


# READ - Show the user's portfolio (cash, holdings, recent transactions)
@app.route("/portfolio")
@login_required
def portfolio():
    holdings = (
        Holding.query
        .filter_by(user_id=current_user.id)
        .filter(Holding.quantity > 0)
        .options(db.joinedload(Holding.stock))
        .all()
    )
    transactions = (
        Transaction.query.filter_by(user_id=current_user.id)
        .order_by(Transaction.transaction_date.desc())
        .all()
    )
    stock_value = sum(
        (
            holding.quantity * holding.stock.current_price
            for holding in holdings
            if holding.stock is not None
        ),
        Decimal("0.00"),
    )
    return render_template("portfolio.html", holdings=holdings, transactions=transactions, 
                           stock_value=stock_value, market_open=is_market_open())


# READ - List all stocks with buy forms
@app.route("/stocks")
@login_required
def stocks():
    return render_template("stocks.html", stocks=Stock.query.all(), market_open=is_market_open())


# How many recent prices to send for the graph
HISTORY_POINTS = 100


# Build the stats for one stock's dropdown - shared by the stats and tick route
def build_stats(stock):
    history = PriceHistory.query.filter_by(stock_id=stock.id)
    week_ago = utc_now() - timedelta(days=7)

    # Let MySQL find the lowest / highest instead of loading every row into Python
    all_low, all_high = history.with_entities(
        db.func.min(PriceHistory.price), db.func.max(PriceHistory.price)
    ).one()
    week_low, week_high = (
        history.filter(PriceHistory.timestamp >= week_ago)
        .with_entities(db.func.min(PriceHistory.price), db.func.max(PriceHistory.price))
        .one()
    )

    change = stock.current_price - stock.initial_price
    change_pct = (change / stock.initial_price * 100).quantize(Decimal("0.01"))

    # Newest points first so LIMIT keeps the most recent, then flip to oldest-first for the graph
    points = history.order_by(PriceHistory.timestamp.desc()).limit(HISTORY_POINTS).all()
    points.reverse()

    return {
        "ticker": stock.ticker,
        "price": str(stock.current_price),
        "initial_price": str(stock.initial_price),
        "change": str(change),
        "change_pct": str(change_pct),
        "all_time_high": str(all_high or stock.current_price),
        "all_time_low": str(all_low or stock.current_price),
        "week_high": str(week_high or stock.current_price),
        "week_low": str(week_low or stock.current_price),
        "market_cap": str(stock.current_price * stock.shares_outstanding),
        "shares_outstanding": stock.shares_outstanding,
        "available_shares": stock.shares_available,
        "history": [
            {"time": p.timestamp.isoformat() + "Z", "price": str(p.price)}
            for p in points
        ],
    }


# STATS - Price stats and history for one stock's dropdown
@app.route("/stocks/<int:stock_id>/stats")
@login_required
def stock_stats(stock_id):
    stock = db.session.get(Stock, stock_id)
    if stock is None:
        abort(404, description="Stock not found")
    return jsonify(build_stats(stock))


# A stock can move at most once every 2 seconds, no matter how many people are watching
MIN_TICK_SECONDS = 2
# Each tick moves the price up to 1% up or down
MAX_TICK_CHANGE_PERCENT = 1


# TICK - Randomly move a stock's price (called every few seconds while its dropdown is open
# or the Sell popup is open.)
@app.route("/stocks/<int:stock_id>/tick", methods=["POST"])
@login_required
def tick(stock_id):
    # Market closed: don't move the price or add a PriceHistory row, just return current stats
    if not is_market_open():
        stock = Stock.query.get_or_404(stock_id)
        return jsonify(build_stats(stock))

    # Lock the stock row so two viewers ticking at the same moment can't both move it
    stock = Stock.query.with_for_update().get_or_404(stock_id)
    last = (
        db.session.query(PriceHistory)
        .filter(PriceHistory.stock_id == stock.id)
        .order_by(PriceHistory.timestamp.desc())
        .first()
    )

    # Only move the price if it hasn't moved in the last few seconds
    if last is None or utc_now() - last.timestamp >= timedelta(
        seconds=MIN_TICK_SECONDS
    ):
        basis_points = random.randint(-100, 100)  # -1% to +1%
        change = Decimal(basis_points) / Decimal("10000")

        new_price = stock.current_price * (Decimal("1") + change)
        stock.current_price = max(
            new_price.quantize(Decimal("0.01")), Decimal("0.01")
        ) 
        db.session.add(PriceHistory(stock_id=stock.id, price=stock.current_price))

    # Saves the new price and releases the lock (even if nothing changed)
    db.session.commit()
    return jsonify(build_stats(stock))


# PREVIEW - Show what a buy would cost without changing anything
@app.route("/buy/<int:stock_id>/preview")
@login_required
def buy_preview(stock_id):
    stock = db.session.get(Stock, stock_id)
    if stock is None:
        abort(404, description="Stock not found")
    quantity = positive_quantity(request.args)
    cost = stock.current_price * quantity
    cash_after = current_user.cash_balance - cost

    return jsonify(
        ticker=stock.ticker,
        price=str(stock.current_price),
        cash_now=str(current_user.cash_balance),
        cost=str(cost),
        cash_after=str(cash_after),
        can_afford=quantity > 0 and cash_after >= 0,
    )


# BUY - Purchase shares of a stock
@app.route("/buy/<int:stock_id>", methods=["POST"])
@login_required
def buy(stock_id):
    # Market closed: reject the order and change nothing
    if not is_market_open():
        flash("The market is closed", "error")
        return redirect(url_for("stocks"))

    quantity = positive_quantity(request.form)

    if quantity == 0:
        flash("Enter a valid number of shares", "error")
        return redirect(url_for("stocks"))

    stock = db.session.get(Stock, stock_id, with_for_update=True)
    if stock is None:
        abort(404, description="Stock not found")

    # Lock the user's row so two trades at once can't both spend the same cash
    user = db.session.get(User, current_user.id, with_for_update=True)
    if user is None:
        abort(404, description="User not found")
    total_cost = stock.current_price * quantity

    if user.cash_balance < total_cost:
        flash("Not enough cash for this purchase", "error")
        return redirect(url_for("stocks"))

    # Make sure there are enough shares left to buy
    if stock.shares_available < quantity:
        flash("Not enough shares available", "error")
        return redirect(url_for("stocks"))

    # 1. take the money out and the shares out of inventory
    user.cash_balance -= total_cost
    stock.shares_available -= quantity

    # 2. add the shares (create a holding row if this is the first time buying)
    holding = db.session.query(Holding).filter(Holding.user_id == user.id, Holding.stock_id == stock.id).first()
    if holding:
        holding.quantity += quantity
    else:
        db.session.add(Holding(user_id=user.id, stock_id=stock.id, quantity=quantity))

    # 3. record the transaction
    record_trade(
            user_id=user.id,
            stock=stock,
            quantity=quantity,
            transaction_type="BUY",
            amount=total_cost,
        )

    # 4. log the event (saved by the same commit below)
    log_event(
        user.id, "BUY", f"Bought {quantity} {stock.ticker} at {stock.current_price}"
    )

    # Save everything together - if anything fails, none of them are saved
    commit_trade(f"Bought {quantity} shares of {stock.ticker}")

    return redirect(url_for("portfolio"))

# Average price paid per share for the shares the user still owns
def average_cost(user_id, stock_id):
    trades = (
        db.session.query(Transaction)
        .filter(Transaction.user_id == user_id, Transaction.stock_id == stock_id)
        .order_by(Transaction.id)
        .all()
    )
    shares = 0
    total_paid = Decimal("0.00")

    for t in trades:
        if t.transaction_type == "BUY":
            shares += t.quantity
            total_paid += t.amount
        elif t.transaction_type == "SELL" and shares:
            total_paid -= total_paid / shares * t.quantity
            shares -= t.quantity

    if shares == 0:
        return Decimal("0.00")
    return (total_paid / shares).quantize(Decimal("0.01"))


@app.route("/sell/<int:stock_id>/preview")
@login_required
def sell_preview(stock_id):
    stock = db.session.get(Stock, stock_id)
    if stock is None:
        abort(404, description="Stock not found")
    quantity = positive_quantity(request.args)
    holding = db.session.query(Holding).filter(
        Holding.user_id == current_user.id, Holding.stock_id == stock.id
    ).first()
    owned = holding.quantity if holding else 0
    proceeds = stock.current_price * quantity
    avg_cost = average_cost(current_user.id, stock.id)
    gain_per_share = stock.current_price - avg_cost

    return jsonify(
        ticker=stock.ticker,
        price=str(stock.current_price),
        cash_now=str(current_user.cash_balance),
        shares_owned=owned,
        proceeds=str(proceeds),
        cash_after=str(current_user.cash_balance + proceeds),
        can_sell=0 < quantity <= owned,
        gain_per_share=str(gain_per_share),
        total_gain=str(gain_per_share * quantity),
    )


# SELL - Sell shares of a stock
@app.route("/sell/<int:stock_id>", methods=["POST"])
@login_required
def sell(stock_id):
    # Market closed: reject the order and change nothing
    if not is_market_open():
        flash("The market is closed", "error")
        return redirect(url_for("portfolio"))

    quantity = positive_quantity(request.form)

    # Validate input
    if quantity == 0:
        flash("Enter a valid number of shares", "error")
        return redirect(url_for("portfolio"))

    stock = db.session.get(Stock, stock_id)
    if stock is None:
        abort(404, description="Stock not found")

    # Lock the user's row and holding so the same shares can't be sold twice
    user = db.session.get(User, current_user.id)
    if user is None:
        abort(404, description="User not found")
    holding = (
        db.session.query(Holding)
        .filter(Holding.user_id == user.id, Holding.stock_id == stock.id)
        .with_for_update()
        .first()
    )

    if not holding or holding.quantity < quantity:
        flash("You do not own enough shares to sell", "error")
        return redirect(url_for("portfolio"))

    # 1. Remove the shares
    holding.quantity -= quantity
    stock.shares_available += quantity

    # 2. Add the money
    proceeds = stock.current_price * quantity
    user.cash_balance += proceeds

    # 3. Record the transaction
    record_trade(
            user_id=user.id,
            stock=stock,
            quantity=quantity,
            transaction_type="SELL",
            amount=proceeds,
        )

    # 4. log the event (saved by the same commit below)
    log_event(
        user.id, "SELL", f"Sold {quantity} {stock.ticker} at {stock.current_price}"
    )

    # Save everything together
    commit_trade(f"Sold {quantity} shares of {stock.ticker}")

    return redirect(url_for("portfolio"))

# ADMIN - Manage per-date market schedule overrides (special hours and holidays)
@app.route("/admin/schedule", methods=["GET", "POST"])
@login_required
def admin_schedule():
    if not current_user.is_admin:
        abort(403)

    if request.method == "POST":
        # Parse the target date
        try:
            schedule_date = datetime.strptime(
                request.form.get("schedule_date", ""), "%Y-%m-%d"
            ).date()
        except ValueError:
            flash("Enter a valid date", "error")
            return redirect(url_for("admin_schedule"))

        is_closed = request.form.get("is_closed") == "on"
        open_time = None
        close_time = None

        # Open dates need both times, and close must be later than open
        if not is_closed:
            try:
                open_time = datetime.strptime(
                    request.form.get("open_time", ""), "%H:%M"
                ).time()
                close_time = datetime.strptime(
                    request.form.get("close_time", ""), "%H:%M"
                ).time()
            except ValueError:
                flash("Enter valid open and close times", "error")
                return redirect(url_for("admin_schedule"))

            if close_time <= open_time:
                flash("Close time must be later than open time", "error")
                return redirect(url_for("admin_schedule"))

        # Upsert the row for that date (update existing or create new)
        row = MarketSchedule.query.filter_by(schedule_date=schedule_date).first()
        if row is None:
            row = MarketSchedule(schedule_date=schedule_date)
            db.session.add(row)
        row.open_time = open_time
        row.close_time = close_time
        row.is_closed = is_closed
        row.updated_by = current_user.admin.id if current_user.admin else None

        # Record who changed the schedule, saved by the same commit below
        if is_closed:
            log_event(current_user.id, "MARKET_CLOSED", f"Market scheduled for close on {schedule_date}")
        else:
            log_event(current_user.id, "MARKET_HOURS_SET", 
                      F"Market hours on {schedule_date} set to {open_time:%H:%M}-{close_time:%H:%M}",
            )
        try:
            db.session.commit()
            flash("Schedule saved", "success")
        except Exception:
            db.session.rollback()
            flash("Could not save the schedule, please try again", "error")

        return redirect(url_for("admin_schedule"))

    return render_template(
        "admin_schedule.html",
        schedules=MarketSchedule.query.order_by(MarketSchedule.schedule_date).all(),
    )


# ADMIN - Remove a per-date schedule override (falls back to the default window)
@app.route("/admin/schedule/<int:id>/delete", methods=["POST"])
@login_required
def admin_delete_schedule(id):
    if not current_user.is_admin:
        abort(403)

    row = MarketSchedule.query.get_or_404(id)
    db.session.delete(row)
    log_event(
        current_user.id,
        "SCHEDULE_REMOVED",
        f"Schedule override for {row.schedule_date} removed (back to default hours)",
    )
    try:
        db.session.commit()
        flash("Schedule entry removed", "success")
    except Exception:
        db.session.rollback()
        flash("Could not remove the entry, please try again", "error")

    return redirect(url_for("admin_schedule"))


# ADMIN - Create new stocks for customers to trade
@app.route("/admin/stocks", methods=["GET", "POST"])
@login_required
def admin_stocks():
    if not current_user.is_admin:
        abort(403)

    if request.method == "POST":
        company_name = request.form.get("company_name", "").strip()
        ticker = request.form.get("ticker", "").strip()

        # Company name and ticker must be present
        if not company_name:
            flash("Company name is required", "error")
            return redirect(url_for("admin_stocks"))
        if not ticker:
            flash("Ticker is required", "error")
            return redirect(url_for("admin_stocks"))

        # Shares outstanding must be a whole number greater than 0
        try:
            shares_outstanding = int(request.form.get("shares_outstanding", ""))
        except (ValueError, TypeError):
            flash("Shares outstanding must be a whole number", "error")
            return redirect(url_for("admin_stocks"))
        if shares_outstanding <= 0:
            flash("Shares outstanding must be greater than 0", "error")
            return redirect(url_for("admin_stocks"))

        # Initial price must be a sensible money value greater than 0
        try:
            price = Decimal(request.form.get("initial_price", "")).quantize(
                Decimal("0.01")
            )
        except (InvalidOperation, ValueError):
            flash("Initial price must be a valid price", "error")
            return redirect(url_for("admin_stocks"))
        if price <= Decimal("0"):
            flash("Initial price must be greater than 0", "error")
            return redirect(url_for("admin_stocks"))

        # Reject a case-insensitive duplicate ticker
        existing = Stock.query.filter(
            db.func.upper(Stock.ticker) == ticker.upper()
        ).first()
        if existing is not None:
            flash(f"A stock with ticker {ticker.upper()} already exists", "error")
            return redirect(url_for("admin_stocks"))

        ticker = ticker.upper()
        stock = Stock(
            company_name=company_name,
            ticker=ticker,
            shares_outstanding=shares_outstanding,
            shares_available=shares_outstanding,
            initial_price=price,
            current_price=price,
            created_by=current_user.admin.id if current_user.admin else None,
        )
        db.session.add(stock)
        # Assigns stock.id so we can attach the first price history row
        db.session.flush()

        db.session.add(PriceHistory(stock_id=stock.id, price=price))
        log_event(current_user.id, "STOCK_CREATED", f"Created stock {ticker} at {price}")

        try:
            db.session.commit()
            flash(f"Stock {ticker} created", "success")
        except Exception:
            db.session.rollback()
            flash("Could not create the stock, please try again", "error")

        return redirect(url_for("admin_stocks"))

    return render_template(
        "admin_stocks.html",
        stocks=Stock.query.order_by(Stock.ticker).all(),
    )

if __name__ == "__main__":
    if "--init-db" in sys.argv:
        init_db()
    else:
        app.run(host="0.0.0.0", port=8000)
