# Bulk Certificate Generator API

A high-performance, asynchronous FastAPI backend designed to process bulk certificate generations securely. It accepts large batches of recipient information, handles PDF rendering in a single-threaded background event stream, logs atomic metrics to MySQL, and enforces strict boundary security policies.

## Key Architectural Principles
* **Atomic State Changes:** Claims pending background actions using conditional SQL updates (`WHERE status = 'pending'`) to lock out concurrency race conditions.
* **Isolated Transactions:** Processes each individual certificate rendering run inside its own independent database transactional context. One worker failure never corrupts or rolls back companion rows.
* **Secured Storage Boundaries:** Validates server path lookups defensively against relative parent directory traversal exploits (`../../etc/passwd`).

## Prerequisites
* macOS 14+ (Apple Silicon or Intel)
* Python 3.11+
* MySQL Server 8.0+

## Getting Started

1. **Clone and enter the workspace:**
   ```bash
   cd ~/projects/bulk-certificate-generator
   ```

2. **Initialize the Virtual Environment:**
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

3. **Configure Environment Variables:**
   Create a `.env` file from the provided example template:
   ```bash
   cp .env.example .env
   ```
   *Open `.env` and fill in your authorized `DB_USER` and `DB_PASSWORD` parameters.*

4. **Launch the Service Live Engine:**
   ```bash
   uvicorn app.main:app --reload
   ```

## Running the Automated Test Matrix
To spin up the isolated test suite running on top of temporary database sandbox fixtures, use the Pytest wrapper runner tool:
```bash
pytest -v
```
