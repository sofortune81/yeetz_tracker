# --- DAILY DEVELOPMENT ---

# 1. Dev Mode (Most Used)
# Rebuilds the app and immediately follows the logs.
# Use this when you change code and want to see it run.
dev:
	docker compose up -d --build
	docker compose logs -f --tail 100

# 2. Logs Only
# Follows the logs (last 100 lines) without restarting anything.
logs:
	docker compose logs -f --tail 100

# 3. Rebuild Only
# Rebuilds and restarts in the background (detached mode).
rebuild:
	docker compose up -d --build

# --- BASIC CONTROLS ---

# Start existing containers (no rebuild)
start:
	docker compose start

# Stop containers (keeps them exists, just paused)
stop:
	docker compose stop

# Restart containers
restart:
	docker compose restart

# --- MAINTENANCE & CLEANUP ---

# Check Disk Space
# Shows how much space Images and Cache are taking up.
space:
	docker system df

# Safe Clean (Reclaims Space)
# Deletes "Dangling" images and "Build Cache" (the 11GB you saw).
# SAFE: Does NOT delete running apps or Database data.
clean:
	docker builder prune -f
	docker image prune -f

# --- DANGER ZONE ---

# Project Reset
# Stops containers and deletes THIS project's specific images.
# Use this if you want to uninstall/reset this specific app.
reset:
	docker compose down --rmi local