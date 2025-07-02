from mcp.server.fastmcp import FastMCP
from llama_index.core import SimpleDirectoryReader
from typing import Optional
from llama_index.core.node_parser import MarkdownNodeParser
from llama_index.core.text_splitter import TokenTextSplitter
from pymilvus import MilvusClient, model
from utils import Entity, SearchResult, LOCAL_MILVUS_PATH, COLLECTION_NAME
import os


mcp = FastMCP("mcp-markdown-rag")


if not os.path.exists(LOCAL_MILVUS_PATH):
    os.makedirs(LOCAL_MILVUS_PATH)
milvus_client = MilvusClient(LOCAL_MILVUS_PATH + "/milvus_markdown.db")
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
async def index_documents(directory: Optional[str] = None):
    """
    Index documents for semantic search

    Args:
        directory (str, optional): Directory containing markdown files to index.
            Defaults to current directory if not provided.

    Processing:
    - Creates Milvus collection with 768 dimensions
    - Reads and processes markdown files
    - Splits content by headings and chunks large content
    - Converts to vector embeddings
    - Stores vectors with metadata

    Note: Automatically handles document chunking and embedding
    """
    if milvus_client.has_collection(COLLECTION_NAME):
        milvus_client.drop_collection(COLLECTION_NAME)
    milvus_client.create_collection(COLLECTION_NAME, dimension=768, auto_id=True)

    # Read and process markdown files
    documents = SimpleDirectoryReader(
        directory or "./", required_exts=[".md"]
    ).load_data()

    # Convert to nodes based on markdown structure, then split larger nodes into chunks
    nodes = MarkdownNodeParser().get_nodes_from_documents(documents)
    chunked_nodes = TokenTextSplitter(
        chunk_size=512, chunk_overlap=100
    ).get_nodes_from_documents(nodes)

    # Extract text from nodes and embed
    docs = [node.text for node in chunked_nodes]
    vectors = embedding_fn.encode_documents(docs)
    data = [
        {
            "vector": vectors[i],
            "text": docs[i],
            "filename": chunked_nodes[i].metadata["file_name"],
            "path": chunked_nodes[i].metadata["file_path"],
        }
        for i in range(len(vectors))
    ]
    res = milvus_client.insert(collection_name=COLLECTION_NAME, data=data)
    return {**res, "added_files": [doc.metadata["file_name"] for doc in documents]}


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
