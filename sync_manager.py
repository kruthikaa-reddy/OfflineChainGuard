import hashlib
import json
import math
from datetime import datetime


def calculate_amendment_hash(data):
    """Return a deterministic SHA-256 hash for an amendment/resolution record."""
    encoded = json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()

def verify_amendment_integrity(amendment):
    """Verify that an amendment's stored data matches its SHA-256 hash."""

    amendment_data = {
        "transaction_id": amendment["transaction_id"],
        "device_id": amendment["device_id"],
        "request_id": amendment["request_id"],
        "base_version": amendment["base_version"],
        "sender": amendment["new_sender"],
        "receiver": amendment["new_receiver"],
        "amount": float(amendment["new_amount"]),
        "created_at": amendment["created_at"],
    }

    calculated_hash = calculate_amendment_hash(amendment_data)

    return calculated_hash == amendment["amendment_hash"]

def queue_offline_amendment(
    conn, transaction_id, device_id, request_id, base_version,
    sender, receiver, amount
):
    """
    Queue an offline amendment once per request_id.
    Reusing a request_id with different content is rejected.
    """
    created_at = datetime.now().isoformat()
    amendment_data = {
        "transaction_id": transaction_id,
        "device_id": device_id,
        "request_id": request_id,
        "base_version": base_version,
        "sender": sender,
        "receiver": receiver,
        "amount": amount,
        "created_at": created_at,
    }
    amendment_hash = calculate_amendment_hash(amendment_data)

    existing = conn.execute(
        "SELECT * FROM offline_amendments WHERE request_id = ?",
        (request_id,),
    ).fetchone()

    if existing:
        same_payload = (
            existing["transaction_id"] == transaction_id
            and existing["device_id"] == device_id
            and existing["base_version"] == base_version
            and existing["new_sender"] == sender
            and existing["new_receiver"] == receiver
            and float(existing["new_amount"]) == float(amount)
        )
        if not same_payload:
            raise ValueError(
                "This request_id already exists with different amendment data."
            )
        return {"created": False, "amendment": dict(existing)}

    conn.execute(
        """
        INSERT INTO offline_amendments (
            transaction_id, device_id, request_id, base_version,
            new_sender, new_receiver, new_amount, created_at,
            amendment_hash, status
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING')
        """,
        (
            transaction_id, device_id, request_id, base_version,
            sender, receiver, amount, created_at, amendment_hash,
        ),
    )
    conn.commit()

    saved = conn.execute(
        "SELECT * FROM offline_amendments WHERE request_id = ?",
        (request_id,),
    ).fetchone()
    return {"created": True, "amendment": dict(saved)}


def _append_block_with_db_commit(conn, blockchain, block_data):
    """
    Append a block while the SQLite changes are still uncommitted.
    If SQLite commit fails, attempt to restore the previous chain.
    This is a compensating safeguard across two separate storage systems,
    not a true distributed atomic transaction.
    """
    previous_chain = list(blockchain.chain)
    blockchain.add_block(block_data)
    try:
        conn.commit()
    except Exception:
        conn.rollback()
        blockchain.chain = previous_chain
        blockchain.save_chain()
        raise


def synchronize_device(conn, blockchain, device_id):
    """Synchronize pending amendments and mark stale amendments as conflicts."""
    pending = conn.execute(
        """
        SELECT * FROM offline_amendments
        WHERE device_id = ? AND status = 'PENDING'
        ORDER BY id ASC
        """,
        (device_id,),
    ).fetchall()

    results = []

    for amendment in pending:
        # Verify amendment integrity before processing it.
        if not verify_amendment_integrity(amendment):
            conn.execute(
                """
                UPDATE offline_amendments
                SET status = 'REJECTED'
                WHERE id = ?
                """,
                (amendment["id"],),
            )
            conn.commit()

            results.append({
                "amendment_id": amendment["id"],
                "status": "REJECTED",
                "reason": "Amendment integrity verification failed",
            })
            continue

        transaction = conn.execute(
            "SELECT * FROM transactions WHERE id = ?",
            (amendment["transaction_id"],),
        ).fetchone()

        if transaction is None:
            conn.execute(
                "UPDATE offline_amendments SET status = 'REJECTED' WHERE id = ?",
                (amendment["id"],),
            )
            conn.commit()
            results.append({
                "amendment_id": amendment["id"],
                "status": "REJECTED",
                "reason": "Transaction does not exist",
            })
            continue

        current_version = transaction["version"]
        if amendment["base_version"] != current_version:
            conn.execute(
                "UPDATE offline_amendments SET status = 'CONFLICT' WHERE id = ?",
                (amendment["id"],),
            )
            conn.commit()
            results.append({
                "amendment_id": amendment["id"],
                "transaction_id": amendment["transaction_id"],
                "status": "CONFLICT",
                "base_version": amendment["base_version"],
                "current_version": current_version,
                "reason": "Transaction was modified after this offline amendment",
            })
            continue

        old_data = {
            "sender": transaction["sender"],
            "receiver": transaction["receiver"],
            "amount": transaction["amount"],
        }
        new_data = {
            "sender": amendment["new_sender"],
            "receiver": amendment["new_receiver"],
            "amount": amendment["new_amount"],
        }
        new_version = current_version + 1
        synced_at = datetime.now().isoformat()

        try:
            conn.execute(
                """
                UPDATE transactions
                SET sender = ?, receiver = ?, amount = ?, version = ?
                WHERE id = ?
                """,
                (
                    new_data["sender"],
                    new_data["receiver"],
                    new_data["amount"],
                    new_version,
                    amendment["transaction_id"],
                ),
            )
            conn.execute(
                """
                INSERT INTO transaction_history (
                    transaction_id, old_sender, old_receiver, old_amount,
                    new_sender, new_receiver, new_amount, edited_at, amendment_hash
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    amendment["transaction_id"],
                    old_data["sender"],
                    old_data["receiver"],
                    old_data["amount"],
                    new_data["sender"],
                    new_data["receiver"],
                    new_data["amount"],
                    synced_at,
                    amendment["amendment_hash"],
                ),
            )
            conn.execute(
                "UPDATE offline_amendments SET status = 'SYNCED' WHERE id = ?",
                (amendment["id"],),
            )

            block_data = {
                "type": "OFFLINE_AMENDMENT_SYNCED",
                "transaction_id": amendment["transaction_id"],
                "device_id": device_id,
                "request_id": amendment["request_id"],
                "base_version": amendment["base_version"],
                "new_version": new_version,
                "old_data": old_data,
                "new_data": new_data,
                "amendment_hash": amendment["amendment_hash"],
                "synced_at": synced_at,
            }
            _append_block_with_db_commit(conn, blockchain, block_data)
        except Exception:
            conn.rollback()
            raise

        results.append({
            "amendment_id": amendment["id"],
            "transaction_id": amendment["transaction_id"],
            "status": "SYNCED",
            "new_version": new_version,
        })

    return results


def resolve_conflict(conn, blockchain, amendment_id, action, sender=None,
                     receiver=None, amount=None):
    """
    Resolve one CONFLICT amendment.

    KEEP_CURRENT records the decision without changing the transaction.
    ACCEPT_PROPOSED applies the conflicting proposal.
    MANUAL applies user-entered values.
    """
    allowed_actions = {"KEEP_CURRENT", "ACCEPT_PROPOSED", "MANUAL"}
    if action not in allowed_actions:
        raise ValueError("Invalid conflict resolution action.")

    amendment = conn.execute(
        "SELECT * FROM offline_amendments WHERE id = ?",
        (amendment_id,),
    ).fetchone()
    if amendment is None:
        raise ValueError("Amendment not found.")
    if amendment["status"] != "CONFLICT":
        raise ValueError("Only amendments with CONFLICT status can be resolved.")

    transaction = conn.execute(
        "SELECT * FROM transactions WHERE id = ?",
        (amendment["transaction_id"],),
    ).fetchone()
    if transaction is None:
        raise ValueError("The related transaction no longer exists.")

    current_data = {
        "sender": transaction["sender"],
        "receiver": transaction["receiver"],
        "amount": transaction["amount"],
    }

    if action == "KEEP_CURRENT":
        chosen_data = current_data
        changes_transaction = False
    elif action == "ACCEPT_PROPOSED":
        chosen_data = {
            "sender": amendment["new_sender"],
            "receiver": amendment["new_receiver"],
            "amount": float(amendment["new_amount"]),
        }
        changes_transaction = True
    else:
        try:
            amount = float(amount)
        except (TypeError, ValueError):
            raise ValueError("Enter a valid positive amount.")
        sender = (sender or "").strip()
        receiver = (receiver or "").strip()
        if (
            not sender
            or not receiver
            or not math.isfinite(amount)
            or amount <= 0
        ):
            raise ValueError("Sender, receiver, and a valid positive amount are required.")
        chosen_data = {"sender": sender, "receiver": receiver, "amount": amount}
        changes_transaction = True

    resolved_at = datetime.now().isoformat()
    resolution_data = {
        "type": "OFFLINE_CONFLICT_RESOLVED",
        "amendment_id": amendment_id,
        "transaction_id": amendment["transaction_id"],
        "device_id": amendment["device_id"],
        "request_id": amendment["request_id"],
        "action": action,
        "base_version": amendment["base_version"],
        "version_before": transaction["version"],
        "version_after": transaction["version"] + (1 if changes_transaction else 0),
        "current_data": current_data,
        "proposed_data": {
            "sender": amendment["new_sender"],
            "receiver": amendment["new_receiver"],
            "amount": amendment["new_amount"],
        },
        "resolved_data": chosen_data,
        "resolved_at": resolved_at,
    }
    resolution_hash = calculate_amendment_hash(resolution_data)
    new_status = {
        "KEEP_CURRENT": "RESOLVED_KEPT_CURRENT",
        "ACCEPT_PROPOSED": "RESOLVED_ACCEPTED_PROPOSAL",
        "MANUAL": "RESOLVED_MANUAL",
    }[action]

    try:
        if changes_transaction:
            conn.execute(
                """
                UPDATE transactions
                SET sender = ?, receiver = ?, amount = ?, version = version + 1
                WHERE id = ?
                """,
                (
                    chosen_data["sender"], chosen_data["receiver"],
                    chosen_data["amount"], amendment["transaction_id"],
                ),
            )
            conn.execute(
                """
                INSERT INTO transaction_history (
                    transaction_id, old_sender, old_receiver, old_amount,
                    new_sender, new_receiver, new_amount, edited_at, amendment_hash
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    amendment["transaction_id"],
                    current_data["sender"], current_data["receiver"], current_data["amount"],
                    chosen_data["sender"], chosen_data["receiver"], chosen_data["amount"],
                    resolved_at, resolution_hash,
                ),
            )

        conn.execute(
            """
            UPDATE offline_amendments
            SET status = ?, resolution_action = ?, resolved_at = ?,
                resolution_hash = ?
            WHERE id = ?
            """,
            (new_status, action, resolved_at, resolution_hash, amendment_id),
        )

        resolution_data["resolution_hash"] = resolution_hash
        _append_block_with_db_commit(conn, blockchain, resolution_data)
    except Exception:
        conn.rollback()
        raise

    return {
        "amendment_id": amendment_id,
        "status": new_status,
        "action": action,
        "resolution_hash": resolution_hash,
        "transaction_changed": changes_transaction,
    }
