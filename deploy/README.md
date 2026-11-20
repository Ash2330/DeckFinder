# Run DeckFinder on AWS

Deploy the CloudFormation stack in a single AWS region. It creates the private S3 bucket, SQS indexing queue and dead-letter queue, DynamoDB table, Cognito user pool and client, and EC2 instance profile.

```sh
aws cloudformation deploy --stack-name deckfinder --template-file deploy/aws.yaml \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides DeckBucketName=YOUR_UNIQUE_BUCKET \
    CognitoDomainPrefix=YOUR_UNIQUE_PREFIX SiteUrl=https://decks.example.edu/
aws cloudformation describe-stacks --stack-name deckfinder --query 'Stacks[0].Outputs'
```

1. Launch an EC2 Linux host with Docker and the Docker Compose plugin, using the stack's **InstanceProfile**. Start with at least 8 GiB RAM for the two embedding processes and Solr. Require IMDSv2 and set its response hop limit to 2 so bridge-networked containers can retrieve the role credentials.
2. Point the domain used in `SiteUrl` at the host. Allow public inbound 80/443 for Caddy and restrict administrative access. The Compose stack keeps FastAPI and Apache Solr on its internal network.
3. Copy this repository to the host. Copy `.env.example` to `.env` and fill in the stack outputs. Set `DECKFINDER_HOST` to the hostname without a scheme or trailing slash. The build downloads the BGE weights once and packages them into the application image.
4. In Cognito, create test accounts and add teacher accounts to the `teachers` group. Students can be placed in the `students` group. This version treats published decks as shared across authenticated accounts in this pool.
5. From the repository directory, run the following.

```sh
docker compose build api
docker compose up -d
docker compose ps -a
docker compose logs -f api worker
```

The one-shot `schema` service exits successfully after it installs the Solr field definitions. Open the configured HTTPS site, sign in as a teacher, upload a deck, and refresh **Your decks** until it becomes ready. Then sign in as a student, search for text in a slide, and download the presentation. Also publish a deck from the slide editor to check that both routes converge.

An indexing message can be delivered more than once. Its Solr document IDs are deterministic and the worker leaves ready decks alone. Failed messages move to the dead-letter queue after four receives. Review worker logs and the dead-letter queue to diagnose a failed upload. Solr stores its index in the Docker volume `solr-data`. Keep that volume with the EC2 data and snapshot it as needed.

To compare indexing throughput with two workers on a host with spare CPU and memory, run `docker compose up -d --scale worker=2` and repeat the bulk demo. Each worker polls the same queue and loads its own copy of the embedding model. The API and worker embedding thread limits can be set with `API_EMBEDDING_THREADS` and `WORKER_EMBEDDING_THREADS`.

## Cloud demo

Put your lecture PowerPoints beside a JSON manifest with the following shape. List approximately 500 decks for the proposed demo. File names in the manifest need to be unique.

```json
{
  "decks": [
    {"file": "queues.pptx", "title": "Message Queues", "course": "Cloud Computing"}
  ],
  "queries": [
    {"q": "why move long-running jobs out of a web request?", "expected_file": "queues.pptx"}
  ]
}
```

Run `python scripts/demo.py https://decks.example.edu path/to/manifest.json` in an environment with `requests` installed. Set `DECKFINDER_TEACHER_TOKEN` and `DECKFINDER_STUDENT_TOKEN` locally to current Cognito access tokens from the corresponding browser sessions. The script submits uploads, polls indexing status, measures p95 student search latency during processing, compares top-five hit rate across retrieval modes, and checks an S3 download. Indexing timings include polling delay. Run against a dedicated demo stack so previous uploads do not affect relevance results.

The CloudFormation stack retains the S3 bucket and DynamoDB table on deletion. The EC2 instance and its volumes are managed separately.
