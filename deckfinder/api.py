import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4
from zipfile import BadZipFile

from boto3.dynamodb.conditions import Key
from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.staticfiles import StaticFiles
from lxml.etree import XMLSyntaxError
from pptx.exc import PackageNotFoundError

from .auth import CLIENT, DOMAIN, current_user
from .search import get_model, search
from .settings import BUCKET, MAX_DECK_BYTES, SESSION, s3, table
from .slides import Draft, open_deck, render_pptx

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app):
    get_model()
    yield


app = FastAPI(title="DeckFinder", lifespan=lifespan)


def teacher(user=Depends(current_user)):
    if not user["teacher"]:
        raise HTTPException(403, "A teacher account is required.")
    return user


def get_record(record_id):
    return table.get_item(Key={"id": record_id}, ConsistentRead=True).get("Item")


def public_fields(item):
    return {field: item[field] for field in ("id", "title", "course", "status", "created_at")}


def now():
    return datetime.now(timezone.utc).isoformat()


@app.get("/api/config")
def config():
    return {"domain": DOMAIN, "client": CLIENT}


@app.get("/api/me")
def me(user=Depends(current_user)):
    return {"teacher": user["teacher"]}


@app.get("/api/library")
def library(user=Depends(teacher)):
    params = {"IndexName": "owner", "KeyConditionExpression": Key("owner").eq(user["id"])}
    items = []
    while True:
        page = table.query(**params)
        items.extend(map(public_fields, page["Items"]))
        if "LastEvaluatedKey" not in page:
            break
        params["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    return sorted(items, key=lambda row: row["created_at"], reverse=True)


def owned_draft(draft_id, user):
    item = get_record(draft_id)
    if not item or item["owner"] != user["id"] or item["status"] != "draft":
        raise HTTPException(404, "Draft unavailable.")
    return item


def save_draft(draft, owner, draft_id, created_at):
    data = draft.model_dump_json()
    if len(data.encode("utf-8")) > 340_000:
        raise HTTPException(413, "Draft too large. Use smaller slide images.")
    table.put_item(Item={
        "id": draft_id, "owner": owner, "status": "draft", "title": draft.title,
        "course": draft.course, "created_at": created_at, "draft": data,
    })
    return {"id": draft_id}


@app.post("/api/drafts", status_code=201)
def create_draft(draft: Draft, user=Depends(teacher)):
    return save_draft(draft, user["id"], uuid4().hex, now())


@app.get("/api/drafts/{draft_id}")
def load_draft(draft_id: str, user=Depends(teacher)):
    return json.loads(owned_draft(draft_id, user)["draft"])


@app.put("/api/drafts/{draft_id}")
def update_draft(draft_id: str, draft: Draft, user=Depends(teacher)):
    prior = owned_draft(draft_id, user)
    return save_draft(draft, user["id"], draft_id, prior["created_at"])


def publish(data, title, course, owner):
    deck_id = uuid4().hex
    item = {"id": deck_id, "owner": owner, "status": "processing", "title": title,
            "course": course, "created_at": now(), "key": f"published/{deck_id}.pptx"}
    table.put_item(Item=item)
    try:
        s3.put_object(Bucket=BUCKET, Key=item["key"], Body=data,
                      ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation")
    except Exception:
        # An object may still have arrived if the response was lost. The worker can recover it.
        table.update_item(
            Key={"id": deck_id}, UpdateExpression="SET #state = :failed",
            ConditionExpression="#state = :processing",
            ExpressionAttributeNames={"#state": "status"},
            ExpressionAttributeValues={":failed": "failed", ":processing": "processing"},
        )
        raise
    return public_fields(item)


@app.post("/api/upload", status_code=202)
def upload(file: UploadFile = File(), title: str = Form(min_length=1, max_length=160),
           course: str = Form(default="", max_length=100), user=Depends(teacher)):
    if not (file.filename or "").lower().endswith(".pptx"):
        raise HTTPException(422, "Choose a .pptx file.")
    data = file.file.read(MAX_DECK_BYTES + 1)
    if len(data) > MAX_DECK_BYTES:
        raise HTTPException(413, "Decks can be up to 25 MB.")
    try:
        open_deck(data)
    except (BadZipFile, PackageNotFoundError, XMLSyntaxError, ValueError, KeyError) as exc:
        raise HTTPException(422, "The PowerPoint could not be opened.") from exc
    return publish(data, title, course, user["id"])


@app.post("/api/drafts/{draft_id}/publish", status_code=202)
def publish_draft(draft_id: str, user=Depends(teacher)):
    draft = Draft.model_validate_json(owned_draft(draft_id, user)["draft"])
    try:
        data = render_pptx(draft)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return publish(data, draft.title, draft.course, user["id"])


def fetch_decks(matches):
    if not matches:
        return {}
    keys = [{"id": row["deck_id"]} for row in matches]
    dynamo = SESSION.resource("dynamodb")
    pending = {table.name: {"Keys": keys, "ConsistentRead": True}}
    records = {}
    for _ in range(4):
        if not pending:
            return records
        response = dynamo.batch_get_item(RequestItems=pending)
        records.update({row["id"]: row for row in response["Responses"].get(table.name, [])})
        pending = response.get("UnprocessedKeys", {})
    if pending:
        raise HTTPException(503, "The deck library is busy. Try again shortly.")
    return records


@app.get("/api/search")
def find_decks(q: str = Query(min_length=1, max_length=300),
               mode: str = Query(default="hybrid", pattern="^(hybrid|keyword|vector)$"),
               user=Depends(current_user)):
    matches = search(q.strip(), mode) if q.strip() else []
    records = fetch_decks(matches)
    ranked = []
    for match in matches:
        item = records.get(match["deck_id"])
        if item and item["status"] == "ready":
            ranked.append({**public_fields(item), "slide": match["slide_number"],
                           "slide_title": match.get("slide_title", ""),
                           "excerpt": match["content"][:420]})
    return ranked[:10]


@app.get("/api/decks/{deck_id}/download")
def download(deck_id: str, user=Depends(current_user)):
    item = get_record(deck_id)
    if not item or item["status"] != "ready":
        raise HTTPException(404, "Deck unavailable.")
    disposition = "attachment; filename*=UTF-8''" + quote(item["title"] + ".pptx", safe="")
    return {"url": s3.generate_presigned_url("get_object", Params={
        "Bucket": BUCKET, "Key": item["key"], "ResponseContentDisposition": disposition,
    }, ExpiresIn=180)}


app.mount("/", StaticFiles(directory=Path(__file__).parent.parent / "web", html=True))
