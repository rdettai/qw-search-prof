import json
import requests
import time
import random
import concurrent.futures
import threading

index_ids = [f"test_index_{i}" for i in range(10)]
commit_timeout = 5

STATS_LOCK = threading.Lock()
TOTAL_INDEXING_DURATION = 0
TOTAL_SEARCH_DURATION = 0
TOTAL_SEARCH_COUNT = 0


def create_index(url: str, index_id: str):
    print(f"Creating index {index_id}...")
    config = f"""
version: 0.8
index_id: {index_id}

doc_mapping:
  mode: dynamic
  field_mappings:
    - name: timestamp
      type: datetime
      input_formats:
        - unix_timestamp
      output_format: unix_timestamp_secs
      fast: true
  timestamp_field: timestamp

indexing_settings:
  commit_timeout_secs: {commit_timeout}
  merge_policy:
    type: "no_merge"
"""
    response = requests.post(
        f"{url}/api/v1/indexes",
        data=config,
        headers={"Content-Type": "application/yaml"},
    )
    if response.status_code != 200:
        raise Exception(
            f"Failed to create index {index_id} {response.status_code}: {response.text}"
        )
    print(f"Index {index_id} created")


def create_indexes(url: str):
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        for index_id in index_ids:
            executor.submit(create_index, url, index_id)


class LogResults:
    def __init__(self, binary_path: str):
        self.start_time = time.time()
        self.log_file = None
        self.binary_path = binary_path

    def on_new_log_line(self, line: str):
        line = line.strip()
        if line.startswith("htrk"):
            print(line)
        elif "heap profiling stopped" in line:
            print(line)
        elif "heap profiling running" in line:
            print(line)

    def print(self):
        if self.log_file is not None:
            print(f"Logs written to {self.log_file}")


def ingest_documents_for_index(url: str, index_id: str):
    global STATS_LOCK, TOTAL_INDEXING_DURATION
    documents = [
        {
            "timestamp": int(time.time()),
            "id": str(i),
            "content": "This is a test document.",
            "float": random.uniform(0, 1),
        }
        for i in range(10000)
    ]
    ndjson = "\n".join(json.dumps(doc) for doc in documents)
    batches_to_ingest = 100
    nb_batches_remaining = batches_to_ingest
    connection = requests.Session()
    while nb_batches_remaining > 0:
        start = time.time()
        response = connection.post(f"{url}/api/v1/{index_id}/ingest", data=ndjson)
        with STATS_LOCK:
            TOTAL_INDEXING_DURATION += time.time() - start
        if response.status_code == 200:
            nb_batches_remaining -= 1
        elif response.status_code != 429 and response.status_code != 503:
            print(f"Failed to ingest documents: {response.status_code} {response.text}")
            break
    print(f"Ingested {len(documents)*batches_to_ingest} documents")


def ingest_documents(url: str, log_results: LogResults):
    for index_id in index_ids:
        ingest_documents_for_index(url, index_id)


def search_for_index(url: str, index_id, id: int):
    global STATS_LOCK, TOTAL_SEARCH_DURATION, TOTAL_SEARCH_COUNT
    print(f"Search index {index_id}...")
    start = time.time()
    query = {
        "query": f"id:{id}",
        "max_hits": 1,
        "aggs": {
            "float_agg": {
                "avg": {"field": "float"},
            }
        },
    }

    response = requests.post(
        f"{url}/api/v1/{index_id}/search",
        json=query,
    )
    if response.status_code != 200:
        raise Exception(
            f"Failed to create index {index_id} {response.status_code}: {response.text}"
        )
    with STATS_LOCK:
        TOTAL_SEARCH_COUNT += 1
        TOTAL_SEARCH_DURATION += time.time() - start
    print(f"Search successful in {time.time() - start} seconds")


def search(url: str, id: int):
    for index_id in index_ids:
        search_for_index(url, index_id, id)


def display_statistics():
    global TOTAL_INDEXING_DURATION, TOTAL_SEARCH_DURATION, TOTAL_SEARCH_COUNT
    print("indexing duration:", TOTAL_INDEXING_DURATION)
    print(
        "avg search duration:",
        TOTAL_SEARCH_DURATION / TOTAL_SEARCH_COUNT,
    )
