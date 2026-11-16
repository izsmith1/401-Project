# Stock Trading System

A web app where trainees practice buying and selling simulated stocks with virtual cash.
Prices move during market hours, an administrator creates stocks and sets the market
schedule, and every account, cash, and stock event is logged. No real money is involved.

Built as a 3-tier web application:

- **Tier 1 – Presentation:** the user's browser (HTML, CSS, and JavaScript served by Flask)
- **Tier 2 – Application:** Flask + Python, serving on port **8000**
- **Tier 3 – Data:** a MySQL database (local MySQL for development, AWS RDS when deployed)

The browser never talks to the database directly. It only talks to the Flask app, and the
Flask app talks to MySQL.

## Tech stack

- Flask with server-rendered Jinja2 templates
- Flask-SQLAlchemy (ORM), Flask-Login (sessions), Flask-Bcrypt (password hashing)
- MySQL, connected with PyMySQL
- A little vanilla JavaScript with `fetch()` for live prices and the trade confirmation popups

## What you need first

- Python 3 (with `pip`)
- A MySQL database. Pick **one** of these:
  - **Local MySQL** on your own computer (for development), or
  - **AWS RDS MySQL** (the shared database used when deployed)

You choose which one in `config.py` (see the Database setup section below).

## Setup

Run these from the project folder (the one that contains `app.py`).

### 1. (Recommended) Create a virtual environment

```bash
python3 -m venv venv
source venv/bin/activate        # Mac/Linux
# venv\Scripts\activate         # Windows PowerShell
```

### 2. Install the dependencies

```bash
pip install -r requirements.txt
```

### 3. Create your config.py

`config.py` holds the database password and the secret key, so it is **gitignored and never
committed**. Each person keeps their own copy. Copy the template:

```bash
cp config.example.py config.py           # Mac/Linux
# Copy-Item config.example.py config.py   # Windows PowerShell
```

Then open `config.py` and set `SECRET_KEY` to a random string. Generate one with:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

Paste the output as the value of `SECRET_KEY`.

## Database setup — choose ONE

In `config.py` there are two `SQLALCHEMY_DATABASE_URI` options. **Use exactly one** and
delete or comment out the other.

### Option A — Local MySQL (development)

Use this when running on your own computer during development.

1. Create the database once in MySQL Workbench (or the MySQL shell):

   ```sql
   CREATE DATABASE stockdb;
   ```

2. In `config.py`, use the local URI and fill in your own MySQL password and database name:

   ```python
   SQLALCHEMY_DATABASE_URI = "mysql+pymysql://root:YOUR_MYSQL_PASSWORD@localhost:3306/stockdb"
   ```

   - `root` — your MySQL username
   - `YOUR_MYSQL_PASSWORD` — your local MySQL password
   - `localhost:3306` — MySQL running on your own machine
   - `stockdb` — the database name you created in step 1

3. Make sure the AWS RDS line (Option B) is commented out.

### Option B — AWS RDS MySQL (deployed / shared)

Use this when running against the shared AWS database. Ask the team for the endpoint and login.

1. In `config.py`, use the RDS URI and fill in the real values:

   ```python
   SQLALCHEMY_DATABASE_URI = "mysql+pymysql://USERNAME:PASSWORD@YOUR-RDS-ENDPOINT.rds.amazonaws.com:3306/stockdb"
   ```

   - `USERNAME` / `PASSWORD` — the RDS login the team gives you
   - `YOUR-RDS-ENDPOINT.rds.amazonaws.com` — the RDS endpoint from the AWS console
   - `3306` — the MySQL port
   - `stockdb` — the database name on RDS

2. Make sure the local line (Option A) is commented out.

> **Heads up on the shared RDS database:** the tables and starting stocks are likely already
> set up by the team. Only run the table-creation and seeding steps below against RDS if you
> know the database is empty — otherwise you could duplicate data (or, with `--reset-db`,
> wipe shared data). When in doubt on RDS, skip straight to *Run the app*.

## Create the tables and seed starting data

With `config.py` pointing at your chosen database:

```bash
python app.py --create-db      # creates any missing tables (safe, never deletes)
python seed_stocks.py          # loads the starting stocks with 30 days of price history
```

There is also a destructive reset command. It **deletes every table and row**, then recreates
them. Use it only on a database you are willing to wipe (typically a local dev database):

```bash
python app.py --reset-db       # DELETES ALL DATA, then recreates empty tables
```

## Run the app

```bash
python app.py
```

Then open your browser to:

```
http://localhost:8000
```

The app listens on `0.0.0.0:8000`.

## AWS deployment (3-tier on AWS)

### The RDS MySQL database (Tier 3)

In the AWS console, create an RDS database with engine **MySQL** (Free tier template is fine):

- **Master username:** `admin`
- **Master password:** a password you won't forget
- **Database port:** `3306`
- **Initial database name:** your database name (for example `stockdb`)
- **Public access:** **Yes** — required so your local Flask app can reach the RDS database
  during development
- **VPC security group:** create one, then under **Inbound rules** add a **MYSQL/Aurora** rule
  (this sets protocol TCP, port 3306)

Then copy the database **Endpoint** from the RDS console and put it in `config.py` Option B
(see *Option B — AWS RDS MySQL* above):

```
mysql+pymysql://admin:password@your-rds-endpoint.rds.amazonaws.com:3306/stockdb
```

> **Security note:** opening the inbound 3306 rule to **Anywhere-IPv4 (`0.0.0.0/0`)** is only
> acceptable during development because it simplifies connecting your computer to RDS. The
> stricter target setup is: RDS in a **private subnet** with inbound 3306 allowed **only from the
> EC2 application tier's security group**, and no public access. Tighten the rule to that before
> treating the deployment as production.

### The EC2 application server (Tier 2)

On an EC2 instance (Ubuntu):

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install python3 python3-pip python3-venv git -y

git clone https://github.com/your-username/your-flask-repo.git
cd your-flask-repo

python3 -m venv venv
source venv/bin/activate
pip3 install -r requirements.txt
```

Create `config.py` on the instance using **Option B** (the RDS endpoint), then run the app:

```bash
python app.py          # serves on 0.0.0.0:8000
```

Reach it in a browser at `http://your-ec2-public-dns:8000`. The EC2 security group must allow
inbound traffic on port **8000**.

To keep the app running after you disconnect from the EC2 session, use `screen`:

```bash
sudo apt install screen
screen                 # start a session, then run: python app.py
#   detach with CTRL+A then D  (app keeps running)
#   reattach later with: screen -r   (then CTRL+C to stop the app)
```

## Project notes

- `config.py` is gitignored (`.gitignore` lists it). Never commit real credentials. The
  committed template is `config.example.py`.
- Default market hours: open weekdays 9:00 AM–5:00 PM, closed on weekends and on any date an
  admin marks closed (holidays). Prices only move while the market is open.
- Every new account starts with $10,000 in virtual cash.
