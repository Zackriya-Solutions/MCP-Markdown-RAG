from mcp.server.fastmcp import FastMCP

mcp = FastMCP("mc-markdown-rag")


@mcp.tool()
def echo(text: str) -> str:
    return text

if __name__ == "__main__":
    mcp.run()
