import os

from llama_index.core import SimpleDirectoryReader
from llama_index.core.node_parser import MarkdownNodeParser
from llama_index.core.text_splitter import TokenTextSplitter
from mcp.server.fastmcp import FastMCP
from pymilvus import MilvusClient, model

from utils import (
    COLLECTION_NAME,
    Entity,
    INDEX_DATA_PATH,
    SearchResult,
    ensure_collection,
    get_changed_files,
    list_md_files,
    update_tracking_file,
)

mcp = FastMCP("mcp-markdown-rag")


if not os.path.exists(INDEX_DATA_PATH):
    os.makedirs(INDEX_DATA_PATH)
milvus_client = MilvusClient(os.path.join(INDEX_DATA_PATH, "milvus_markdown.db"))
embedding_fn = model.DefaultEmbeddingFunction()


def search(query: str, k: int) -> list[list[SearchResult]]:
    query_vectors = embedding_fn.encode_queries([query])
    res = milvus_client.search(
        collection_name=COLLECTION_NAME,
        data=query_vectors,
        limit=k,
        output_fields=list(Entity.model_fields.keys()),
    )
    return res


@mcp.tool()
async def index_documents(
    current_working_directory: str,
    directory: str = "",
    recursive: bool = False,
    force_reindex: bool = False,
):
    """
    Index Markdown files for semantic search using Milvus.

    Reads `.md` files from a directory (joined with current working directory),
    splits them by heading and size, embeds them into 768D vectors,
    and stores them with metadata in Milvus. Supports full and incremental indexing.

    Args:
        current_working_directory (str): Base directory used to resolve the full path to the Markdown folder.
        directory (str, optional): Optional relative path from the current working directory to the folder containing Markdown files.
        recursive (bool, optional): Set to True to index all subdirectories.
        force_reindex (bool, optional): Set to True to clear and rebuild the entire index, ignoring any cached data.

    Returns:
        dict: Summary with file count, chunk count, and indexing status.

    Uses change-based detection to skip unchanged files.
    """
    target_path = os.path.join(current_working_directory, directory)

    if not os.path.exists(target_path):
        return {"message": "Directory does not exist!"}

    if force_reindex:
        if milvus_client.has_collection(COLLECTION_NAME):
            milvus_client.drop_collection(COLLECTION_NAME)
        ensure_collection(milvus_client)

        all_files = list_md_files(target_path, recursive=recursive)
        documents = SimpleDirectoryReader(
            input_files=all_files, required_exts=[".md"]
        ).load_data()
        processed_files = [doc.metadata["file_path"] for doc in documents]

    else:
        changed_files = get_changed_files(target_path, recursive=recursive)

        if not changed_files:
            return {"message": "Already up to date, Nothing to index!"}
        # If not collection exists create a new one
        ensure_collection(milvus_client)
        # Needs to delete the old chunks related to changed files
        for file_path in changed_files:
            try:
                milvus_client.delete(
                    collection_name=COLLECTION_NAME, filter=f"path == '{file_path}'"
                )
            except Exception as e:
                print(e)

        # Load only changed files to index
        documents = SimpleDirectoryReader(
            input_files=changed_files, required_exts=[".md"]
        ).load_data()
        # Update tracking file
        processed_files = changed_files

    # Convert to nodes based on markdown structure, then split larger nodes into chunks
    nodes = MarkdownNodeParser().get_nodes_from_documents(documents)
    chunked_nodes = TokenTextSplitter(
        chunk_size=512, chunk_overlap=100
    ).get_nodes_from_documents(nodes)
    chunked_nodes = [node for node in chunked_nodes if node.text.strip()]

    # Extract text from nodes and embed
    texts = [node.text for node in chunked_nodes]
    vectors = embedding_fn.encode_documents(texts)
    data = [
        {
            "vector": vector,
            "text": node.text,
            "filename": node.metadata["file_name"],
            "path": node.metadata["file_path"],
        }
        for vector, node in zip(vectors, chunked_nodes)
    ]
    res = milvus_client.insert(collection_name=COLLECTION_NAME, data=data)

    # Update tracking file
    update_tracking_file(processed_files)

    return {
        **res,
        "message": "Full reindex" if force_reindex else "Incremental update",
        "processed_files": len(processed_files),
        "total_chunks": len(chunked_nodes),
        "files": [os.path.basename(f) for f in processed_files],
    }


@mcp.tool()
async def search_documents(query: str, k: int = 5):
    """
    Search for semantically relevant documents based on query

    Args:
        query (str): Query to search for
        k (int, optional): Number of documents to return. Defaults to 5.

    Returns:
        list: List of documents similar to query
    """
    results = search(query, k=k)

    return "\n---\n".join(
        [
            f"File: **{res.entity.filename}**\n---\nText: {res.entity.text}\n---\n"
            for res in results[0]
        ]
    )  # Iterate through the relevent docs and append the text


if __name__ == "__main__":
    mcp.run(transport="stdio")
