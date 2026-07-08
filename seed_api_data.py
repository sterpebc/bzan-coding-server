"""
A command-line script to seed the local datastore with fake API data.

This script reads data from a JSON file and populates "API domain"
collections under a specified domain, which api.py then serves as JSON
to students.

Usage:
    python seed_api_data.py <domain_name> <path_to_json_file>
"""
import argparse
import json
import sys

try:
    from datastore import datastore
except ImportError as e:
    print(f"Error importing necessary libraries: {e}", file=sys.stderr)
    print("Please ensure you have run 'pip install -r requirements.txt'", file=sys.stderr)
    sys.exit(1)


def seed_collection(domain, collection_name, data, id_field='id'):
    """
    Seeds a local "API domain" collection with a list of dictionaries.
    Uses a specific field from each dictionary as the document ID.
    """
    print(f"Seeding collection: {domain}/{collection_name}...")

    if not datastore.collection_is_empty(domain, collection_name):
        print(f"Collection '{collection_name}' is not empty. Skipping seeding.")
        return

    count = datastore.bulk_add_api_documents(domain, collection_name, data, id_field=id_field)
    print(f"Successfully seeded {count} documents into '{collection_name}'.")


def main():
    """Seeds the local datastore with all fake data collections from a JSON file."""
    parser = argparse.ArgumentParser(description="Seed the local datastore with data from a JSON file.")
    parser.add_argument("domain", help="The domain name to nest the collections under (e.g., 'supply_chain').")
    parser.add_argument("json_file", help="Path to the JSON file containing the data to seed.")
    args = parser.parse_args()

    domain_name = args.domain
    json_file_path = args.json_file

    try:
        with open(json_file_path, 'r') as f:
            all_data = json.load(f)
    except FileNotFoundError:
        print(f"Error: The file '{json_file_path}' was not found.", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Error: Failed to parse JSON file '{json_file_path}': {e}", file=sys.stderr)
        sys.exit(1)

    if not datastore:
        print("Error: the local datastore could not be initialized.", file=sys.stderr)
        sys.exit(1)

    print("Starting API data seeding process...")
    try:
        for collection_name, data in all_data.items():
            id_field = 'id' if collection_name != 'inventory' else None
            seed_collection(domain_name, collection_name, data, id_field=id_field)
        print("\nAPI data seeding complete!")
        print(f"You can now query endpoints under the /api/{domain_name}/ domain.")
    except Exception as e:
        print(f"\nAn error occurred during seeding: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
