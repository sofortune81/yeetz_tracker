#!/bin/bash

# Define the project directory
PROJECT_DIR="/var/services/homes/kelvin/yeetz_tracker"

# Navigate to the directory
cd "$PROJECT_DIR" || { echo "❌ Directory not found: $PROJECT_DIR"; exit 1; }

# Function to show the menu
show_menu() {
    echo "=============================="
    echo "🐳 Yeetz Bot Manager (Docker)"
    echo "=============================="
    echo "1) 🚀 Start / Restart (Quick)"
    echo "2) 🔨 Rebuild & Update (Use after code changes)"
    echo "3) 🛑 Stop Bot"
    echo "4) 🔍 Check Status (Is it running?)"
    echo "5) 📋 View Logs (Ctrl+C to exit)"
    echo "6) 🚪 Exit Menu"
    echo "=============================="
    echo -n "Select an option [1-6]: "
}

# Loop until the user chooses to exit
while true; do
    show_menu
    read choice
    echo ""

    case $choice in
        1)
            echo "🔄 Restarting yeetz-listener..."
            sudo docker-compose restart yeetz-listener
            # Ensure it starts if it wasn't running
            sudo docker-compose up -d yeetz-listener
            echo "✅ Done."
            ;;
        2)
            echo "🔨 Rebuilding container (this may take a minute)..."
            sudo docker-compose up -d --build yeetz-listener
            echo "✅ Rebuild complete."
            ;;
        3)
            echo "🛑 Stopping yeetz-listener..."
            sudo docker-compose stop yeetz-listener
            echo "✅ Bot stopped."
            ;;
        4)
            echo "🔍 Checking container status..."
            echo "----------------------------------------------------------------"
            # Shows running containers for this project
            sudo docker-compose ps
            echo "----------------------------------------------------------------"
            echo "Resource Usage:"
            # Shows CPU/Memory usage (no-stream means just a snapshot, not live)
            sudo docker stats --no-stream yeetz_listener --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.NetIO}}"
            ;;
        5)
            echo "📋 Attaching to logs... (Press Ctrl+C to return/exit)"
            echo "---------------------------------------------------"
            sudo docker-compose logs -f yeetz-listener
            ;;
        6)
            echo "👋 Exiting."
            exit 0
            ;;
        *)
            echo "❌ Invalid option. Please try again."
            ;;
    esac

    echo ""
    echo "Press Enter to continue..."
    read
    clear
done