import hashlib
import json
import os

from pydantic import BaseModel
from pymilvus import MilvusClient

INDEX_DATA_PATH = "./.db"
INDEX_TRACKING_FILE = "index_tracking.json"
COLLECTION_NAME = "markdown_vectors"


class Entity(BaseModel):
    text: str
    filename: str
    path: str


class SearchResult(BaseModel):
    id: int
    distance: float
    entity: Entity


def load_tracking_file():
    try:
        with open(os.path.join(INDEX_DATA_PATH, INDEX_TRACKING_FILE), "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def save_tracking_file(tracking_data):
    with open(os.path.join(INDEX_DATA_PATH, INDEX_TRACKING_FILE), "w") as f:
        json.dump(tracking_data, f, indent=2)


def get_file_info(file_path):
    with open(file_path, "rb") as f:
        file_hash = hashlib.md5(f.read()).hexdigest()
    modified_time = os.path.getmtime(file_path)
    return file_hash, modified_time


def update_tracking_file(processed_files):
    tracking_data = load_tracking_file()
    for file_path in processed_files:
        try:
            tracking_data[file_path] = get_file_info(file_path)
        # Remove deleted files from tracking
        except (FileNotFoundError, PermissionError):
            tracking_data.pop(file_path, None)
    save_tracking_file(tracking_data)


def list_md_files(base_dir: str, recursive: bool = False) -> list[str]:
    md_files = []
    if recursive:
        for root, dirs, files in os.walk(base_dir):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for file in files:
                if file.endswith(".md"):
                    md_files.append(os.path.join(root, file))
    else:
        for file in os.listdir(base_dir):
            if file.endswith(".md"):
                md_files.append(os.path.join(base_dir, file))
    return md_files


def get_changed_files(
    directory: str,
    recursive: bool = False,
) -> list[str]:
    """
    Get list of changed file's paths in the directory
    """
    tracking_data = load_tracking_file()
    changed_files = []
    md_files = list_md_files(directory, recursive)
    for file_path in md_files:
        if file_path not in tracking_data:
            changed_files.append(file_path)
            continue
        try:
            current_hash, current_modified_time = get_file_info(file_path)
        except (FileNotFoundError, PermissionError):
            continue
        stored_hash, stored_time = tracking_data[file_path]
        if current_hash != stored_hash or current_modified_time != stored_time:
            changed_files.append(file_path)
    return changed_files


def ensure_collection(milvus_client: MilvusClient):
    if not milvus_client.has_collection(COLLECTION_NAME):
        milvus_client.create_collection(COLLECTION_NAME, dimension=768, auto_id=True)
