"""Mobile eXperience AI Data Hub MCP server.

Cline SR(또는 Claude Desktop 등 MCP 호환 클라이언트)이 stdio 로 접속하여
사내 AI 데이터 허브의 REST API를 도구로 호출할 수 있게 해 준다.

서버 이름: `ai-data-hub`
환경변수:
    - API_URL  (default: http://localhost:8000)
    - API_TIMEOUT (default: 30 초)
"""
__version__ = "0.1.0"

__all__ = ["__version__"]
