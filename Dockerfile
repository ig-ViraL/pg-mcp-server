FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY pg_mcp_server/ ./pg_mcp_server/
RUN pip install --no-cache-dir ".[mysql,sqlite]"
EXPOSE 8000
ENV PGMCP_TRANSPORT=http HOST=0.0.0.0
CMD ["pg-mcp-server", "--transport", "http", "--host", "0.0.0.0"]
