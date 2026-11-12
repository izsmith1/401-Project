import sys
import random
from zoneinfo import ZoneInfo
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
login_manager.login_message_category = "info"


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

# python app.py --create-db : creates any missing tables, never deletes anything
def create_db():
    with app.app_context():
        db.create_all()
    print("Tables created. Now run: python seed_stocks.py")

# python app.py --reset-db : DELETES every table and row, then recreates them
def reset_db():
    answer = input("This deletes ALL data in the database. Type 'reset' to continue: ")
    if answer != "reset":
        print("Cancelled.")
        return
    with app.app_context():
        db.drop_all()
        db.create_all()
    print("Database dropped and recreated. Now run: python seed_stocks.py")

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

def commit_changes(success_message, error_message):
    try:
        db.session.commit()
        flash(success_message, "success")
    except SQLAlchemyError:
        db.session.rollback()
        app.logger.exception(error_message)
        flash(error_message, "error")
        return False

# Default market window used when no per-date override row exists.
DEFAULT_OPEN = time(9, 0)
DEFAULT_CLOSE = time(17, 0)
# Mon-Fri
DEFAULT_TRADING_WEEKDAYS = {0, 1, 2, 3, 4}

MARKET_TZ = ZoneInfo("America/New_York")

def market_now():
    return datetime.now(MARKET_TZ)

def market_hours_for(day, override=None):
    # Returns (open_time, close_time) for a date, or None if the market is closed that day.
    # A per-date row (holiday or special hours) overrides the weekly default.
    if override is not None:
        if override.is_closed or override.open_time is None or override.close_time is None:
            return None
        return override.open_time, override.close_time

    # No override: fall back to the default weekday window.
    if day.weekday() not in DEFAULT_TRADING_WEEKDAYS:
        return None
    return DEFAULT_OPEN, DEFAULT_CLOSE

market_switch = None

# Today's (open_time, close_time), or None if the market is closed all day
def todays_hours():
    today = market_now().date()
    override = MarketSchedule.query.filter_by(schedule_date=today).first()
    return market_hours_for(today, override)

def is_market_open():
    if market_switch is not None:
        return market_switch

    hours = todays_hours()
    return hours is not None and hours[0] <= market_now().time() < hours[1]
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

        # Checks length of password
        if len(password) < 8 or len(password.encode("utf-8")) > 72:
            flash("Password must be 8 to 72 characters", "error")
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
    # Visiting the login page signs out whoever is logged in, so the logged-in navbar
    # never shows next to a login form
    if current_user.is_authenticated:
        logout_user()
  
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        user = User.query.filter_by(username=username).first()
        # Over 72 bytes can't be a real password (register blocks it) and would crash bcrypt
        if (
            user
            and len(password.encode("utf-8")) <= 72
            and bcrypt.check_password_hash(user.password_hash, password)
        ):
            login_user(user)
            flash("Logged in successfully!", "success")
            return redirect(url_for("portfolio"))
        else:
            flash("Invalid username or password", "error")

    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("index"))


# Percent change from old to new, rounded to two decimals
def percent_change(new, old):
    return ((new - old) / old * 100).quantize(Decimal("0.01"))


# Midnight today on the market's clock, converted to the plain UTC the database stores
def market_day_start_utc():
    midnight = market_now().replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight.astimezone(timezone.utc).replace(tzinfo=None)


# One row per stock with today's change and the change since launch.
# Two queries no matter how many stocks there are.
def market_overview():
    stocks = Stock.query.order_by(Stock.ticker).all()

    # When each stock's newest price from before today was recorded
    latest = (
        db.session.query(
            PriceHistory.stock_id.label("stock_id"),
            db.func.max(PriceHistory.timestamp).label("timestamp"),
        )
        .filter(PriceHistory.timestamp < market_day_start_utc())
        .group_by(PriceHistory.stock_id)
        .subquery()
    )
    # The price at that moment is the previous close
    previous_close = dict(
        db.session.query(PriceHistory.stock_id, PriceHistory.price)
        .join(
            latest,
            db.and_(
                PriceHistory.stock_id == latest.c.stock_id,
                PriceHistory.timestamp == latest.c.timestamp,
            ),
        )
        .all()
    )

    rows = []
    for stock in stocks:
        # A stock created today has no previous close, so compare with its launch price
        close = previous_close.get(stock.id, stock.initial_price)
        rows.append({
            "stock": stock,
            "day_pct": percent_change(stock.current_price, close),
            "launch_pct": percent_change(stock.current_price, stock.initial_price),
            "market_cap": stock.current_price * stock.shares_outstanding,
        })
    return rows


# HOME - Public market page, the same for visitors and logged-in users
@app.route("/")
def index():
    rows = market_overview()
    gainers = sorted(
        (row for row in rows if row["day_pct"] > 0),
        key=lambda row: row["day_pct"], reverse=True,
    )
    losers = sorted(
        (row for row in rows if row["day_pct"] < 0),
        key=lambda row: row["day_pct"],
    )

    # Every stock is already loaded for the movers, so the search just filters that list
    search = request.args.get("q", "").strip()
    listed = rows
    if search:
        needle = search.lower()
        listed = [
            row for row in rows
            if needle in row["stock"].ticker.lower()
            or needle in row["stock"].company_name.lower()
        ]

    return render_template(
        "home.html",
        market_open=is_market_open(),
        market_forced=market_switch is not None,
        today_hours=todays_hours(),
        market_tz=market_now().tzname(),
        rows=listed,
        search=search,
        stock_count=len(rows),
        up_count=len(gainers),
        down_count=len(losers),
        gainers=gainers[:3],
        losers=losers[:3],
    )

# What the shares the user owns now were worth at each price change since they first
# bought them. Same shape as a stock's history, so drawChart() can draw it.
def holdings_value_history(holdings):
    shares = {h.stock_id: h.quantity for h in holdings}
    if not shares:
        return []

    # When the user first bought any of the stocks they hold
    start = (
        db.session.query(db.func.min(Transaction.transaction_date))
        .filter(Transaction.user_id == current_user.id, Transaction.stock_id.in_(shares))
        .scalar()
    )
    # Each stock's price at that moment
    prices = {}
    for stock_id in shares:
        prices[stock_id] = (
            PriceHistory.query
            .filter(PriceHistory.stock_id == stock_id, PriceHistory.timestamp <= start)
            .order_by(PriceHistory.timestamp.desc())
            .first()
            .price
        )

    def point(when):
        value = sum(shares[s] * prices[s] for s in shares)
        return {"time": when.isoformat() + "Z", "price": str(value)}

    # One point at the start, one per price change after it, and one for right now
    points = [point(start)]
    changes = (
        PriceHistory.query
        .filter(PriceHistory.stock_id.in_(shares), PriceHistory.timestamp > start)
        .order_by(PriceHistory.timestamp)
    )
    for change in changes:
        prices[change.stock_id] = change.price
        points.append(point(change.timestamp))
    points.append(point(utc_now()))
    return points

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
    stock_value = sum(
        (
            holding.quantity * holding.stock.current_price
            for holding in holdings
        ),
        Decimal("0.00"),
    )
    return render_template("portfolio.html", holdings=holdings, stock_value=stock_value,
                           value_history=holdings_value_history(holdings), market_open=is_market_open())

# HISTORY - The user's past activity (transactions for now)
@app.route("/history")
@login_required
def history():
    transactions = (
        Transaction.query.filter_by(user_id=current_user.id)
        .order_by(Transaction.transaction_date.desc())
        .all()
    )
    return render_template("history.html", transactions=transactions)


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
    change_pct = percent_change(stock.current_price, stock.initial_price)

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
    stock = db.get_or_404(Stock, stock_id, description="Stock not found")
    return jsonify(build_stats(stock))


# A stock can move at most once every 2 seconds, no matter how many people are watching
MIN_TICK_SECONDS = 2


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
    stock = db.get_or_404(Stock, stock_id, with_for_update=True)
    last = (
        db.session.query(PriceHistory)
        .filter(PriceHistory.stock_id == stock.id)
        .order_by(PriceHistory.timestamp.desc())
        .first()
    )

    # Only move the price if it hasn't moved in the last few seconds
    if is_market_open() and (
        last is None or utc_now() - last.timestamp >= timedelta(seconds=MIN_TICK_SECONDS)
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
    stock = db.session.db.get_or_404(Stock, stock_id, description="Stock not found")
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

    stock = db.session.get_or_404(Stock, stock_id, with_for_update=True, description="Stock not found")

    # Lock the user's row so two trades at once can't both spend the same cash
    user = db.session.get_or_404(User, current_user.id, with_for_update=True, populate_existing=True, 
                                 description="User not found")
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
    commit_changes(f"Bought {quantity} shares of {stock.ticker}", "Trade failed, please try again")

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
    stock = db.get_or_404(Stock, stock_id, description="Stock Not Found")
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

    stock = db.session.get_or_404(Stock, stock_id, with_for_update=True, description="Stock not found")

    # Lock the user's row and holding so the same shares can't be sold twice
    user = db.session.get_or_404(User, current_user.id, with_for_update=True, populate_existing=True,
                                 description="User not found")
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
    commit_changes(f"Sold {quantity} shares of {stock.ticker}", "Trade failed, please try again")

    return redirect(url_for("portfolio"))


# Validate a money amount from a form: parses as Decimal, > 0, at most two decimals.
# Returns (amount, None) on success or (None, error_message) on failure.
def validate_amount(raw):
    try:
        amount = Decimal(raw)
    except (InvalidOperation, ValueError, TypeError):
        return None, "Enter a valid amount"
    if amount <= 0:
        return None, "Amount must be greater than 0"
    if amount != amount.quantize(Decimal("0.01")):
        return None, "Amount can have at most two decimal places"
    return amount, None


# CASH - Show the deposit / withdraw page
@app.route("/cash")
@login_required
def cash():
    return render_template("cash.html")


# DEPOSIT - Add cash to the user's balance
@app.route("/deposit", methods=["POST"])
@login_required
def deposit():
    amount, error = validate_amount(request.form.get("amount", ""))
    if error:
        flash(error, "error")
        return redirect(url_for("cash"))

    # Lock the user's row so two cash changes at once can't clobber each other
    user = db.session.get_or_404(User, current_user.id, with_for_update=True, populate_existing=True, 
                          description="User not found")

    user.cash_balance += amount
    db.session.add(
        Transaction(
            user_id=user.id,
            transaction_type="DEPOSIT",
            quantity=0,
            amount=amount,
        )
    )
    log_event(user.id, "DEPOSIT", f"Deposited {amount}")
    
    commit_changes(f"Deposited ${amount}", "Could not complete the deposit, please try again")
    return redirect(url_for("cash"))


# WITHDRAW - Take cash out of the user's balance
@app.route("/withdraw", methods=["POST"])
@login_required
def withdraw():
    amount, error = validate_amount(request.form.get("amount", ""))
    if error:
        flash(error, "error")
        return redirect(url_for("cash"))

    # Lock the user's row before checking the balance so two withdrawals can't both pass
    user = db.session.get_or_404(User, current_user.id, with_for_update=True, populate_existing=True
                                 , description="User not found")

    if amount > user.cash_balance:
        flash("Insufficient funds", "error")
        return redirect(url_for("cash"))

    user.cash_balance -= amount
    db.session.add(
        Transaction(
            user_id=user.id,
            transaction_type="WITHDRAW",
            quantity=0,
            amount=amount,
        )
    )
    log_event(user.id, "WITHDRAW", f"Withdrew {amount}")

    commit_changes(f"Withdrew ${amount}", "Could not complete the withdrawal, please try again")

    return redirect(url_for("cash"))

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
        # Past days already happened, only today and later can be changed
        if schedule_date < market_now().date():
            flash("You can only change today or a future date", "error")
            return redirect(url_for("admin_schedule"))

        # Redirect back to the week that was just edited
        week_url = url_for("admin_schedule", week=schedule_date.isoformat())


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
                return redirect(week_url)

            if close_time <= open_time:
                flash("Close time must be later than open time", "error")
                return redirect(week_url)

        # Upsert the row for that date (update existing or create new)
        row = MarketSchedule.query.filter_by(schedule_date=schedule_date).first()
        if row is None:
            row = MarketSchedule(schedule_date=schedule_date)
            db.session.add(row)
        row.open_time = open_time
        row.close_time = close_time
        row.is_closed = is_closed
        row.updated_by = current_user.admin.id

        # Record who changed the schedule, saved by the same commit below
        if is_closed:
            log_event(current_user.id, "MARKET_CLOSED", f"Market scheduled for close on {schedule_date}")
        else:
            log_event(current_user.id, "MARKET_HOURS_SET", 
                      F"Market hours on {schedule_date} set to {open_time:%H:%M}-{close_time:%H:%M}",
            )
        commit_changes("Schedule saved", "Could not save the schedule, please try again")

        return redirect(week_url)

    # Weekly view: ?week=YYYY-MM-DD shows the Mon-Sun week holding that date, default is this week
    today = market_now().date()
    try:
        anchor = datetime.strptime(request.args.get("week", ""), "%Y-%m-%d").date()
    except ValueError:
        anchor = today
    week_start = anchor - timedelta(days=anchor.weekday())
    week_end = week_start + timedelta(days=6)

    overrides = {
        row.schedule_date: row
        for row in MarketSchedule.query.filter(
            MarketSchedule.schedule_date.between(week_start, week_end)
        )
    }
    week_days = []
    for offset in range(7):
        day = week_start + timedelta(days=offset)
        override = overrides.get(day)
        week_days.append({
            "date": day,
            "hours": market_hours_for(day, override),
            "override": override,
            "is_past": day < today,
            "is_today": day == today,
        })

    return render_template(
        "admin_schedule.html",
        schedules=MarketSchedule.query.order_by(MarketSchedule.schedule_date).all(),
        week_days=week_days,
        week_start=week_start,
        week_end=week_end,
        prev_week=(week_start - timedelta(days=7)).isoformat(),
        next_week=(week_start + timedelta(days=7)).isoformat(),
        today=today,
        default_open=DEFAULT_OPEN.strftime("%H:%M"),
        default_close=DEFAULT_CLOSE.strftime("%H:%M"),
        market_open=is_market_open(),
    )

# ADMIN - Testing only: flip the market open or closed right now
@app.route("/admin/market/toggle", methods=["POST"])
@login_required
def admin_toggle_market():
    if not current_user.is_admin:
        abort(403)

    global market_switch
    market_switch = not is_market_open()
    flash("Market is now " + ("open" if market_switch else "closed"), "success")
    return redirect(url_for("admin_schedule"))

# ADMIN - Remove a per-date schedule override (falls back to the default window)
@app.route("/admin/schedule/<int:id>/delete", methods=["POST"])
@login_required
def admin_delete_schedule(id):
    if not current_user.is_admin:
        abort(403)

    row = db.get_or_404(MarketSchedule, id)
    week_url = url_for("admin_schedule", week=row.schedule_date.isoformat())
    db.session.delete(row)
    log_event(
        current_user.id,
        "SCHEDULE_REMOVED",
        f"Schedule override for {row.schedule_date} removed (back to default hours)",
    )
    commit_changes("Schedule entry removed", "Could not remove the entry, please try again")

    return redirect(week_url)


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
            created_by=current_user.admin.id
        )
        db.session.add(stock)
        # Assigns stock.id so we can attach the first price history row
        db.session.flush()

        db.session.add(PriceHistory(stock_id=stock.id, price=price))
        log_event(current_user.id, "STOCK_CREATED", f"Created stock {ticker} at {price}")

        commit_changes(f"Stock {ticker} created", "Could not create the stock, please try again")

        return redirect(url_for("admin_stocks"))

    return render_template(
        "admin_stocks.html",
        stocks=Stock.query.order_by(Stock.ticker).all(),
    )

# ADMIN - List every account so ana dmin can open one users event log.
@app.route("/admin/users")
@login_required
def admin_users():
    if not current_user.is_admin:
        abort(403)

    users = User.query.order_by(User.created_at.desc()).all()
    return render_template("admin_users.html", users=users)

# ADMIN - Review a single user's recorded events (audit trail)
@app.route("/admin/users/<int:user_id>/events")
@login_required
def admin_user_events(user_id):
    if not current_user.is_admin:
        abort(403)

    user = db.session.get_or_404(User, user_id, description="User not found")

    events = (
        EventLog.query.filter_by(user_id=user_id)
        .order_by(EventLog.event_date.desc())
        .all()
    )
    return render_template("admin_user_events.html", viewed_user=user, events=events)


if __name__ == "__main__":
    if "--create-db" in sys.argv:
        create_db()
    elif "--reset-db" in sys.argv:
        reset_db()
    else:
        app.run(host="0.0.0.0", port=8000)
