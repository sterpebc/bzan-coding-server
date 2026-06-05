"""
A command-line script to seed the Firestore database with fake API data.

This script reads data from a JSON file and populates Firestore collections
under a specified domain.

Usage:
    python seed_api_data.py <domain_name> <path_to_json_file>
"""
import argparse
import os
import sys
import json
from datetime import datetime

try:
    # It's important to set the project before importing the datastore
    # if it's not already set in the environment.
    if 'GOOGLE_CLOUD_PROJECT' not in os.environ:
        # Replace with your actual project ID if needed
        os.environ['GOOGLE_CLOUD_PROJECT'] = 'bzan-coding-server'

    from datastore import firestore
    if not firestore:
        raise ImportError("Firestore client could not be initialized.")

except ImportError as e:
    print(f"Error importing necessary libraries: {e}", file=sys.stderr)
    print("Please ensure you have run 'pip install -r requirements.txt'", file=sys.stderr)
    sys.exit(1)

def json_parser_hook(dct):
    """
    Custom JSON parser to convert ISO 8601 date strings into datetime objects.
    """
    for k, v in dct.items():
        if isinstance(v, str):
            try:
                # The 'Z' suffix indicates UTC. The fromisoformat method in Python 3.7+
                # can parse this directly into an aware datetime object.
                if v.endswith('Z'):
                    dct[k] = datetime.fromisoformat(v[:-1] + '+00:00')
            except (ValueError, TypeError):
                pass
    return dct

def seed_collection(db, domain, collection_name, data, id_field='id'):
    """
    Seeds a Firestore collection with a list of dictionaries.
    Uses a specific field from the dictionary as the document ID.
    """
    print(f"Seeding collection: {domain}/{collection_name}...")
    batch = db.batch()
    # Construct the path to the subcollection
    collection_path = f"api_domains/{domain}/{collection_name}"
    collection_ref = db.collection(collection_path)
    count = 0

    # Check if collection is empty
    if next(collection_ref.limit(1).stream(None), None):
        print(f"Collection '{collection_name}' is not empty. Skipping seeding.")
        return

    for item in data:
        # Use the item's 'id' field as the document ID, converted to a string.
        # This makes API paths predictable (e.g., /api/products/1).
        if id_field in item:
            doc_id = str(item[id_field])
            doc_ref = collection_ref.document(doc_id)
        else:
            # For collections like 'inventory', let Firestore auto-generate IDs.
            doc_ref = collection_ref.document()

        batch.set(doc_ref, item)
        count += 1
        # Firestore batches have a limit of 500 operations.
        if count % 499 == 0:
            batch.commit()
            batch = db.batch()

    if count % 499 != 0:
        batch.commit()

    print(f"Successfully seeded {count} documents into '{collection_name}'.")


def main():
    """Seeds the Firestore database with all fake data collections."""
    parser = argparse.ArgumentParser(description="Seed Firestore with data from a JSON file.")
    parser.add_argument("domain", help="The domain name to nest the collections under (e.g., 'supply_chain').")
    parser.add_argument("json_file", help="Path to the JSON file containing the data to seed.")
    args = parser.parse_args()

    domain_name = args.domain
    json_file_path = args.json_file

    try:
        with open(json_file_path, 'r') as f:
            all_data = json.load(f, object_hook=json_parser_hook)
    except FileNotFoundError:
        print(f"Error: The file '{json_file_path}' was not found.", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Error: Failed to parse JSON file '{json_file_path}': {e}", file=sys.stderr)
        sys.exit(1)

    db = firestore.Client()
    print("Starting API data seeding process...")
    try:
        for collection_name, data in all_data.items():
            id_field = 'id' if collection_name != 'inventory' else None
            seed_collection(db, domain_name, collection_name, data, id_field=id_field)
        print("\nAPI data seeding complete!")
        print(f"You can now query endpoints under the /api/{domain_name}/ domain.")
    except Exception as e:
        print(f"\nAn error occurred during seeding: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
