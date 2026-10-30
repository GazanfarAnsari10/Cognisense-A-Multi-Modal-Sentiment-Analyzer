"""One-time CLI for promoting an existing account to the initial admin."""

from __future__ import annotations

import argparse
import os
import sys

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from database.models import AuditLog, User
from database.session import get_session_factory


class ProvisioningError(Exception):
    pass


def promote_initial_admin(factory: sessionmaker, email: str, actor: str) -> int:
    normalized_email = email.strip().casefold()
    if not normalized_email or not actor.strip() or len(actor) > 200 or any(ord(char) < 32 for char in actor):
        raise ProvisioningError("Provisioning arguments are invalid.")

    provision_error = None
    target_id = None
    with factory() as session, session.begin():
        users = session.scalars(select(User).order_by(User.id).with_for_update()).all()
        target = next((user for user in users if user.email == normalized_email), None)
        if any(user.role == "admin" for user in users):
            provision_error = "An administrator is already provisioned."
        elif target is None or target.status != "active" or not target.password_hash:
            provision_error = "An existing active account with a password is required."
        else:
            target.role = "admin"
            target_id = target.id

        session.add(AuditLog(
            actor_identifier=f"operator:{actor.strip()}",
            action="admin.provision",
            target_type="user",
            target_identifier=str(target.id) if target is not None else None,
            outcome="denied" if provision_error else "success",
        ))

    if provision_error:
        raise ProvisioningError(provision_error)
    return target_id


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Promote one existing account to the initial Cognisense admin.")
    parser.add_argument("--email", required=True, help="Email address of the existing account")
    parser.add_argument("--confirm-email", required=True, help="Repeat the exact account email to confirm promotion")
    parser.add_argument("--actor", required=True, help="Operator identifier for the audit record")
    args = parser.parse_args(argv)

    if os.getenv("COGNISENSE_ALLOW_ADMIN_PROVISIONING") != "1":
        print("Admin provisioning is disabled; explicitly set COGNISENSE_ALLOW_ADMIN_PROVISIONING=1.", file=sys.stderr)
        return 2
    if args.email.strip().casefold() != args.confirm_email.strip().casefold():
        print("Confirmation email does not match.", file=sys.stderr)
        return 2

    try:
        user_id = promote_initial_admin(get_session_factory(), args.email, args.actor)
    except ProvisioningError as error:
        print(str(error), file=sys.stderr)
        return 1
    except (SQLAlchemyError, RuntimeError, ValueError):
        print("Admin provisioning failed; check database availability and configuration.", file=sys.stderr)
        return 1

    print(f"Initial admin provisioned for existing user id {user_id}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())