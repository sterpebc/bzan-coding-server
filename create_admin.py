"""
A command-line script to create an initial admin user in the local datastore,
or reset an existing one's password.

This script addresses the bootstrapping problem where you need to be a logged-in
user to create a new user or change a password via the web UI's Users/
Change Password pages -- including the case where you're locked out because
you forgot the only admin account's password.

Usage:
    python create_admin.py <username>
        Creates a new user. Fails if the username already exists (use
        --reset to change an existing user's password instead).

    python create_admin.py <username> --reset
        Resets an existing user's password. Fails if the username doesn't
        exist (use without --reset to create it first).

Either way, the script securely prompts for the password (and a
confirmation) rather than taking it as a command-line argument, so it never
ends up in your shell history or `ps` output.
"""
import argparse
import getpass
import os
import sys

try:
    import datastore
    from werkzeug.security import generate_password_hash
except ImportError as e:
    print(f"Error importing necessary libraries: {e}", file=sys.stderr)
    print("Please ensure you have run 'pip install -r requirements.txt'", file=sys.stderr)
    sys.exit(1)

def prompt_for_password():
    """Prompts for a password twice and confirms the two entries match.
    Returns the password, or None (after printing an error) if it was
    empty or the confirmation didn't match."""
    password = getpass.getpass("Enter password: ")
    if not password:
        print("Error: Password cannot be empty.", file=sys.stderr)
        return None

    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        print("Error: Passwords did not match.", file=sys.stderr)
        return None

    return password

def main():
    """Creates a new user, or resets an existing user's password, in the
    local datastore."""
    parser = argparse.ArgumentParser(
        description="Create an admin user for the Coding Server, or reset an existing one's password.")
    parser.add_argument("username", help="The username to create or reset.")
    parser.add_argument(
        "--reset", action="store_true",
        help="Reset the password for an existing user instead of creating a new one.")
    args = parser.parse_args()

    username = args.username

    if not datastore.datastore:
        print("Error: the local datastore could not be initialized.", file=sys.stderr)
        sys.exit(1)

    user_exists = datastore.datastore.get_user(username) is not None

    if args.reset:
        if not user_exists:
            print(f"Error: User '{username}' does not exist. Run without --reset to create it.", file=sys.stderr)
            sys.exit(1)
    else:
        if user_exists:
            print(f"Error: User '{username}' already exists. Add --reset to change their password instead.", file=sys.stderr)
            sys.exit(1)

    try:
        password = prompt_for_password()
        if password is None:
            sys.exit(1)

        password_hash = generate_password_hash(password)
        if args.reset:
            datastore.datastore.update_user_password(username, password_hash)
            print(f"Successfully reset the password for user '{username}'.")
        else:
            datastore.datastore.add_user(username, password_hash, created_by='system_script')
            print(f"Successfully created user '{username}'.")

    except Exception as e:
        print(f"An unexpected error occurred: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == '__main__':
    main()