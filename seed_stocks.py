from decimal import Decimal
from app import app, db, Stock, PriceHistory

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

        # The listed price is the price the stock launches at, so the current
        # price and the remembered initial price start out the same.
        starting_price = Decimal(price)
        stock = Stock(
            ticker=ticker,
            company_name=company_name,
            price=starting_price,            # current price starts at the initial price
            initial_price=starting_price,    # remembers the price it launched at
            shares_outstanding=SHARES_EACH,  # total shares that exist
            available_shares=SHARES_EACH,    # all shares are available to buy at first
        )
        db.session.add(stock)
        # Flush so the database assigns stock.id now, which the PriceHistory row below needs.
        db.session.flush()

        # Record the starting price as the first data point so charts and trend
        # arrows have something to show right away.
        db.session.add(PriceHistory(stock_id=stock.id, price=starting_price))
        added += 1

    db.session.commit()
    print(f'Added {added} stocks ({len(FAKE_STOCKS) - added} already existed)')
