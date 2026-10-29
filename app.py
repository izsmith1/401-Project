import sys
from flask import Flask, render_template, redirect, url_for, flash, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from flask_bcrypt import Bcrypt
from datetime import datetime
from decimal import Decimal  #use Decimal for money, floats cause rounding errors

app = Flask(__name__)
bcrypt = Bcrypt(app)

app.config.from_object('config')  #DB login and secret key live in config.py (gitignored)
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(250), unique=True, nullable=False)
    password = db.Column(db.String(250), nullable=False)
    role = db.Column(db.String(50), default="user", nullable=False)
    cash_balance = db.Column(db.Numeric(12, 2), default=Decimal('10000.00'), nullable=False)  #starting money
    holdings = db.relationship('Holding', backref='user', lazy=True)
    transactions = db.relationship('Transaction', backref='user', lazy=True)

class Stock(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    ticker = db.Column(db.String(10), unique=True, nullable=False)  #ex: AAPL
    company_name = db.Column(db.String(250), nullable=False)
    price = db.Column(db.Numeric(12, 2), nullable=False)  #current price per share
    initial_price = db.Column(db.Numeric(12, 2), nullable=False)  #starting price set at creation
    shares_outstanding = db.Column(db.BigInteger, nullable=False)  #total shares defined at creation
    available_shares = db.Column(db.BigInteger, nullable=False)  #buyable supply, starts == shares_outstanding


class Holding(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    stock_id = db.Column(db.Integer, db.ForeignKey('stock.id'), nullable=False)
    quantity = db.Column(db.Integer, default=0, nullable=False)
    stock = db.relationship('Stock')
    __table_args__ = (db.UniqueConstraint('user_id', 'stock_id'),)  #one row per user per stock

class Transaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    stock_id = db.Column(db.Integer, db.ForeignKey('stock.id'), nullable=True)  #null for cash events
    type = db.Column(db.String(8), nullable=False)  #BUY | SELL | DEPOSIT | WITHDRAW
    quantity = db.Column(db.Integer, nullable=False)  #shares for trades; 0 for cash events
    amount = db.Column(db.Numeric(12, 2), nullable=True)  #cash amount for DEPOSIT/WITHDRAW
    price = db.Column(db.Numeric(12, 2), nullable=True)  #price per share for trades; null for cash events
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    stock = db.relationship('Stock')


class PriceHistory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    stock_id = db.Column(db.Integer, db.ForeignKey('stock.id'), nullable=False)
    price = db.Column(db.Numeric(12, 2), nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    stock = db.relationship('Stock')


class EventLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)  #nullable for system events
    event_type = db.Column(db.String(32), nullable=False)   #ACCOUNT_CREATED | BUY | SELL | DEPOSIT | WITHDRAW | PRICE_UPDATE_ERROR
    details = db.Column(db.String(500), nullable=False)      #human-readable summary
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    user = db.relationship('User')


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
            password=hashed_password,
            role='user' #default role
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
            user.password, request.form.get("password")
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
    transactions = Transaction.query.filter_by(user_id=current_user.id).order_by(Transaction.timestamp.desc()).all()
    return render_template('portfolio.html', holdings=holdings, transactions=transactions)

# READ - List all stocks with buy/sell forms
@app.route('/stocks')
@login_required
def stocks():
    return render_template('stocks.html', stocks=Stock.query.all())


#PREVIEW - Show what a buy would cost without changing anything
@app.route('/buy/<int:stock_id>/preview')
@login_required
def buy_preview(stock_id):
    stock = Stock.query.get_or_404(stock_id)
    quantity = request.args.get('quantity', default=0, type=int)
    cost = stock.price * quantity
    cash_after = current_user.cash_balance - cost

    return jsonify(
        ticker=stock.ticker,
        price=str(stock.price),
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
    total_cost = stock.price * quantity

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
    db.session.add(Transaction(user_id=user.id, stock_id=stock.id, type='BUY',
                               quantity=quantity, price=stock.price))

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
    proceeds = stock.price * quantity

    return jsonify(
        ticker=stock.ticker,
        price=str(stock.price),
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

# 1. remove the shares
    holding.quantity -= quantity

# 2. add the money
    user.cash_balance += stock.price * quantity

# 3. record the transaction
    db.session.add(Transaction(user_id=user.id, stock_id=stock.id, type='SELL',
                               quantity=quantity, price=stock.price))

# Save all 3 changes together
    try:
        db.session.commit()
        flash(f'Sold {quantity} shares of {stock.ticker}', 'success')
    except Exception:
        db.session.rollback()
        flash('Trade failed, please try again', 'error')

    return redirect(url_for('portfolio'))

# UPDATE - Edit an individual user
@app.route('/update/<int:user_id>', methods=['POST'])
def update_user(user_id):
    user = User.query.get_or_404(user_id)

    # TODO: Implement update user functionality
    user.username = request.form['username']
    new_password = request.form.get('password')
    if new_password: #leave password unchanged if field is blank
        user.password = bcrypt.generate_password_hash(new_password).decode('utf-8')
    db.session.commit()
    flash('User updated successfully!', 'success')

    return redirect(url_for('view_user', user_id=user_id))

# DELETE - Delete a user
@app.route('/delete/<int:user_id>', methods=['POST'])
def delete_user(user_id):

    # TODO: Implement delete user functionality
    user = User.query.get_or_404(user_id)
    db.session.delete(user)
    db.session.commit()
    flash('User deleted successfully!', 'success')
    return redirect(url_for('index'))


if __name__ == "__main__":
    if "--init-db" in sys.argv:
        init_db()
    else:
        app.run(host="0.0.0.0", port=8000)
