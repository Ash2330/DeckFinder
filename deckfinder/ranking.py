from collections import defaultdict

MIN_COSINE = 0.55


def rank_decks(lexical, semantic):
    scores = defaultdict(float)
    documents = {}
    keyword_decks = {doc["deck_id"] for doc in lexical}
    relevant_decks = keyword_decks | {
        doc["deck_id"] for doc in semantic if 2 * doc["score"] - 1 >= MIN_COSINE
    }
    for results in (lexical, semantic):
        for rank, document in enumerate(results, 1):
            chunk_id = document["id"]
            scores[chunk_id] += 1 / (60 + rank)
            documents[chunk_id] = document

    decks = defaultdict(list)
    for chunk_id in sorted(scores, key=scores.get, reverse=True):
        document = documents[chunk_id]
        if document["deck_id"] in relevant_decks:
            decks[document["deck_id"]].append((scores[chunk_id], document))

    def deck_score(chunks):
        return chunks[0][0] + (0.3 * chunks[1][0] if len(chunks) > 1 else 0)

    return [chunks[0][1] for chunks in sorted(decks.values(), key=deck_score, reverse=True)]
