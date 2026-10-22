from decimal import Decimal
from app import app, db, Stock

# Fake stocks to add to the database (ticker, company name, price)
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
        db.session.add(Stock(ticker=ticker, company_name=company_name, price=Decimal(price)))
        added += 1

    db.session.commit()
    print(f'Added {added} stocks ({len(FAKE_STOCKS) - added} already existed)')
