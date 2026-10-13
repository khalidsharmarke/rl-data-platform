.PHONY: up down reset logs test seed psql

up:      ## Build and start everything in the background
	docker compose up --build -d

down:    ## Stop everything (keeps the database)
	docker compose down

reset:   ## Stop everything and delete the database
	docker compose down -v

logs:    ## Follow logs from all services
	docker compose logs -f

test:    ## Run the backend tests against a separate test database
	docker compose run --rm --no-deps api pytest -q

seed:    ## Re-run the (idempotent) seed
	docker compose exec api python -m app.seed

psql:    ## Open a SQL shell on the dev database
	docker compose exec db psql -U rl -d rl
