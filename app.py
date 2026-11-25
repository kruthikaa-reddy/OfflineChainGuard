from flask import Flask, render_template, request, redirect, url_for, flash, jsonify,send_from_directory
from flask_wtf.csrf import CSRFProtect
import html
import sqlite3
import os
import uuid
from datetime import datetime
import hashlib
import json
import math
from flask import session
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
import secrets
from blockchain.chain import Blockchain
from sync_manager import (
    queue_offline_amendment,
    synchronize_device
)

app = Flask(__name__)
app.secret_key = os.environ.get(
    "FLASK_SECRET_KEY",
    "offlinechainguard-development-key-change-before-deployment"
)
csrf = CSRFProtect(app)

DATABASE = "transactions.db"

# Create blockchain
my_blockchain = Blockchain()


# DATABASE CONNECTION
def get_db_connection():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

# LOGIN REQUIRED DECORATOR
def login_required(route_function):

    @wraps(route_function)
    def wrapper(*args, **kwargs):

        if "user_id" not in session:
            flash("Please log in to access this page.")
            return redirect(url_for("login"))

        return route_function(*args, **kwargs)

    return wrapper


# DATABASE INITIALIZATION
def initialize_database():

    conn = get_db_connection()

    # Users table for authentication
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    # Transactions table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sender TEXT NOT NULL,
            receiver TEXT NOT NULL,
            amount REAL NOT NULL,
            version INTEGER NOT NULL DEFAULT 1
        )
    """)

    # Upgrade existing databases safely
    columns = conn.execute(
        "PRAGMA table_info(transactions)"
    ).fetchall()

    column_names = [column["name"] for column in columns]

    if "version" not in column_names:
        conn.execute("""
            ALTER TABLE transactions
            ADD COLUMN version INTEGER NOT NULL DEFAULT 1
        """)

    # Transaction amendment history
    conn.execute("""
        CREATE TABLE IF NOT EXISTS transaction_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            transaction_id INTEGER,
            old_sender TEXT,
            old_receiver TEXT,
            old_amount REAL,
            new_sender TEXT,
            new_receiver TEXT,
            new_amount REAL,
            edited_at TEXT,
            amendment_hash TEXT
        )
    """)

    # Offline amendment queue
    conn.execute("""
        CREATE TABLE IF NOT EXISTS offline_amendments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            transaction_id INTEGER NOT NULL,
            device_id TEXT NOT NULL,
            request_id TEXT NOT NULL UNIQUE,
            base_version INTEGER NOT NULL,
            new_sender TEXT NOT NULL,
            new_receiver TEXT NOT NULL,
            new_amount REAL NOT NULL,
            created_at TEXT NOT NULL,
            amendment_hash TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'PENDING'
        )
    """)

     # Bank account balances
    conn.execute("""
        CREATE TABLE IF NOT EXISTS account_balances (
            account_name TEXT PRIMARY KEY,
            balance REAL NOT NULL DEFAULT 10000.00
        )
    """)
    conn.commit()
    conn.close()

# USER REGISTRATION
@app.route('/register', methods=['GET', 'POST'])
def register():

    if request.method == 'POST':

        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')

        if not username or not password:
            flash("Username and password are required.")
            return redirect(url_for('register'))

        if len(username) < 3 or len(username) > 30:
            flash("Username must be between 3 and 30 characters.")
            return redirect(url_for('register'))

        if len(password) < 8:
            flash("Password must contain at least 8 characters.")
            return redirect(url_for('register'))

        password_hash = generate_password_hash(password)

        conn = get_db_connection()

        try:
            conn.execute("""
                INSERT INTO users (
                    username,
                    password_hash,
                    created_at
                )
                VALUES (?, ?, ?)
            """, (
                username,
                password_hash,
                datetime.now().isoformat()
            ))

            conn.commit()

            flash("Registration successful! Please log in.")
            return redirect(url_for('login'))

        except sqlite3.IntegrityError:
            flash("Username already exists.")

        finally:
            conn.close()

    return render_template('register.html')

# USER LOGIN
@app.route('/login', methods=['GET', 'POST'])
def login():

    if request.method == 'POST':

        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')

        conn = get_db_connection()

        user = conn.execute("""
            SELECT * FROM users
            WHERE username = ?
        """, (username,)).fetchone()

        conn.close()

        if user and check_password_hash(
            user['password_hash'], password
        ):

            session.clear()
            session['user_id'] = user['id']
            session['username'] = user['username']

            flash("Login successful!")
            return redirect(url_for('home'))

        flash("Invalid username or password.")

    return render_template('login.html')

# USER LOGOUT
@app.route('/logout')
def logout():

    session.clear()

    flash("You have been logged out.")

    return redirect(url_for('login'))


# HOME

@app.route('/')
@login_required
def home():
    username = html.escape(str(session.get('username', 'User')))

    return f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">

        <title>OfflineChainGuard | Dashboard</title>

        <style>
            * {{
                margin: 0;
                padding: 0;
                box-sizing: border-box;
            }}

            body {{
                font-family: Arial, sans-serif;
                background: #211A2B;
                color: #F5EDF3;
                min-height: 100vh;
            }}

            header {{
                background: #302334;
                padding: 25px 6%;
                border-bottom: 2px solid #79516E;
            }}

            header h1 {{
                color: #E8B99B;
                font-size: 28px;
            }}

            .welcome {{
                color: #D9B8C8;
                margin-top: 9px;
            }}

            .container {{
                width: 90%;
                max-width: 1150px;
                margin: 38px auto;
            }}

            .section-title {{
                color: #E8B99B;
                font-size: 21px;
                margin-bottom: 20px;
            }}

            .cards {{
                display: grid;
                grid-template-columns:
                    repeat(auto-fit, minmax(240px, 1fr));
                gap: 22px;
                margin-bottom: 38px;
            }}

            .card {{
                background: #49334F;
                border: 1px solid #79516E;
                border-radius: 14px;
                padding: 25px;
                transition: 0.3s;
            }}

            .card:hover {{
                transform: translateY(-5px);
                border-color: #C17F8C;
                box-shadow: 0 9px 22px rgba(0, 0, 0, 0.25);
            }}

            .card h3 {{
                color: #E8B99B;
                font-size: 18px;
                margin-bottom: 13px;
            }}

            .card p {{
                color: #E0CBD9;
                line-height: 1.6;
                font-size: 14px;
                min-height: 45px;
                margin-bottom: 18px;
            }}

            .card a {{
                display: inline-block;
                background: #C17F8C;
                color: #211A2B;
                text-decoration: none;
                padding: 11px 17px;
                border-radius: 7px;
                font-weight: bold;
                font-size: 14px;
                transition: 0.3s;
            }}

            .card a:hover {{
                background: #E8B99B;
            }}

            .logout {{
                display: inline-block;
                margin-top: 5px;
                padding: 11px 20px;
                border-radius: 7px;
                background: #79516E;
                color: #FFFFFF;
                text-decoration: none;
            }}

            .logout:hover {{
                background: #A45F7E;
            }}

            footer {{
                text-align: center;
                padding: 23px;
                color: #C9AFC2;
                border-top: 1px solid #49334F;
                margin-top: 45px;
                font-size: 13px;
            }}

            @media (max-width: 600px) {{
                .container {{
                    width: 94%;
                }}

                header h1 {{
                    font-size: 23px;
                }}
            }}
        </style>
    </head>

    <body>

        <header>
            <h1>OfflineChainGuard</h1>
            <p class="welcome">Welcome back, {username}!</p>
        </header>

        <main class="container">

            <h2 class="section-title">
                Transaction Management
            </h2>

            <div class="cards">

                <div class="card">
                    <h3>View Transactions</h3>
                    <p>
                        Explore and manage your existing transactions.
                    </p>
                    <a href="/transactions">View Transactions</a>
                </div>

                <div class="card">
                    <h3>Create Transaction</h3>
                    <p>
                        Create a new transaction in the system.
                    </p>
                    <a href="/create_transaction">Create Transaction</a>
                </div>

                <div class="card">
                    <h3>Amendment History</h3>
                    <p>
                        Review previous transaction modifications.
                    </p>
                    <a href="/edit_history">View History</a>
                </div>

            </div>

            <h2 class="section-title">
                Blockchain Management
            </h2>

            <div class="cards">

                <div class="card">
                    <h3>View Blockchain</h3>
                    <p>
                        Explore blocks and recorded transaction details.
                    </p>
                    <a href="/blockchain">View Blockchain</a>
                </div>

                <div class="card">
                    <h3>Verify Blockchain</h3>
                    <p>
                        Check the integrity and validity of your blockchain.
                    </p>
                    <a href="/verify_blockchain">Verify Blockchain</a>
                </div>

            </div>

            <h2 class="section-title">
                Offline Synchronization
            </h2>

            <div class="cards">

                <div class="card">
                    <h3>Offline Amendments</h3>
                    <p>
                        View queued amendments and synchronization status.
                    </p>
                    <a href="/offline/amendments">View Amendments</a>
                </div>

            </div>

            <a href="/logout" class="logout">Logout →</a>

        </main>

        <footer>
            OfflineChainGuard | Offline Transaction Synchronization and Security Monitoring
        </footer>

    </body>
    </html>
    """
# CREATE TRANSACTION
@app.route('/create_transaction', methods=['GET', 'POST'])
@login_required
def create_transaction():

    if request.method == 'POST':

        sender = request.form.get('sender', '').strip()
        receiver = request.form.get('receiver', '').strip()
        request_id = request.form.get('request_id', '').strip()

        try:
            amount = float(request.form.get('amount', ''))

            if (
                not sender
                or not receiver
                or not math.isfinite(amount)
                or amount <= 0
            ):
                raise ValueError

        except (ValueError, TypeError):
            return jsonify({
                "success": False,
                "error": "Invalid transaction details"
            }), 400

        conn = get_db_connection()

        try:

            # Start a safe database transaction
            conn.execute("BEGIN IMMEDIATE")

            # Prevent duplicate transaction synchronization
            conn.execute("""
                CREATE TABLE IF NOT EXISTS transaction_sync_requests (
                    request_id TEXT PRIMARY KEY,
                    transaction_id INTEGER NOT NULL
                )
            """)

            # Check for an already processed request
            if request_id:

                existing = conn.execute("""
                    SELECT transaction_id
                    FROM transaction_sync_requests
                    WHERE request_id = ?
                """, (request_id,)).fetchone()

                if existing:

                    conn.rollback()
                    conn.close()

                    return jsonify({
                        "success": True,
                        "duplicate": True,
                        "transaction_id": existing["transaction_id"],
                        "message": "Transaction was already synchronized"
                    })

            # Create sender account if it doesn't exist
            conn.execute("""
                INSERT OR IGNORE INTO account_balances (
                    account_name, balance
                )
                VALUES (?, 10000.00)
            """, (sender,))

            # Create receiver account if it doesn't exist
            conn.execute("""
                INSERT OR IGNORE INTO account_balances (
                    account_name, balance
                )
                VALUES (?, 10000.00)
            """, (receiver,))

            # Get sender's available balance
            sender_account = conn.execute("""
                SELECT balance
                FROM account_balances
                WHERE account_name = ?
            """, (sender,)).fetchone()

            available_balance = sender_account["balance"]

            # Check whether sender has enough money
            if amount > available_balance:

                conn.rollback()
                conn.close()

                return jsonify({
                    "success": False,
                    "error": "Insufficient balance",
                    "available_balance": available_balance,
                    "requested_amount": amount
                }), 400

            # Create the transaction
            cursor = conn.execute("""
                INSERT INTO transactions (
                    sender, receiver, amount
                )
                VALUES (?, ?, ?)
            """, (sender, receiver, amount))

            transaction_id = cursor.lastrowid

            # Deduct amount from sender
            conn.execute("""
                UPDATE account_balances
                SET balance = balance - ?
                WHERE account_name = ?
            """, (amount, sender))

            # Add amount to receiver
            conn.execute("""
                UPDATE account_balances
                SET balance = balance + ?
                WHERE account_name = ?
            """, (amount, receiver))

            # Save request ID for duplicate protection
            if request_id:

                conn.execute("""
                    INSERT INTO transaction_sync_requests (
                        request_id, transaction_id
                    )
                    VALUES (?, ?)
                """, (request_id, transaction_id))

            # Get updated balances
            sender_updated = conn.execute("""
                SELECT balance
                FROM account_balances
                WHERE account_name = ?
            """, (sender,)).fetchone()

            receiver_updated = conn.execute("""
                SELECT balance
                FROM account_balances
                WHERE account_name = ?
            """, (receiver,)).fetchone()

            sender_balance = sender_updated["balance"]
            receiver_balance = receiver_updated["balance"]

            # Save all database changes together
            conn.commit()
            conn.close()

        except Exception as e:

            conn.rollback()
            conn.close()

            print("Transaction error:", e)

            return jsonify({
                "success": False,
                "error": "Transaction could not be completed"
            }), 500

        # Add successful transaction to blockchain
        my_blockchain.add_block({
            "transaction_id": transaction_id,
            "sender": sender,
            "receiver": receiver,
            "amount": amount,
            "sender_balance_after": sender_balance,
            "receiver_balance_after": receiver_balance,
            "type": "NEW_TRANSACTION"
        })

        return jsonify({
            "success": True,
            "duplicate": False,
            "transaction_id": transaction_id,
            "sender_balance": sender_balance,
            "receiver_balance": receiver_balance,
            "message": "Transaction completed successfully"
        })

    return render_template('create_transaction.html')
# VIEW TRANSACTIONS
@app.route('/transactions')
@login_required
def transaction_history():

    conn = get_db_connection()

    transactions = conn.execute("""
        SELECT * FROM transactions
        ORDER BY id DESC
    """).fetchall()

    conn.close()

    return render_template(
        'transactions.html',
        transactions=transactions
    )


# EDIT TRANSACTION
@app.route(
    '/edit_transaction/<int:transaction_id>',
    methods=['GET', 'POST']
)
@login_required


def edit_transaction(transaction_id):

    conn = get_db_connection()

    transaction = conn.execute("""
        SELECT * FROM transactions
        WHERE id = ?
    """, (transaction_id,)).fetchone()

    if transaction is None:
        conn.close()
        return "Transaction not found", 404

    if request.method == 'POST':

        sender = request.form['sender'].strip()
        receiver = request.form['receiver'].strip()

        try:
            amount = float(request.form['amount'])

            if not sender or not receiver or amount <= 0:
                raise ValueError

        except ValueError:
            conn.close()
            return "Invalid transaction details", 400

        old_data = {
            "sender": transaction['sender'],
            "receiver": transaction['receiver'],
            "amount": transaction['amount']
        }

        new_data = {
            "sender": sender,
            "receiver": receiver,
            "amount": amount
        }

        edited_at = datetime.now().isoformat()

        amendment_data = {
            "transaction_id": transaction_id,
            "old_data": old_data,
            "new_data": new_data,
            "edited_at": edited_at
        }

        amendment_hash = hashlib.sha256(
            json.dumps(
                amendment_data,
                sort_keys=True
            ).encode()
        ).hexdigest()

        # Save amendment history
        conn.execute("""
            INSERT INTO transaction_history (
                transaction_id,
                old_sender,
                old_receiver,
                old_amount,
                new_sender,
                new_receiver,
                new_amount,
                edited_at,
                amendment_hash
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            transaction_id,
            old_data["sender"],
            old_data["receiver"],
            old_data["amount"],
            new_data["sender"],
            new_data["receiver"],
            new_data["amount"],
            edited_at,
            amendment_hash
        ))

        # Update transaction and increment version
        conn.execute("""
            UPDATE transactions
            SET sender = ?,
                receiver = ?,
                amount = ?,
                version = version + 1
            WHERE id = ?
        """, (
            sender,
            receiver,
            amount,
            transaction_id
        ))

        conn.commit()
        conn.close()

        # Record amendment on blockchain
        my_blockchain.add_block({
            "transaction_id": transaction_id,
            "type": "TRANSACTION_AMENDMENT",
            "old_data": old_data,
            "new_data": new_data,
            "amendment_hash": amendment_hash,
            "edited_at": edited_at
        })

        flash("Transaction updated and amendment added to blockchain!")

        return redirect(url_for('transaction_history'))

    conn.close()

    return render_template(
        'edit_transaction.html',
        transaction=transaction
    )


# VIEW EDIT HISTORY
@app.route('/edit_history')
@login_required
def edit_history():

    conn = get_db_connection()

    history = conn.execute("""
        SELECT * FROM transaction_history
        ORDER BY id DESC
    """).fetchall()

    conn.close()

    return render_template(
        'edit_history.html',
        history=history
    )


# QUEUE AN OFFLINE AMENDMENT
@app.route('/offline/amendments', methods=['POST'])
@login_required
def create_offline_amendment():

    data = request.get_json(silent=True) or {}

    required_fields = [
        "transaction_id",
        "device_id",
        "base_version",
        "sender",
        "receiver",
        "amount"
    ]

    if any(field not in data for field in required_fields):
        return {"error": "Missing required fields"}, 400

    try:
        transaction_id = int(data["transaction_id"])
        base_version = int(data["base_version"])
        amount = float(data["amount"])

        device_id = str(data["device_id"]).strip()
        request_id = str(
            data.get("request_id") or uuid.uuid4()
        ).strip()
        sender = str(data["sender"]).strip()
        receiver = str(data["receiver"]).strip()

        if (
            transaction_id <= 0
            or base_version < 1
            or not math.isfinite(amount)
            or amount <= 0
            or not device_id
            or not request_id
            or not sender
            or not receiver
        ):
            raise ValueError

    except (ValueError, TypeError):
        return {"error": "Invalid amendment details"}, 400

    conn = get_db_connection()

    try:

        transaction = conn.execute("""
            SELECT id FROM transactions
            WHERE id = ?
        """, (transaction_id,)).fetchone()

        if transaction is None:
            return {"error": "Transaction not found"}, 404

        result = queue_offline_amendment(
            conn,
            transaction_id,
            device_id,
            request_id,
            base_version,
            sender,
            receiver,
            amount
        )

        return {
            "message": (
                "Offline amendment queued"
                if result["created"]
                else "This request was already queued"
            ),
            "created": result["created"],
            "amendment": result["amendment"]
        }, 201 if result["created"] else 200

    except ValueError as e:
        return {"error": str(e)}, 400

    except sqlite3.Error:
        app.logger.exception("Could not queue offline amendment")
        return {"error": "Could not queue offline amendment"}, 500

    finally:
        conn.close()


# SYNCHRONIZE OFFLINE AMENDMENTS
@app.route('/sync/<device_id>', methods=['POST'])
@login_required
def synchronize_offline_transactions(device_id):

    if not device_id.strip():
        return {"error": "Device ID is required"}, 400

    conn = get_db_connection()

    try:

        results = synchronize_device(
            conn,
            my_blockchain,
            device_id
        )

        return {
            "device_id": device_id,
            "total_processed": len(results),
            "results": results
        }

    except Exception:
        app.logger.exception("Synchronization failed")

        return {
            "error": "Synchronization failed. Check the server log."
        }, 500

    finally:
        conn.close()




# BLOCKCHAIN VERIFICATION
@app.route('/verify_blockchain')
@login_required
def verify_blockchain():

    is_valid = my_blockchain.validate_chain()

    if is_valid:
        return """
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Blockchain Verification | OfflineChainGuard</title>

            <style>
                * {
                    box-sizing: border-box;
                }

                body {
                    margin: 0;
                    padding: 25px;
                    min-height: 100vh;
                    display: flex;
                    justify-content: center;
                    align-items: center;
                    font-family: Arial, sans-serif;
                    background: #202A38;
                    color: #F4F1E9;
                }

                .container {
                    width: 100%;
                    max-width: 600px;
                    background: #293749;
                    padding: 45px 35px;
                    border-radius: 18px;
                    text-align: center;
                    border-top: 5px solid #75A98A;
                    box-shadow: 0 15px 40px rgba(0,0,0,0.3);
                }

                .symbol {
                    width: 85px;
                    height: 85px;
                    margin: 0 auto 22px;
                    border-radius: 50%;
                    background: #294C43;
                    color: #A9D7B9;
                    display: flex;
                    justify-content: center;
                    align-items: center;
                    font-size: 43px;
                }

                h1 {
                    color: #A9D7B9;
                    margin-bottom: 12px;
                }

                p {
                    color: #CDD5DF;
                    line-height: 1.7;
                }

                .status {
                    background: #294C43;
                    padding: 17px;
                    border-radius: 10px;
                    margin: 25px 0;
                    color: #B8E1C5;
                    font-weight: bold;
                    letter-spacing: 1px;
                }

                .description {
                    font-size: 14px;
                }

                a {
                    display: inline-block;
                    margin: 10px 6px 0;
                    padding: 13px 20px;
                    border-radius: 8px;
                    text-decoration: none;
                    font-weight: bold;
                    font-size: 13px;
                    background: #B69B59;
                    color: #202A38;
                }

                a:hover {
                    opacity: 0.85;
                }

                .secondary {
                    background: #F4F1E9;
                    color: #26364D;
                }
            </style>
        </head>

        <body>
            <div class="container">

                <div class="symbol">&#10003;</div>

                <h1>Blockchain Verified!</h1>

                <p>
                    The integrity check has been completed successfully.
                </p>

                <div class="status">
                    BLOCKCHAIN STATUS: VALID
                </div>

                <p class="description">
                    All blocks passed the blockchain validation check.
                    No hash-link inconsistency or tampering was detected
                    during this verification.
                </p>

                <a href="/blockchain">View Blockchain</a>
                <a href="/" class="secondary">Back to Dashboard</a>

            </div>
        </body>
        </html>
        """

    else:
        return """
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Blockchain Verification | OfflineChainGuard</title>

            <style>
                * {
                    box-sizing: border-box;
                }

                body {
                    margin: 0;
                    padding: 25px;
                    min-height: 100vh;
                    display: flex;
                    justify-content: center;
                    align-items: center;
                    font-family: Arial, sans-serif;
                    background: #202A38;
                    color: #F4F1E9;
                }

                .container {
                    width: 100%;
                    max-width: 600px;
                    background: #293749;
                    padding: 45px 35px;
                    border-radius: 18px;
                    text-align: center;
                    border-top: 5px solid #C87575;
                    box-shadow: 0 15px 40px rgba(0,0,0,0.3);
                }

                .symbol {
                    width: 85px;
                    height: 85px;
                    margin: 0 auto 22px;
                    border-radius: 50%;
                    background: #57363D;
                    color: #F0AAAA;
                    display: flex;
                    justify-content: center;
                    align-items: center;
                    font-size: 43px;
                }

                h1 {
                    color: #F0AAAA;
                    margin-bottom: 12px;
                }

                p {
                    color: #CDD5DF;
                    line-height: 1.7;
                }

                .status {
                    background: #57363D;
                    padding: 17px;
                    border-radius: 10px;
                    margin: 25px 0;
                    color: #F0AAAA;
                    font-weight: bold;
                    letter-spacing: 1px;
                }

                .description {
                    font-size: 14px;
                }

                a {
                    display: inline-block;
                    margin: 10px 6px 0;
                    padding: 13px 20px;
                    border-radius: 8px;
                    text-decoration: none;
                    font-weight: bold;
                    font-size: 13px;
                    background: #B69B59;
                    color: #202A38;
                }

                a:hover {
                    opacity: 0.85;
                }

                .secondary {
                    background: #F4F1E9;
                    color: #26364D;
                }
            </style>
        </head>

        <body>
            <div class="container">

                <div class="symbol">&#10007;</div>

                <h1>Tampering Detected!</h1>

                <p>
                    The blockchain integrity check has failed.
                </p>

                <div class="status">
                    BLOCKCHAIN STATUS: INVALID
                </div>

                <p class="description">
                    The blockchain validation detected an inconsistency.
                    The chain should be investigated before relying on
                    its transaction records.
                </p>

                <a href="/blockchain">Inspect Blockchain</a>
                <a href="/" class="secondary">Back to Dashboard</a>

            </div>
        </body>
        </html>
    """


# DISPLAY BLOCKCHAIN
@app.route('/blockchain')
@login_required
def display_blockchain():

    blocks = []

    for block in my_blockchain.chain:

        blocks.append({
            "index": block.index,
            "timestamp": block.timestamp,
            "data": block.data,
            "previous_hash": block.previous_hash,
            "hash": block.hash
        })

    return render_template(
        "blockchain.html",
        total_blocks=len(blocks),
        blocks=blocks
    )


# DISPLAY OFFLINE AMENDMENTS
@app.route('/offline/amendments', methods=['GET'])
@login_required
def view_offline_amendments():

    conn = get_db_connection()

    amendments = conn.execute("""
        SELECT * FROM offline_amendments
        ORDER BY id DESC
    """).fetchall()

    conn.close()

    amendments = [
        dict(amendment) for amendment in amendments
    ]

    return render_template(
        "offline_amendments.html",
        total_amendments=len(amendments),
        amendments=amendments
    )


# SERVICE WORKER
@app.route('/service-worker.js')
def service_worker():

    response = send_from_directory(
        'static',
        'service-worker.js'
    )

    response.headers['Service-Worker-Allowed'] = '/'

    return response


# OFFLINE FALLBACK PAGE
@app.route('/offline-page.html')
def offline_page():

    return render_template('offline-page.html')

# START APPLICATION
if __name__ == '__main__':

    initialize_database()

    app.run(debug=False)

