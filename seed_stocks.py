import random
from datetime import timedelta
from decimal import Decimal
from app import app, db, Stock, PriceHistory, utc_now


# How many days of fake history each stock gets
HISTORY_DAYS = 30

def fake_history(start_price):
    """Random walk of daily prices, oldest first, starting at the launch price."""
    prices = [start_price]
    for _ in range(HISTORY_DAYS):
        change = Decimal(str(random.uniform(-0.03, 0.03))) # Up to 3% up or down per day
        new_price = (prices[-1] * (1 + change)).quantize(Decimal('0.01')) # Round to cents
        prices.append(max(new_price, Decimal('0.01'))) # A stock can't drop below .01 cent
    return prices

# Fake stocks to add to the database (ticker, company name, price)
SHARES_EACH = 1_000_000

# Fake stocks to add to the database (ticker, company name, starting price)
FAKE_STOCKS = [
    ('ACME', 'Acme Corporation', '125.40'),
    ('GLBX', 'Globex Industries', '78.15'),
    ('INIT', 'Initech Software', '42.90'),
    ('UMBR', 'Umbrella Pharmaceuticals', '210.75'),
    ('STRK', 'Stark Energy', '315.60'),
    ('WAYN', 'Wayne Enterprises', '189.25'),
    ('CYBD', 'Cyberdyne Systems', '96.80'),
    ('SOYL', 'Soylent Foods', '18.35'),
    ('PIED', 'Pied Piper Inc.', '54.10'),
    ('HOOL', 'Hooli Technologies', '402.00'),
]

with app.app_context():
    added = 0
    for ticker, company_name, price in FAKE_STOCKS:
        # Skip stocks that are already in the database so the script can be run more than once
        if Stock.query.filter_by(ticker=ticker).first():
            continue

        # The listed price is what the stock launched at 30 days ago. The fake
        # history walks forward from there, and the last day is today's price.
        starting_price = Decimal(price)
        prices = fake_history(starting_price)
        stock = Stock(
            ticker=ticker,
            company_name=company_name,
            price=prices[-1],                # Current price = last day of history
            initial_price=starting_price,    # Remembers the price it launched at
            shares_outstanding=SHARES_EACH,  # Total shares that exist
            available_shares=SHARES_EACH,    # All shares are available to buy at first
        )
        db.session.add(stock)
        # Flush so the database assigns stock.id now, which the PriceHistory row below needs.
        db.session.flush()

        # One history point per day, dated from 30 days ago up to today
        now = utc_now()
        for days_ago, day_price in zip(range(HISTORY_DAYS, -1, -1), prices):
            db.session.add(PriceHistory(stock_id=stock.id, price=day_price,
                                        timestamp=now - timedelta(days=days_ago)))
        added += 1

    db.session.commit()
    print(f'Added {added} stocks ({len(FAKE_STOCKS) - added} already existed)')
