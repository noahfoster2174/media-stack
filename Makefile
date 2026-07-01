# Reelz / media-stack — common tasks. Run `make help` for the list.
COMPOSE := docker compose

.PHONY: help up down restart logs ps pull health app

help:  ## Show this help
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | sed -E 's/:.*## /\t/' | sort | awk -F'\t' '{printf "  \033[1m%-10s\033[0m %s\n", $$1, $$2}'

up:  ## Start the media stack
	$(COMPOSE) up -d

down:  ## Stop the media stack
	$(COMPOSE) down

restart:  ## Restart the media stack
	$(COMPOSE) restart

logs:  ## Follow all container logs
	$(COMPOSE) logs -f

ps:  ## Show container status
	$(COMPOSE) ps

pull:  ## Pull the pinned images (after bumping a tag in docker-compose.yml)
	$(COMPOSE) pull

health:  ## Ping every service (stack + chat + web app)
	@./health.sh

app:  ## Launch the Reelz web app
	@./launch-app.sh
