# Mealie MCP Server - Test Handoff

## Mission

Thoroughly test the mealie-mcp server to verify all 27 tools work correctly. Report any failures, unexpected behavior, or suggestions for improvement.

## Background

This is an MCP server for Mealie (self-hosted recipe manager). It's deployed on a services LXC at 192.168.2.100:8096 and connects to Mealie at 192.168.2.100:9000.

- **Local project**: `~/Claude/mcp-servers/mealie-mcp/`
- **Deployed to**: services LXC (`ssh services`)
- **Server file**: `/opt/mcp-servers/mealie/server.py`
- **Logs**: `/var/log/mealie-mcp.log` and `journalctl -u mealie-mcp`

## Test Environment Setup

```bash
cd ~/Claude/mcp-servers/mealie-mcp
source venv/bin/activate
source .env && export MEALIE_URL MEALIE_API_KEY
```

## Test Plan

### 1. Health & Connectivity

```bash
# Test health check
python3 -c "from server import health_check; print(health_check())"
```

**Expected**: Shows API URL, "connected" status, cache stats, group slug "home"

### 2. Recipe Search Tools

Test each search scenario:

```python
from server import search_recipes, get_recipe, list_recipes, get_random_recipe

# Basic search
print(search_recipes("chicken"))

# Ingredient search
print(search_recipes("garlic lemon"))

# Time-constrained search
print(search_recipes("quick under 30 min"))

# Source search (recipes imported from specific sites)
print(search_recipes("nytimes"))

# Pagination
print(list_recipes(limit=5, page=1))
print(list_recipes(limit=5, page=2))

# Random recipe
print(get_random_recipe())

# Get specific recipe (use a slug from search results)
print(get_recipe("chicken-piccata"))  # adjust slug as needed
```

**Verify**:
- Results contain recipe names, descriptions, links
- Links are valid URLs to Mealie (http://192.168.2.100:9000/g/home/r/...)
- Time search filters correctly (recipes should be ≤30 min)
- No errors or empty results for valid queries

### 3. Recipe CRUD Tools

```python
from server import create_recipe, update_recipe, apply_tags, delete_recipe

# Create a test recipe
result = create_recipe(
    name="Test Recipe from MCP",
    ingredients=["1 cup test ingredient", "2 tbsp testing powder"],
    instructions=["Step 1: Test the first step", "Step 2: Verify it works"],
    description="A test recipe created by MCP server testing",
    prep_time="5 minutes",
    cook_time="10 minutes",
    servings="2 servings"
)
print(result)
# Note the slug from the output

# Update the recipe
print(update_recipe(
    slug="test-recipe-from-mcp",  # adjust if different
    description="Updated description for testing",
    notes=["This is a test note"]
))

# Apply tags
print(apply_tags("test-recipe-from-mcp", ["test", "mcp-testing"]))

# Verify changes
print(get_recipe("test-recipe-from-mcp"))

# Clean up - delete test recipe
print(delete_recipe("test-recipe-from-mcp", confirm=True))
```

**Verify**:
- Recipe creates successfully with all fields
- Update modifies only specified fields
- Tags are applied (may create new tags)
- Delete removes the recipe

### 4. URL Import

```python
from server import create_recipe_from_url

# Test with a known recipe URL (use a real recipe page)
print(create_recipe_from_url("https://www.seriouseats.com/easy-pan-fried-pork-chops-recipe"))
```

**Verify**:
- Recipe imports with name, ingredients, instructions
- Returns valid Mealie URL
- (Clean up: delete the imported recipe after testing)

### 5. Meal Planning Tools

```python
from server import get_todays_meals, get_meal_plan, plan_meal, plan_random_meal
from datetime import datetime, timedelta

# Check today's meals
print(get_todays_meals())

# Check weekly plan
print(get_meal_plan(days=7))

# Plan a random meal for tomorrow
tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
print(plan_random_meal(date=tomorrow, meal_type="dinner"))

# Plan a specific meal (need a valid recipe slug)
print(plan_meal(recipe_slug="chicken-piccata", date=tomorrow, meal_type="lunch"))
```

**Verify**:
- Today's meals shows correct date grouping
- Meal plan shows multiple days
- Random meal adds entry with recipe details
- Specific meal planning works with valid slugs

### 6. Shopping List Tools

```python
from server import get_shopping_lists, get_shopping_list, add_to_shopping_list, add_recipe_to_shopping_list

# List all shopping lists
print(get_shopping_lists())

# Get default shopping list contents
print(get_shopping_list())

# Add a single item
print(add_to_shopping_list("Test item from MCP"))

# Add recipe ingredients (use valid slug)
print(add_recipe_to_shopping_list("chicken-piccata"))

# Verify items were added
print(get_shopping_list())
```

**Verify**:
- Shopping lists are retrieved
- Items show checked/unchecked status
- Single items add correctly
- Recipe ingredients are added

### 7. Organization Tools

```python
from server import list_tags, get_recipes_by_tag, get_categories, get_recipes_by_category
from server import get_cookbooks, get_cookbook_recipes, get_favorites

# Tags
print(list_tags())
print(get_recipes_by_tag("dinner"))  # adjust tag as needed

# Categories
print(get_categories())
print(get_recipes_by_category("Main Dish"))  # adjust category as needed

# Cookbooks
print(get_cookbooks())
# If cookbooks exist, test get_cookbook_recipes with an ID

# Favorites
print(get_favorites())
```

**Verify**:
- Tags list shows available tags with slugs
- Tag filtering returns relevant recipes
- Categories work similarly
- Cookbooks list (may be empty)
- Favorites returns user's favorited recipes

### 8. Utility Tools

```python
from server import get_statistics, clear_cache

# Stats
print(get_statistics())

# Clear cache
print(clear_cache())

# Verify cache was cleared (next search should be slower)
import time
start = time.time()
search_recipes("test")
print(f"Search after cache clear: {time.time() - start:.2f}s")
```

### 9. MCP Prompts

```python
import asyncio
from server import mcp

async def check_prompts():
    prompts = await mcp.list_prompts()
    for p in prompts:
        print(f"- {p.name}: {p.description[:50]}...")
        content = await mcp.get_prompt(p.name)
        print(f"  Content length: {len(str(content))} chars")

asyncio.run(check_prompts())
```

**Expected prompts**:
- `mealie_assistant` - General usage guide
- `weekly_meal_planning` - Meal planning workflow
- `whats_for_dinner` - Dinner suggestion flow

### 10. Error Handling

```python
# Invalid recipe slug
print(get_recipe("this-recipe-does-not-exist-12345"))

# Invalid search (should return "No recipes found")
print(search_recipes("xyznonexistentingredient123"))

# Delete without confirm
print(delete_recipe("chicken-piccata", confirm=False))

# Empty update (should return error message)
print(update_recipe("chicken-piccata"))
```

**Verify**:
- Errors return helpful messages, not stack traces
- Invalid operations are handled gracefully

### 11. Service Health (SSH to services)

```bash
# Check service status
ssh services "systemctl status mealie-mcp"

# Check recent logs for errors
ssh services "tail -50 /var/log/mealie-mcp.log"
ssh services "journalctl -u mealie-mcp -n 50 --no-pager"

# Check for memory issues
ssh services "ps aux | grep mealie"
```

## Reporting

Create a summary with:

1. **Pass/Fail** for each test section
2. **Errors encountered** - exact error messages
3. **Unexpected behavior** - things that worked but seemed wrong
4. **Performance notes** - slow operations
5. **Suggestions** - improvements or missing features

## Quick Test Commands

One-liner to run basic tests:

```bash
cd ~/Claude/mcp-servers/mealie-mcp && source venv/bin/activate && source .env && export MEALIE_URL MEALIE_API_KEY && python3 << 'EOF'
from server import health_check, search_recipes, get_todays_meals, get_shopping_list, get_statistics

print("=== Health Check ===")
print(health_check())

print("\n=== Search Test ===")
print(search_recipes("chicken", limit=3))

print("\n=== Today's Meals ===")
print(get_todays_meals())

print("\n=== Shopping List ===")
print(get_shopping_list())

print("\n=== Statistics ===")
print(get_statistics())
EOF
```

## Known Issues

- Group slug auth fails (401) but falls back to "home" - this is expected if MEALIE_USERNAME/PASSWORD aren't configured
- First search after startup may be slow (building cache)

## Files Reference

| File | Purpose |
|------|---------|
| `server.py` | Main MCP server (27 tools, 3 prompts) |
| `deploy.sh` | One-command deployment to services LXC |
| `run.sh` | Local stdio runner |
| `run-proxy.sh` | Production HTTP/SSE wrapper |
| `requirements.txt` | Python dependencies |
| `.env` | Local environment (not in git) |
| `.env.example` | Environment template |
