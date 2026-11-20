import logging
import os
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from time import perf_counter

import requests
from sentence_transformers import SentenceTransformer

from .ranking import rank_decks
SOLR = os.getenv("SOLR_URL", "http://solr:8983/solr/decks")
MODEL_ID = "BAAI/bge-small-en-v1.5"
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "
POOL = ThreadPoolExecutor(max_workers=4)
logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_model():
    return SentenceTransformer(MODEL_ID)


@lru_cache(maxsize=1024)
def query_vector(query: str):
    return get_model().encode(QUERY_PREFIX + query, normalize_embeddings=True).tolist()


def solr_request(method, path, **kwargs):
    response = requests.request(method, f"{SOLR}/{path}", timeout=20, **kwargs)
    response.raise_for_status()
    return response.json()


def search_branch(query, mode):
    start = perf_counter()
    fields = "id,deck_id,slide_number,slide_title,content,score"
    params = {"fl": fields, "rows": 50}
    if mode == "keyword":
        params.update({"q": "{!edismax qf='deck_title^3 course^2 slide_title^2 content' uf='-' v=$student_query}",
                       "student_query": query})
    else:
        vector = query_vector(query)
        params["q"] = "{!knn f=embedding topK=50}" + str(vector)
    docs = solr_request("POST", "select", data=params)["response"]["docs"]
    logger.info("Search %s branch took %.1f ms", mode, (perf_counter() - start) * 1000)
    return docs


def search(query, mode="hybrid"):
    if mode == "hybrid":
        keyword = POOL.submit(search_branch, query, "keyword")
        vector = POOL.submit(search_branch, query, "vector")
        return rank_decks(keyword.result(), vector.result())
    docs = search_branch(query, mode)
    return rank_decks(docs, []) if mode == "keyword" else rank_decks([], docs)


FIELDS = {
    "deck_id": "string",
    "deck_title": "text_general",
    "course": "text_general",
    "slide_number": "pint",
    "slide_title": "text_general",
    "content": "text_general",
    "embedding": "deck_vector",
}


def init_schema():
    names = {entry["name"] for entry in solr_request("GET", "schema/fieldtypes")["fieldTypes"]}
    if "deck_vector" not in names:
        solr_request("POST", "schema", json={"add-field-type": {
            "name": "deck_vector", "class": "solr.DenseVectorField",
            "vectorDimension": 384, "similarityFunction": "cosine",
        }})
    existing = {field["name"] for field in solr_request("GET", "schema/fields")["fields"]}
    for name, kind in FIELDS.items():
        if name not in existing:
            solr_request("POST", "schema", json={"add-field": {
                "name": name, "type": kind, "indexed": True,
                "stored": name != "embedding", "multiValued": False,
            }})


if __name__ == "__main__":
    init_schema()
