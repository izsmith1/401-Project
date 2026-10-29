#=====================================================================
# CONFIG TEMPLATE - copy this to config.py and fill in your own values
#=====================================================================
#
# config.py holds passwords, so it is gitignored and never committed.
# Each person keeps their own copy. The app won't start without it.
#
# 1. Copy this file (run from the stocks/ folder):
#       Windows (PowerShell):  Copy-Item config.example.py config.py
#       Mac/Linux:             cp config.example.py config.py
#
# 2. In config.py, set SQLALCHEMY_DATABASE_URI to ONE of the options below
#    and delete or comment out the other.
#
# 3. Set SECRET_KEY to a random string. Generate one with:
#       python -c "import secrets; print(secrets.token_hex(32))"
#
# 4. First run on a new database: start the app once so it creates the
#    tables, then load the starting stocks with:
#       python seed_stocks.py
#
# Never put real passwords in this file - it IS committed to git.
#=====================================================================

#Option A - local MySQL on your own computer
#Create the database first in MySQL Workbench: CREATE DATABASE stockdb;
SQLALCHEMY_DATABASE_URI = "mysql+pymysql://root:YOUR_MYSQL_PASSWORD@localhost:3306/stockdb"

#Option B - shared AWS RDS database (ask the team for the endpoint and login)
#SQLALCHEMY_DATABASE_URI = "mysql+pymysql://USERNAME:PASSWORD@YOUR-RDS-ENDPOINT.rds.amazonaws.com:3306/stockdb"

#Signs login sessions - anyone who knows it can forge a login, so keep it secret
SECRET_KEY = 'paste-your-generated-key-here'
