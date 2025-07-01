from pydantic import BaseModel

LOCAL_MILVUS_PATH = "./milvus"
COLLECTION_NAME = "markdown_vectors"


class Document(BaseModel):
    filename: str
    preview: str
    content: str
    path: str


class Entity(BaseModel):
    text: str
    filename: str
    path: str


class SearchResult(BaseModel):
    id: int
    distance: float
    entity: Entity
