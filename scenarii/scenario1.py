import json
import requests
import time
import random
import subprocess

index_id = "test_index"
commit_timeout = 5


def create_indexes(url: str):
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


# def symbolize(binary_path: str, addresses: list[str]):
#     process = subprocess.Popen(
#         [binary_path, "tool", "symbolize", "--addr", ",".join(addresses)],
#         stdout=subprocess.PIPE,
#         stderr=subprocess.PIPE,
#         text=True,
#         cwd=".",
#     )
#     output, error = process.communicate()
#     if process.returncode != 0:
#         print(f"Error symbolizing addresses {addresses}:")
#         print(error)
#     else:
#         print(output)


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


def ingest_documents(url: str, log_results: LogResults):
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
        response = connection.post(f"{url}/api/v1/{index_id}/ingest", data=ndjson)
        if response.status_code == 200:
            nb_batches_remaining -= 1
        elif response.status_code != 429 and response.status_code != 503:
            print(f"Failed to ingest documents: {response.status_code} {response.text}")
            break
    print(f"Ingested {len(documents)*batches_to_ingest} documents")


def search(url: str, id: int):
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
    print(f"Search successful in {time.time() - start} seconds")
    time.sleep(1)


def display_statistics():
    return
