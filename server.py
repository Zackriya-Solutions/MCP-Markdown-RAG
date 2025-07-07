import os

from fastmcp import FastMCP
from llama_index.core import SimpleDirectoryReader
from llama_index.core.node_parser import MarkdownNodeParser
from llama_index.core.text_splitter import TokenTextSplitter
from pydantic import Field
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

mcp = FastMCP(
    "mcp-markdown-rag",
    instructions="""This MCP server provides semantic search capabilities over markdown files using 
    vector embeddings and Milvus database. It enables you to index markdown documents and perform 
    intelligent searches to find relevant content based on semantic similarity rather than just 
    keyword matching.""",
)


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


@mcp.tool(
    name="index_documents",
    description="Index Markdown files for semantic search using Milvus.",
    tags={"index", "vectorize", "store"},
)
async def index_documents(
    current_working_directory: str = Field(description="Current working directory"),
    directory: str = Field("", description="Directory to index"),
    recursive: bool = Field(False, description="Recursively index subdirectories"),
    force_reindex: bool = Field(False, description="Force reindex"),
):
    # TODO: Implement Client Elicitation when it is available in Popular clients
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
            except Exception:
                continue

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


@mcp.tool(
    name="search_documents",
    description="Search for semantically relevant documents based on query",
    tags={"search", "query"},
)
async def search_documents(
    query: str = Field(description="Query to search for"),
    k: int = Field(5, description="Number of documents to return"),
):
    results = search(query, k=k)

    return "\n---\n".join(
        [
            f"File: **{res.entity.filename}**\n---\nText: {res.entity.text}\n---\n"
            for res in results[0]
        ]
    )  # Iterate through the relevent docs and append the text


@mcp.tool(
    name="clear_index",
    description="Clear the vector database's collection and reset the tracking file",
    tags={"clear", "reset"},
)
async def clear_index():
    ensure_collection(milvus_client)
    res = milvus_client.delete(collection_name=COLLECTION_NAME, filter="id >= 0")
    update_tracking_file([], is_clear=True)
    return res


if __name__ == "__main__":
    mcp.run(transport="stdio")
