"""
A command-line script to create an initial admin user in Firestore.

This script addresses the bootstrapping problem where you need to be a logged-in
user to create a new user.

Usage:
    python create_admin.py <username>

The script will securely prompt for a password for the new user.
"""
import argparse
import getpass
import os
import sys

# Add the 'sqlite_web' directory to the Python path to allow importing the datastore module.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'sqlite_web'))

try:
    # It's important to set the project before importing the datastore
    # if it's not already set in the environment.
    if 'GOOGLE_CLOUD_PROJECT' not in os.environ:
        os.environ['GOOGLE_CLOUD_PROJECT'] = 'bzan-coding-server'

    from sqlite_web import datastore
    from werkzeug.security import generate_password_hash
except ImportError as e:
    print(f"Error importing necessary libraries: {e}", file=sys.stderr)
    print("Please ensure you have run 'pip install -r requirements.txt'", file=sys.stderr)
    sys.exit(1)

def main():
    """Creates a new user in the Firestore database."""
    parser = argparse.ArgumentParser(description="Create an admin user for the Coding Server.")
    parser.add_argument("username", help="The username for the new admin user.")
    args = parser.parse_args()

    username = args.username

    if not datastore.datastore:
        print("Error: Firestore is not configured or the client library is not installed.", file=sys.stderr)
        print("Please check your configuration and 'google-cloud-firestore' installation.", file=sys.stderr)
        sys.exit(1)

    # Check if user already exists
    if datastore.datastore.get_user(username):
        print(f"Error: User '{username}' already exists.", file=sys.stderr)
        sys.exit(1)

    try:
        password = getpass.getpass("Enter password for new user: ")
        if not password:
            print("Error: Password cannot be empty.", file=sys.stderr)
            sys.exit(1)

        password_hash = generate_password_hash(password)
        datastore.datastore.add_user(username, password_hash, created_by='system_script')
        print(f"Successfully created user '{username}'.")

    except Exception as e:
        print(f"An unexpected error occurred: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == '__main__':
    main()