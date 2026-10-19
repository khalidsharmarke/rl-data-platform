.PHONY: up down reset logs test seed psql demo

DEMO = docker compose -f docker-compose.yml -f docker-compose.demo.yml

up:      ## Build and start everything in the background
	docker compose up --build -d

down:    ## Stop everything (keeps the database)
	docker compose down

reset:   ## Stop everything and delete the database
	docker compose down -v

logs:    ## Follow logs from all services
	docker compose logs -f

test:    ## Run the backend tests (against a separate test database) and the detection service's tests
	docker compose run --rm --no-deps api pytest -q
	docker compose run --rm --no-deps --build detection pytest -q
	docker compose build env-image
	docker compose run --rm --no-deps --build sandbox pytest -q -m sandbox
	docker compose run --rm --no-deps --build generator python -m pytest -q

seed:    ## Re-run the (idempotent) seed
	docker compose exec api python -m app.seed

psql:    ## Open a SQL shell on the dev database
	docker compose exec db psql -U rl -d rl

demo:    ## Fresh database, demo clock on, then run the scripted demo scenario
	$(DEMO) down -v
	$(DEMO) up --build -d
	$(DEMO) run --rm --build simulator
