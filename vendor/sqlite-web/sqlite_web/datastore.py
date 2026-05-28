"""
Manages shared application state using Google Firestore.

This module abstracts the storage of the database list, allowing multiple
stateless instances of the application to share the same configuration.
"""
import os

try:
    from google.cloud import firestore
except ImportError:
    firestore = None


class FirestoreDatastore:
    """A state manager that uses Firestore as a backend."""

    def __init__(self):
        if not firestore:
            raise ImportError(
                "The 'google-cloud-firestore' library is required to use Firestore for"
                " state management. Please install it."
            )

        # The project ID will be inferred from the environment by the client library.
        self.db = firestore.Client()
        # Use a specific collection for this app's state.
        db_collection_name = os.environ.get("FIRESTORE_DB_COLLECTION", "sqlite-web-databases")
        self.db_collection = self.db.collection(db_collection_name)

        config_collection_name = os.environ.get("FIRESTORE_CONFIG_COLLECTION", "sqlite-web-config")
        self.config_collection = self.db.collection(config_collection_name)
        self.config_doc_ref = self.config_collection.document("settings")

    def get_all_datasets(self):
        """Retrieves all dataset configurations from Firestore."""
        docs = self.db_collection.stream()
        # The document ID is the dataset name, and it contains a 'path' field.
        return {doc.id: doc.to_dict().get("path") for doc in docs}

    def add_dataset(self, name, path):
        """Adds or updates a dataset configuration in Firestore."""
        self.db_collection.document(name).set({"path": path})

    def remove_dataset(self, name):
        """Removes a dataset configuration from Firestore."""
        self.db_collection.document(name).delete()

    def get_config(self):
        """Retrieves the application configuration document from Firestore."""
        doc = self.config_doc_ref.get()
        if doc.exists:
            return doc.to_dict()
        return None

    def save_config(self, config_dict):
        """Saves the application configuration to Firestore."""
        self.config_doc_ref.set(config_dict)


# Singleton instance to be used by the application.
datastore = FirestoreDatastore() if firestore else None