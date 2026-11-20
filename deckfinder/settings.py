import os

import boto3
from botocore.config import Config

REGION = os.environ["AWS_REGION"]
BUCKET = os.environ["DECK_BUCKET"]
TABLE_NAME = os.environ["DECK_TABLE"]
SESSION = boto3.Session(region_name=REGION)
s3 = SESSION.client("s3", config=Config(signature_version="s3v4"))
table = SESSION.resource("dynamodb").Table(TABLE_NAME)
MAX_DECK_BYTES = 25 * 1024 * 1024
