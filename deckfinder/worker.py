import json
import logging
import os
from time import perf_counter, sleep
from urllib.parse import unquote_plus

from botocore.exceptions import ClientError

from .search import get_model, solr_request
from .settings import BUCKET, MAX_DECK_BYTES, SESSION, s3, table
from .slides import slide_chunks

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def status(deck_id, value):
    try:
        table.update_item(
            Key={"id": deck_id}, UpdateExpression="SET #state = :value",
            ConditionExpression="attribute_exists(id) AND #state <> :ready",
            ExpressionAttributeNames={"#state": "status"},
            ExpressionAttributeValues={":value": value, ":ready": "ready"},
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise


def process(event, attempts):
    bucket = event["s3"]["bucket"]["name"]
    key = unquote_plus(event["s3"]["object"]["key"])
    if bucket != BUCKET or not key.startswith("published/") or not key.endswith(".pptx"):
        return
    deck_id = key.removeprefix("published/").removesuffix(".pptx")
    item = table.get_item(Key={"id": deck_id}, ConsistentRead=True).get("Item")
    if not item or item.get("key") != key or item["status"] == "ready":
        return
    start = perf_counter()
    try:
        response = s3.get_object(Bucket=bucket, Key=key)
        with response["Body"] as body:
            data = body.read(MAX_DECK_BYTES + 1)
        if len(data) > MAX_DECK_BYTES:
            raise ValueError("Presentation exceeds the supported file size.")
        chunks = list(slide_chunks(data))
        if not chunks:
            chunks = [(1, 0, "", item["title"])]
        for offset in range(0, len(chunks), 32):
            batch = chunks[offset:offset + 32]
            passages = [f"{item['title']}\\n{item['course']}\\n{title}\\n{text}"
                        for _, _, title, text in batch]
            vectors = get_model().encode(passages, batch_size=32, normalize_embeddings=True)
            documents = [{
                "id": f"{deck_id}:{slide}:{index}", "deck_id": deck_id,
                "deck_title": item["title"], "course": item["course"], "slide_number": slide,
                "slide_title": title, "content": text, "embedding": vector.tolist(),
            } for (slide, index, title, text), vector in zip(batch, vectors)]
            solr_request("POST", "update", json=documents)
        solr_request("POST", "update", json={"commit": {"softCommit": False, "waitSearcher": True}})
        status(deck_id, "ready")
        logger.info("Indexed deck %s in %.1f ms", deck_id, (perf_counter() - start) * 1000)
    except Exception:
        if attempts >= 4:
            status(deck_id, "failed")
        raise


def run():
    queue_url = os.environ["INDEX_QUEUE_URL"]
    sqs = SESSION.client("sqs")
    get_model()
    while True:
        try:
            result = sqs.receive_message(QueueUrl=queue_url, WaitTimeSeconds=20,
                                         MessageSystemAttributeNames=["ApproximateReceiveCount"])
            for message in result.get("Messages", []):
                try:
                    attempts = int(message["Attributes"]["ApproximateReceiveCount"])
                    for event in json.loads(message["Body"]).get("Records", []):
                        process(event, attempts)
                    sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=message["ReceiptHandle"])
                except Exception:
                    logger.exception("Indexing failed for message %s", message["MessageId"])
        except ClientError:
            logger.exception("Could not poll the indexing queue")
            sleep(5)


if __name__ == "__main__":
    run()
