# Mealie MCP Server

MCP server for interacting with a Mealie recipe manager instance. Provides natural language recipe search, recipe management, and import capabilities.

## Features

- **Natural language search** - Search by ingredients, recipe names, source (nytimes, bonappetit), time constraints
- **Smart caching** - LRU cache with TTL to minimize API calls
- **Connection pooling** - Persistent HTTP connections with retry logic
- **Recipe management** - Create, import from URL, apply tags, delete
- **HTTP/SSE transport** - Runs as a persistent service, no SSH required

## Requirements

- Python 3.10+
- Mealie instance with API access
- API token from Mealie (Settings > API Tokens)

## Installation

### Local Development

```bash
cp .env.example .env
# Edit .env with your Mealie URL and API key

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Run directly (stdio mode)
./run.sh

# Or with HTTP/SSE transport
mcp-proxy --port=8096 python3 server.py
```

### Deploy to Server

```bash
./deploy.sh
```

This will:
1. Copy files to `/opt/mcp-servers/mealie/` on the services VM
2. Create Python venv and install dependencies
3. Install and start systemd service
4. Server listens on port 8096

## Configuration

Environment variables (in `.env`):

| Variable | Description |
|----------|-------------|
| `MEALIE_URL` | Mealie instance URL (e.g., `http://192.168.2.100:9000`) |
| `MEALIE_API_KEY` | API token from Mealie settings |
| `MEALIE_USERNAME` | (Optional) Username for group slug lookup |
| `MEALIE_PASSWORD` | (Optional) Password for group slug lookup |

## Claude Desktop Configuration

### HTTP/SSE Mode (Recommended for remote servers)

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "mealie": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "http://192.168.2.100:8096/sse", "--allow-http"]
    }
  }
}
```

**Note:** The `--allow-http` flag is required for non-HTTPS URLs.

### Direct stdio Mode (Local development)

```json
{
  "mcpServers": {
    "mealie": {
      "command": "/Users/bgrane/Claude/mcp-servers/mealie-mcp/run.sh"
    }
  }
}
```

### Endpoints

| Network | URL |
|---------|-----|
| Local | http://192.168.2.100:8096/sse |
| Tailscale | http://100.78.245.24:8096/sse |

## Available Tools (26 total)

### Recipe Management
| Tool | Description |
|------|-------------|
| `search_recipes` | Natural language recipe search |
| `get_recipe` | Get full recipe details by slug |
| `list_recipes` | List recipes with pagination |
| `get_random_recipe` | Get a random recipe suggestion |
| `create_recipe` | Create a new recipe manually |
| `create_recipe_from_url` | Import recipe from URL |
| `apply_tags` | Apply tags to a recipe |
| `delete_recipe` | Delete a recipe |

### Meal Planning
| Tool | Description |
|------|-------------|
| `get_todays_meals` | What's planned for today |
| `get_meal_plan` | View upcoming meal plan |
| `plan_meal` | Add a recipe to meal plan |
| `plan_random_meal` | Add a random recipe to meal plan |

### Shopping Lists
| Tool | Description |
|------|-------------|
| `get_shopping_lists` | List all shopping lists |
| `get_shopping_list` | Get items from a shopping list |
| `add_to_shopping_list` | Add an item to shopping list |
| `add_recipe_to_shopping_list` | Add recipe ingredients to list |

### Organization
| Tool | Description |
|------|-------------|
| `list_tags` | List available tags |
| `get_recipes_by_tag` | Get recipes with a specific tag |
| `get_categories` | List recipe categories |
| `get_recipes_by_category` | Get recipes in a category |
| `get_cookbooks` | List recipe collections |
| `get_cookbook_recipes` | Get recipes in a cookbook |
| `get_favorites` | Get favorite recipes |

### Utilities
| Tool | Description |
|------|-------------|
| `health_check` | Check server and API connectivity |
| `get_statistics` | Mealie instance statistics |
| `clear_cache` | Clear all cached data |

## Usage Examples

### Search
```
"chicken recipes"           - Find recipes with chicken
"quick under 30 min"        - Fast recipes
"garlic"                    - Search by ingredient
"nytimes"                   - Search by source
"pasta dinner"              - Multiple terms
```

### Meal Planning
```
"What's for dinner tonight?"      - get_todays_meals
"Plan my meals for the week"      - get_meal_plan
"Add a random dinner for Friday"  - plan_random_meal
"Add chicken piccata to Tuesday"  - plan_meal
```

### Shopping
```
"Show me my shopping list"              - get_shopping_list
"Add chicken stir fry to my list"       - add_recipe_to_shopping_list
"Add milk to shopping list"             - add_to_shopping_list
```

## Service Management

```bash
# View status
ssh services "systemctl status mealie-mcp"

# View logs
ssh services "journalctl -u mealie-mcp -f"

# Restart
ssh services "systemctl restart mealie-mcp"

# Stop
ssh services "systemctl stop mealie-mcp"
```

## Architecture

```
Claude Desktop
    │
    ├── (Option A: HTTP/SSE - recommended)
    │   └── mcp-remote ─────► mcp-proxy (port 8096)
    │                              │
    │                              └── server.py (stdio)
    │
    └── (Option B: Direct stdio - local only)
        └── run.sh ─────► server.py
                              │
                              ▼
                         Mealie API
                    (http://192.168.2.100:9000)
```

## Security Note

The current implementation passes the API key via command-line arguments, which makes it visible in process listings (`ps aux`). For a homelab environment this is acceptable, but for more sensitive deployments consider:

1. Using systemd's `LoadCredential` directive
2. Reading credentials from a file at runtime
3. Using a secrets manager

## Troubleshooting

**Connection timeout after ~24 hours (old SSH method)**
- This is why we use HTTP/SSE transport now
- The systemd service auto-restarts if it crashes

**"No recipes found" for ingredient search**
- First search may be slow as it builds the cache
- Try `clear_cache` then search again

**Auth errors in logs**
- Group slug lookup uses username/password
- If not configured, defaults to "home" (usually fine)
- Only affects recipe URL building, not API access

## Development

```bash
# Run tests
cd ~/Claude/mcp-servers/mealie-mcp
source venv/bin/activate
source .env && export MEALIE_URL MEALIE_API_KEY

python3 -c "from server import health_check; print(health_check())"
```
