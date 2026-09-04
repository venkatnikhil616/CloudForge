.PHONY: help install lint test test-unit test-integration test-e2e docker-up docker-down docker-build k8s-apply k8s-delete clean

help:
	@echo "CloudTask Development Makefile"
	@echo "Available commands:"
	@echo "  make install         Install python dependencies"
	@echo "  make lint            Run ruff linter"
	@echo "  make test            Run all tests with pytest"
	@echo "  make test-unit       Run unit tests only"
	@echo "  make test-integration Run integration tests"
	@echo "  make docker-up       Start all services with Docker Compose"
	@echo "  make docker-down     Stop Docker Compose stack"
	@echo "  make docker-build    Build all service docker images"
	@echo "  make k8s-apply       Apply all Kubernetes manifests"
	@echo "  make k8s-delete      Delete all Kubernetes resources"
	@echo "  make clean           Clean cache and temporary files"

install:
	pip install -r requirements.txt

lint:
	ruff check .

test:
	pytest -v tests/

test-unit:
	pytest -v tests/unit/

test-integration:
	pytest -v tests/integration/

docker-up:
	docker compose up -d

docker-down:
	docker compose down -v

docker-build:
	docker compose build

k8s-apply:
	kubectl apply -f deployments/kubernetes/namespace.yaml
	kubectl apply -f deployments/kubernetes/

k8s-delete:
	kubectl delete -f deployments/kubernetes/

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type d -name ".pytest_cache" -exec rm -rf {} +
	rm -rf .coverage htmlcov dist build
