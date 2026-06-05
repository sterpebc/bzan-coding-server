"""
A simple, GET-only JSON API for students, backed by Firestore.

This API is designed to be extensible. New endpoints can be added by simply
creating new collections in Firestore. The routes are dynamic and will serve
data from any collection requested.
"""
import os
from flask import Flask, jsonify, request

try:
    # Import the shared datastore instance
    from datastore import datastore
except (ImportError, ModuleNotFoundError):
    datastore = None

app = Flask(__name__)


@app.route('/<domain>/<collection>', methods=['GET'], strict_slashes=False)
def get_collection(domain, collection):
    """
    Returns a list of all items in a collection, with optional filtering.
    Example: /api/supply_chain/products?unitPrice=199.99
    """
    if not datastore:
        return jsonify({"error": "Datastore not configured"}), 500

    filters = request.args.to_dict()
    results = datastore.query_api_collection(domain, collection, **filters)
    return jsonify(results)


@app.route('/<domain>/<collection>/<doc_id>', methods=['GET'], strict_slashes=False)
def get_document(domain, collection, doc_id):
    """
    Returns a single item by its ID from a collection.
    Example: /api/supply_chain/products/1
    """
    if not datastore:
        return jsonify({"error": "Datastore not configured"}), 500

    document = datastore.get_api_document(domain, collection, doc_id)
    if document:
        return jsonify(document)
    else:
        return jsonify({"error": "Document not found"}), 404