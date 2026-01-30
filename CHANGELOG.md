# Changelog

## [1.2.0] - 2026-01-30

### Added
- **MCP Prompts** - Teach Claude Desktop how to use mealie tools contextually:
  - `mealie_assistant` - General guide mapping user requests to tools
  - `weekly_meal_planning` - Step-by-step meal planning workflow
  - `whats_for_dinner` - Quick response flow for dinner questions

### How Prompts Work
Prompts are exposed via the MCP protocol. When Claude Desktop connects,
it learns how to interpret natural language requests like:
- "What should I make for dinner?" → checks today's plan, then suggests
- "Plan my week" → uses get_meal_plan, identifies gaps, fills with plan_meal
- "Quick chicken recipe" → search_recipes with time constraints

---

## [1.1.0] - 2026-01-30

### Added
- **`update_recipe` tool** - Flexible recipe modification supporting:
  - `name`, `description` - text field updates
  - `ingredients` - replace ingredient list
  - `instructions` - replace instruction steps
  - `prep_time`, `cook_time`, `total_time` - timing updates
  - `servings` - yield/serving size
  - `notes` - recipe notes
- `PUT` method support in API client

### How update_recipe Works
1. Fetches current recipe via `GET /api/recipes/{slug}`
2. Merges provided changes into recipe object
3. Sends full recipe via `PUT /api/recipes/{slug}`
4. Invalidates cache entry for immediate consistency

### Documentation
- Added detailed parameter table for update_recipe
- Added 5 usage examples (typo fix, time update, ingredient replacement, notes, full overhaul)
- Updated tool count: 26 → 27

---

## [1.0.0] - 2026-01-29

### Initial Release
Full-featured MCP server for Mealie recipe manager with 26 tools.

### Features
- **Recipe Management** (8 tools)
  - `search_recipes` - Natural language search (ingredients, source, time constraints)
  - `get_recipe` - Full recipe details by slug
  - `list_recipes` - Paginated recipe listing
  - `get_random_recipe` - Random suggestion
  - `create_recipe` - Manual recipe creation
  - `create_recipe_from_url` - Import from URL (scraping)
  - `apply_tags` - Tag management
  - `delete_recipe` - Recipe deletion

- **Meal Planning** (4 tools)
  - `get_todays_meals` - Today's plan
  - `get_meal_plan` - Multi-day view
  - `plan_meal` - Add specific recipe
  - `plan_random_meal` - Add random recipe

- **Shopping Lists** (4 tools)
  - `get_shopping_lists` - List all lists
  - `get_shopping_list` - View items
  - `add_to_shopping_list` - Add single item
  - `add_recipe_to_shopping_list` - Add recipe ingredients

- **Organization** (7 tools)
  - `list_tags`, `get_recipes_by_tag`
  - `get_categories`, `get_recipes_by_category`
  - `get_cookbooks`, `get_cookbook_recipes`
  - `get_favorites`

- **Utilities** (3 tools)
  - `health_check` - Server/API status
  - `get_statistics` - Instance stats
  - `clear_cache` - Force fresh data

### Architecture
- HTTP/SSE transport via mcp-proxy (replaces unstable SSH-stdio)
- httpx with connection pooling and retry logic
- LRU cache with TTL (500 recipes, 5 min TTL)
- Group slug caching (1 hour TTL)
- Systemd service for production deployment

### Infrastructure
- Deployed to services LXC (192.168.2.100:8096)
- GitHub: https://github.com/scarabone/mealie-mcp
- One-command deployment: `./deploy.sh`

### Fixes from Original Implementation
- Time parsing: "2 hours" now correctly parses as 120 minutes (was 2)
- ISO 8601 duration support (PT1H30M)
- Group slug caching (was making 2 API calls per recipe URL)
- Bounded cache size (was unbounded memory growth)
- Proper logging (file in production, stderr in dev)
