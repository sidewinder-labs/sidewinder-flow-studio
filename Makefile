# Flow Studio — convenience commands
.PHONY: help docker docker-single docker-down clean

help:
	@echo ""
	@echo "  Flow Studio"
	@echo ""
	@echo "  Docker (two containers — nginx + FastAPI):"
	@echo "    make docker        Build and start"
	@echo "    make docker-down   Stop"
	@echo ""
	@echo "  Docker (single container — FastAPI serves everything):"
	@echo "    make docker-single Build and start on :8000"
	@echo ""
	@echo "  Utilities:"
	@echo "    make gen-key       Generate a SECRET_KEY"
	@echo "    make clean         Remove build artifacts"
	@echo ""

# ── Docker two-container ──────────────────────────────────────────────────────

docker:
	@test -f .env || (cp .env.docker .env && echo "Created .env from .env.docker — edit SECRET_KEY before continuing" && exit 1)
	docker compose up --build -d
	@echo ""
	@echo "  Flow Studio running at http://localhost:$$(grep FRONTEND_PORT .env | cut -d= -f2 || echo 8080)"
	@echo "  Logs: docker compose logs -f"

docker-down:
	docker compose down

docker-logs:
	docker compose logs -f

# ── Docker single-container ───────────────────────────────────────────────────

docker-single:
	@test -f .env || (cp .env.docker .env && echo "Created .env — edit SECRET_KEY then re-run" && exit 1)
	docker compose -f docker-compose.single.yml up --build -d
	@echo ""
	@echo "  Flow Studio running at http://localhost:$$(grep ^PORT .env | cut -d= -f2 || echo 8000)"

docker-single-down:
	docker compose -f docker-compose.single.yml down

# ── Utilities ─────────────────────────────────────────────────────────────────

gen-key:
	@docker run --rm python:3.13-slim sh -c "pip -q install cryptography 2>/dev/null && python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"

clean:
	rm -rf backend/.venv backend/__pycache__ backend/flow_studio.db
	rm -rf backend/projects backend/frame_cache
	rm -rf frontend/dist frontend/node_modules
