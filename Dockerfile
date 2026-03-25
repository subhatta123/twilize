# ---- Stage 1: Build the frontend ----
FROM node:20-slim AS frontend-builder

WORKDIR /app/extension/frontend
COPY extension/frontend/package.json extension/frontend/package-lock.json* ./
RUN npm install
COPY extension/frontend/ ./
RUN npm run build

# ---- Stage 2: Python runtime ----
FROM python:3.13-slim

WORKDIR /app

# Install Python dependencies
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy the full project
COPY . .

# Install the twilize package in editable mode
RUN pip install --no-cache-dir -e .

# Copy the built frontend from stage 1
COPY --from=frontend-builder /app/extension/frontend/dist /app/extension/frontend/dist

# Railway sets $PORT; default to 8000
ENV PORT=8000
EXPOSE ${PORT}

CMD python -m uvicorn extension.backend.app:app --host 0.0.0.0 --port ${PORT}
