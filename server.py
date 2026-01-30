#!/usr/bin/env python3
"""
Mealie MCP Server
Provides natural language recipe search, bulk categorization, and recipe management.

Requires: MEALIE_URL and MEALIE_API_KEY environment variables.
"""
import os
import re
import json
import logging
import time
from functools import lru_cache
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------
MEALIE_URL = os.environ.get("MEALIE_URL", "").rstrip("/")
MEALIE_API_KEY = os.environ.get("MEALIE_API_KEY", "")
MEALIE_USERNAME = os.environ.get("MEALIE_USERNAME", "")
MEALIE_PASSWORD = os.environ.get("MEALIE_PASSWORD", "")

# Cache settings
CACHE_TTL_SECONDS = 300  # 5 minutes
MAX_CACHED_RECIPES = 500  # Limit memory usage

# -----------------------------------------------------------------------------
# Logging
# -----------------------------------------------------------------------------
LOG_FILE = os.environ.get("MCP_LOG_FILE", "")

if LOG_FILE:
    # Production mode: log to file (mcp-proxy doesn't like stderr output)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        filename=LOG_FILE,
        filemode="a"
    )
else:
    # Development mode: log to stderr
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
logger = logging.getLogger("mealie-mcp")

# -----------------------------------------------------------------------------
# HTTP Client (connection pooling, retries)
# -----------------------------------------------------------------------------
_http_client: httpx.Client | None = None


def get_http_client() -> httpx.Client:
    """Get or create a persistent HTTP client with connection pooling."""
    global _http_client
    if _http_client is None:
        transport = httpx.HTTPTransport(retries=3)
        _http_client = httpx.Client(
            timeout=30.0,
            transport=transport,
            headers={
                "Authorization": f"Bearer {MEALIE_API_KEY}",
                "Content-Type": "application/json"
            }
        )
    return _http_client


def api_request(
    endpoint: str,
    method: str = "GET",
    data: dict | None = None,
    retry_count: int = 2
) -> dict:
    """Make authenticated API request to Mealie with retries."""
    url = f"{MEALIE_URL}/api{endpoint}"
    client = get_http_client()

    last_error = None
    for attempt in range(retry_count + 1):
        try:
            if method == "GET":
                resp = client.get(url)
            elif method == "POST":
                resp = client.post(url, json=data)
            elif method == "PATCH":
                resp = client.patch(url, json=data)
            elif method == "DELETE":
                resp = client.delete(url)
            elif method == "PUT":
                resp = client.put(url, json=data)
            else:
                return {"error": f"Unsupported method: {method}"}

            resp.raise_for_status()

            # Handle empty responses (DELETE often returns empty)
            if not resp.content:
                return {"success": True}

            return resp.json()

        except httpx.HTTPStatusError as e:
            error_body = e.response.text if e.response else ""
            last_error = f"HTTP {e.response.status_code}: {error_body[:200]}"
            logger.warning(f"API error on {method} {endpoint}: {last_error}")
            # Don't retry client errors (4xx)
            if e.response.status_code < 500:
                break
        except httpx.RequestError as e:
            last_error = f"Request failed: {str(e)}"
            logger.warning(f"Request error on attempt {attempt + 1}: {last_error}")
            if attempt < retry_count:
                time.sleep(1 * (attempt + 1))  # Backoff
        except Exception as e:
            last_error = str(e)
            logger.error(f"Unexpected error: {last_error}")
            break

    return {"error": last_error}


# -----------------------------------------------------------------------------
# Caching
# -----------------------------------------------------------------------------
class RecipeCache:
    """LRU-style cache for recipe details with TTL."""

    def __init__(self, max_size: int = MAX_CACHED_RECIPES, ttl: int = CACHE_TTL_SECONDS):
        self.max_size = max_size
        self.ttl = ttl
        self._cache: dict[str, dict] = {}
        self._timestamps: dict[str, float] = {}
        self._all_recipes_cache: list | None = None
        self._all_recipes_timestamp: float = 0

    def get(self, slug: str) -> dict | None:
        """Get recipe from cache if not expired."""
        if slug in self._cache:
            if time.time() - self._timestamps[slug] < self.ttl:
                return self._cache[slug]
            else:
                # Expired
                del self._cache[slug]
                del self._timestamps[slug]
        return None

    def set(self, slug: str, recipe: dict) -> None:
        """Add recipe to cache, evicting oldest if full."""
        # Evict oldest if at capacity
        if len(self._cache) >= self.max_size and slug not in self._cache:
            oldest_slug = min(self._timestamps, key=self._timestamps.get)
            del self._cache[oldest_slug]
            del self._timestamps[oldest_slug]

        self._cache[slug] = recipe
        self._timestamps[slug] = time.time()

    def invalidate(self, slug: str | None = None) -> None:
        """Invalidate specific recipe or entire cache."""
        if slug:
            self._cache.pop(slug, None)
            self._timestamps.pop(slug, None)
        else:
            self._cache.clear()
            self._timestamps.clear()
            self._all_recipes_cache = None
            self._all_recipes_timestamp = 0

    def get_all_recipes(self) -> list | None:
        """Get cached list of all recipes if not expired."""
        if self._all_recipes_cache and (time.time() - self._all_recipes_timestamp) < self.ttl:
            return self._all_recipes_cache
        return None

    def set_all_recipes(self, recipes: list) -> None:
        """Cache list of all recipes."""
        self._all_recipes_cache = recipes
        self._all_recipes_timestamp = time.time()

    def stats(self) -> dict:
        """Return cache statistics."""
        return {
            "cached_recipes": len(self._cache),
            "max_size": self.max_size,
            "ttl_seconds": self.ttl,
            "all_recipes_cached": self._all_recipes_cache is not None
        }


_cache = RecipeCache()

# Group slug cache (rarely changes)
_group_slug: str | None = None
_group_slug_timestamp: float = 0
_group_slug_auth_failed: bool = False  # Track auth failures to avoid retry spam
GROUP_SLUG_TTL = 3600  # 1 hour


def get_group_slug() -> str:
    """Get the current user's group slug for URL building (cached)."""
    global _group_slug, _group_slug_timestamp, _group_slug_auth_failed

    # Return cached value if fresh
    if _group_slug and (time.time() - _group_slug_timestamp) < GROUP_SLUG_TTL:
        return _group_slug

    # If auth previously failed, use default without retrying
    if _group_slug_auth_failed:
        return "home"

    # Skip auth attempt if credentials not configured
    if not MEALIE_USERNAME or not MEALIE_PASSWORD:
        logger.info("No username/password configured, using default group 'home'")
        _group_slug = "home"
        _group_slug_timestamp = time.time()
        return _group_slug

    try:
        # Get token via username/password
        client = get_http_client()
        auth_resp = client.post(
            f"{MEALIE_URL}/api/auth/token",
            data={
                "username": MEALIE_USERNAME,
                "password": MEALIE_PASSWORD
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"}
        )
        auth_resp.raise_for_status()
        token = auth_resp.json()["access_token"]

        # Get user info
        user_resp = client.get(
            f"{MEALIE_URL}/api/users/self",
            headers={"Authorization": f"Bearer {token}"}
        )
        user_resp.raise_for_status()
        _group_slug = user_resp.json().get("groupSlug", "home")
        _group_slug_timestamp = time.time()
        logger.info(f"Cached group slug: {_group_slug}")
        return _group_slug

    except Exception as e:
        logger.warning(f"Failed to get group slug: {e}, using 'home' (will not retry)")
        _group_slug_auth_failed = True
        _group_slug = "home"
        _group_slug_timestamp = time.time()
        return _group_slug


def build_recipe_url(slug: str) -> str:
    """Build clickable recipe URL for Mealie."""
    group = get_group_slug()
    return f"{MEALIE_URL}/g/{group}/r/{slug}"


# -----------------------------------------------------------------------------
# Recipe fetching
# -----------------------------------------------------------------------------
def get_all_recipes_summary() -> list:
    """Fetch summary list of all recipes (without full details)."""
    cached = _cache.get_all_recipes()
    if cached:
        logger.debug("Returning cached recipe list")
        return cached

    logger.info("Fetching all recipes from API...")
    all_recipes = []
    page = 1
    per_page = 100

    while True:
        result = api_request(f"/recipes?page={page}&perPage={per_page}")
        if "error" in result:
            logger.error(f"Error fetching recipes page {page}: {result['error']}")
            break
        items = result.get("items", [])
        if not items:
            break
        all_recipes.extend(items)
        if len(items) < per_page:
            break
        page += 1

    logger.info(f"Fetched {len(all_recipes)} recipes")
    _cache.set_all_recipes(all_recipes)
    return all_recipes


def get_recipe_details(slug: str) -> dict | None:
    """Get full recipe details (with caching)."""
    cached = _cache.get(slug)
    if cached:
        return cached

    result = api_request(f"/recipes/{slug}")
    if "error" not in result:
        _cache.set(slug, result)
        return result
    return None


def get_recipes_with_details(slugs: list[str]) -> list[dict]:
    """Fetch full details for a list of recipe slugs."""
    recipes = []
    for slug in slugs:
        details = get_recipe_details(slug)
        if details:
            recipes.append(details)
    return recipes


# -----------------------------------------------------------------------------
# Search helpers
# -----------------------------------------------------------------------------
def get_recipe_search_text(recipe: dict) -> str:
    """Extract all searchable text from a recipe."""
    parts = []

    # Basic info
    parts.append(recipe.get("name", ""))
    parts.append(recipe.get("description", ""))

    # Source URL (for searching by source like "nytimes")
    org_url = recipe.get("orgURL", "")
    if org_url:
        parts.append(org_url)

    # Notes
    for note in recipe.get("notes", []):
        parts.append(note.get("text", ""))

    # Tools
    for tool in recipe.get("tools", []):
        parts.append(tool.get("name", ""))

    # Tags
    for tag in recipe.get("tags", []):
        parts.append(tag.get("name", ""))

    # Categories
    for cat in recipe.get("recipeCategory", []):
        parts.append(cat.get("name", ""))

    # Ingredients
    for ing in recipe.get("recipeIngredient", []):
        parts.append(ing.get("note", ""))
        parts.append(ing.get("display", ""))
        parts.append(ing.get("originalText", ""))
        if ing.get("food"):
            parts.append(ing["food"].get("name", ""))

    # Instructions
    for step in recipe.get("recipeInstructions", []):
        parts.append(step.get("text", ""))

    return " ".join(filter(None, parts)).lower()


def parse_natural_query(query: str) -> dict:
    """Parse natural language query into search parameters."""
    params = {"search": query}

    # Time patterns
    time_match = re.search(r'under\s+(\d+)\s*(?:min|minutes?)', query.lower())
    if time_match:
        params["maxTotalTime"] = int(time_match.group(1))

    # Quick/fast patterns
    if any(word in query.lower() for word in ["quick", "fast", "easy"]):
        params.setdefault("maxTotalTime", 30)

    return params


def extract_time_minutes(time_str: str | None) -> int | None:
    """Extract minutes from time string like '30 Minutes', '2 hours', or 'PT30M'."""
    if not time_str:
        return None

    time_str = str(time_str).lower()
    total_minutes = 0

    # Handle ISO 8601 duration (PT1H30M)
    iso_match = re.search(r'pt(?:(\d+)h)?(?:(\d+)m)?', time_str)
    if iso_match:
        hours = int(iso_match.group(1) or 0)
        minutes = int(iso_match.group(2) or 0)
        return hours * 60 + minutes

    # Handle "X hours Y minutes" format
    hours_match = re.search(r'(\d+)\s*hours?', time_str)
    minutes_match = re.search(r'(\d+)\s*(?:min|minutes?)', time_str)

    if hours_match:
        total_minutes += int(hours_match.group(1)) * 60
    if minutes_match:
        total_minutes += int(minutes_match.group(1))

    # If we found time components, return total
    if total_minutes > 0:
        return total_minutes

    # Fallback: just extract first number (assume minutes)
    num_match = re.search(r'(\d+)', time_str)
    return int(num_match.group(1)) if num_match else None


# -----------------------------------------------------------------------------
# MCP Server
# -----------------------------------------------------------------------------
mcp = FastMCP("mealie")


@mcp.tool()
def health_check() -> str:
    """
    Check server health and Mealie API connectivity.
    Returns status of the server and cache statistics.
    """
    # Check API connectivity
    result = api_request("/recipes?perPage=1")
    api_status = "connected" if "error" not in result else f"error: {result['error']}"

    cache_stats = _cache.stats()

    return f"""Mealie MCP Server Health Check
==============================
API URL: {MEALIE_URL}
API Status: {api_status}
Group Slug: {get_group_slug()}

Cache Statistics:
- Cached recipes: {cache_stats['cached_recipes']}/{cache_stats['max_size']}
- TTL: {cache_stats['ttl_seconds']}s
- Full list cached: {cache_stats['all_recipes_cached']}
"""


@mcp.tool()
def search_recipes(query: str, limit: int = 15) -> str:
    """
    Search recipes using natural language queries.
    Searches recipe names, descriptions, ingredients, and instructions.

    Examples:
    - "chicken recipes"
    - "quick dinners under 30 min"
    - "recipes with garlic"
    - "nytimes" (search by source)

    Args:
        query: Search terms
        limit: Maximum results to return (default 15)
    """
    params = parse_natural_query(query)
    search_words = query.lower().split()
    max_time = params.get("maxTotalTime")

    # Get all recipes (summary only)
    all_recipes = get_all_recipes_summary()

    # First pass: filter by name/description from summary
    candidates = []
    for recipe in all_recipes:
        name = recipe.get("name", "").lower()
        desc = recipe.get("description", "").lower()

        # Quick check on summary fields
        if any(word in name or word in desc for word in search_words):
            candidates.append(recipe.get("slug"))

    # If not enough candidates, we need to check full details
    # This is expensive but necessary for ingredient search
    if len(candidates) < limit and len(search_words) > 0:
        logger.info(f"Expanding search to full recipe details...")
        # Get details for recipes not yet checked
        for recipe in all_recipes:
            slug = recipe.get("slug")
            if slug not in candidates:
                details = get_recipe_details(slug)
                if details:
                    search_text = get_recipe_search_text(details)
                    if any(word in search_text for word in search_words):
                        candidates.append(slug)
                        if len(candidates) >= limit * 2:  # Get extra for filtering
                            break

    # Get full details for candidates and filter
    matched = []
    for slug in candidates:
        details = get_recipe_details(slug)
        if not details:
            continue

        # Verify match with full text
        search_text = get_recipe_search_text(details)
        if not any(word in search_text for word in search_words):
            continue

        # Check time constraint
        if max_time:
            recipe_time = extract_time_minutes(details.get("totalTime"))
            if recipe_time and recipe_time > max_time:
                continue

        matched.append({
            "name": details.get("name"),
            "slug": slug,
            "description": (details.get("description") or "")[:100],
            "totalTime": details.get("totalTime"),
            "url": build_recipe_url(slug)
        })

        if len(matched) >= limit:
            break

    if not matched:
        return f"No recipes found matching '{query}'"

    # Format results
    output = [f"Found {len(matched)} recipe(s) matching '{query}':\n"]
    for r in matched:
        output.append(f"- **{r['name']}**")
        if r['description']:
            output.append(f"  {r['description']}...")
        if r['totalTime']:
            output.append(f"  Time: {r['totalTime']}")
        output.append(f"  Link: {r['url']}\n")

    return "\n".join(output)


@mcp.tool()
def get_recipe(slug: str) -> str:
    """
    Get full recipe details by slug.
    Returns ingredients, instructions, and direct link.

    Args:
        slug: Recipe slug (from search results or URL)
    """
    details = get_recipe_details(slug)
    if not details:
        result = api_request(f"/recipes/{slug}")
        if "error" in result:
            return f"Error getting recipe: {result['error']}"
        details = result

    output = [f"# {details.get('name', 'Recipe')}\n"]

    if details.get("description"):
        output.append(f"{details['description']}\n")

    output.append(f"**Link:** {build_recipe_url(slug)}\n")

    if details.get("totalTime"):
        output.append(f"**Total Time:** {details['totalTime']}")
    if details.get("prepTime"):
        output.append(f"**Prep Time:** {details['prepTime']}")
    if details.get("performTime"):
        output.append(f"**Cook Time:** {details['performTime']}")

    # Servings
    if details.get("recipeYield"):
        output.append(f"**Yield:** {details['recipeYield']}")

    # Ingredients
    ingredients = details.get("recipeIngredient", [])
    if ingredients:
        output.append("\n## Ingredients")
        for ing in ingredients:
            note = ing.get("note", "") or ing.get("display", "")
            if note:
                output.append(f"- {note}")

    # Instructions
    instructions = details.get("recipeInstructions", [])
    if instructions:
        output.append("\n## Instructions")
        for i, step in enumerate(instructions, 1):
            text = step.get("text", "")
            if text:
                output.append(f"{i}. {text}")

    # Notes
    notes = details.get("notes", [])
    if notes:
        output.append("\n## Notes")
        for note in notes:
            text = note.get("text", "")
            if text:
                output.append(f"- {text}")

    # Tags
    tags = details.get("tags", [])
    if tags:
        tag_names = [t.get("name", "") for t in tags]
        output.append(f"\n**Tags:** {', '.join(tag_names)}")

    # Source
    if details.get("orgURL"):
        output.append(f"**Source:** {details['orgURL']}")

    return "\n".join(output)


@mcp.tool()
def list_recipes(limit: int = 20, page: int = 1) -> str:
    """
    List recipes with pagination.

    Args:
        limit: Number of recipes per page (default 20)
        page: Page number (default 1)
    """
    result = api_request(f"/recipes?perPage={limit}&page={page}")
    if "error" in result:
        return f"Error listing recipes: {result['error']}"

    recipes = result.get("items", [])
    total = result.get("total", len(recipes))

    if not recipes:
        return "No recipes found."

    output = [f"# Recipes (page {page}, showing {len(recipes)} of {total})\n"]
    for recipe in recipes:
        name = recipe.get("name", "Untitled")
        slug = recipe.get("slug", "")
        tags = ", ".join([t.get("name", "") for t in recipe.get("tags", [])])

        output.append(f"- **{name}**")
        if tags:
            output.append(f"  Tags: {tags}")
        output.append(f"  {build_recipe_url(slug)}\n")

    if total > page * limit:
        output.append(f"\n*Use page={page + 1} to see more*")

    return "\n".join(output)


@mcp.tool()
def list_tags() -> str:
    """List all available recipe tags."""
    result = api_request("/organizers/tags")
    if "error" in result:
        return f"Error listing tags: {result['error']}"

    tags = result.get("items", [])
    if not tags:
        return "No tags found."

    output = ["# Available Tags\n"]
    for tag in sorted(tags, key=lambda t: t.get("name", "")):
        name = tag.get("name", "")
        slug = tag.get("slug", "")
        output.append(f"- {name} (slug: `{slug}`)")

    return "\n".join(output)


@mcp.tool()
def create_recipe_from_url(url: str) -> str:
    """
    Import a recipe from a URL.
    Mealie will scrape the recipe from the webpage.

    Args:
        url: URL of the recipe page to import
    """
    logger.info(f"Importing recipe from: {url}")
    result = api_request("/recipes/create/url", "POST", {"url": url, "includeTags": True})
    if "error" in result:
        return f"Error importing recipe: {result['error']}"

    # Invalidate cache
    _cache.invalidate()

    slug = result if isinstance(result, str) else result.get("slug", "")
    return f"Recipe imported successfully!\n\nURL: {build_recipe_url(slug)}"


@mcp.tool()
def create_recipe(
    name: str,
    ingredients: list[str],
    instructions: list[str],
    description: str = "",
    prep_time: str = "",
    cook_time: str = "",
    total_time: str = "",
    servings: str = "",
    tags: list[str] | None = None
) -> str:
    """
    Create a new recipe manually.

    Args:
        name: Recipe name (required)
        ingredients: List of ingredient strings (e.g., ["1 cup flour", "2 eggs"])
        instructions: List of instruction steps
        description: Optional description
        prep_time: Optional prep time (e.g., "15 minutes")
        cook_time: Optional cook time (e.g., "30 minutes")
        total_time: Optional total time
        servings: Optional yield (e.g., "4 servings")
        tags: Optional list of tag names
    """
    import uuid

    if not name:
        return "Error: Recipe name is required"

    # Create empty recipe first
    result = api_request("/recipes", "POST", {"name": name})
    if "error" in result:
        return f"Error creating recipe: {result['error']}"

    slug = result if isinstance(result, str) else result.get("slug", name.lower().replace(" ", "-"))

    # Prepare ingredients
    recipe_ingredients = []
    for ing in ingredients:
        recipe_ingredients.append({
            "referenceId": str(uuid.uuid4()),
            "note": ing,
            "display": ing
        })

    # Prepare instructions
    recipe_instructions = []
    for step in instructions:
        recipe_instructions.append({
            "id": str(uuid.uuid4()),
            "text": step
        })

    # Update with full data
    update_data = {
        "name": name,
        "description": description,
        "prepTime": prep_time or None,
        "performTime": cook_time or None,
        "totalTime": total_time or None,
        "recipeYield": servings or None,
        "recipeIngredient": recipe_ingredients,
        "recipeInstructions": recipe_instructions
    }

    update_result = api_request(f"/recipes/{slug}", "PATCH", update_data)
    if "error" in update_result:
        return f"Recipe created but failed to update: {update_result['error']}"

    # Apply tags if provided
    if tags:
        apply_tags(slug, tags)

    # Invalidate cache
    _cache.invalidate()

    return f"Recipe created successfully!\n\nName: {name}\nURL: {build_recipe_url(slug)}"


@mcp.tool()
def apply_tags(slug: str, tags: list[str]) -> str:
    """
    Apply tags to a recipe. Creates tags if they don't exist.

    Args:
        slug: Recipe slug
        tags: List of tag names (e.g., ["dinner", "quick", "chicken"])
    """
    # Get current recipe
    recipe = api_request(f"/recipes/{slug}")
    if "error" in recipe:
        return f"Error getting recipe: {recipe['error']}"

    # Get existing tags
    existing_tags = api_request("/organizers/tags")
    existing_tag_map = {t.get("name"): t for t in existing_tags.get("items", [])}

    tags_to_apply = []
    created_tags = []

    for tag_name in tags:
        if tag_name in existing_tag_map:
            tags_to_apply.append(existing_tag_map[tag_name])
        else:
            # Create new tag
            new_tag = api_request("/organizers/tags", "POST", {"name": tag_name})
            if "error" not in new_tag:
                tags_to_apply.append(new_tag)
                created_tags.append(tag_name)
                logger.info(f"Created new tag: {tag_name}")

    # Merge with existing tags
    current_tags = recipe.get("tags", [])
    all_tags = current_tags + tags_to_apply

    # Deduplicate by ID
    seen_ids = set()
    unique_tags = []
    for t in all_tags:
        tid = t.get("id")
        if tid and tid not in seen_ids:
            seen_ids.add(tid)
            unique_tags.append(t)

    # Update recipe
    update_result = api_request(f"/recipes/{slug}", "PATCH", {"tags": unique_tags})
    if "error" in update_result:
        return f"Error updating recipe: {update_result['error']}"

    # Invalidate cache for this recipe
    _cache.invalidate(slug)

    output = [f"Tags updated for: {recipe.get('name')}\n"]
    if created_tags:
        output.append(f"Created new tags: {', '.join(created_tags)}")
    output.append(f"Applied: {', '.join(tags)}")
    output.append(f"\nURL: {build_recipe_url(slug)}")

    return "\n".join(output)


@mcp.tool()
def update_recipe(
    slug: str,
    name: str | None = None,
    description: str | None = None,
    ingredients: list[str] | None = None,
    instructions: list[str] | None = None,
    prep_time: str | None = None,
    cook_time: str | None = None,
    total_time: str | None = None,
    servings: str | None = None,
    notes: list[str] | None = None
) -> str:
    """
    Update an existing recipe. Only provided fields are updated.

    Args:
        slug: Recipe slug (required)
        name: New recipe name
        description: New description
        ingredients: New list of ingredients (replaces existing)
        instructions: New list of instruction steps (replaces existing)
        prep_time: New prep time (e.g., "15 minutes")
        cook_time: New cook time (e.g., "30 minutes")
        total_time: New total time
        servings: New yield (e.g., "4 servings")
        notes: New list of notes (replaces existing)
    """
    import uuid

    # Fetch current recipe
    recipe = api_request(f"/recipes/{slug}")
    if "error" in recipe:
        return f"Error fetching recipe: {recipe['error']}"

    # Track what we're changing
    changes = []

    # Update simple fields
    if name is not None:
        recipe["name"] = name
        changes.append("name")

    if description is not None:
        recipe["description"] = description
        changes.append("description")

    if prep_time is not None:
        recipe["prepTime"] = prep_time if prep_time else None
        changes.append("prep time")

    if cook_time is not None:
        recipe["performTime"] = cook_time if cook_time else None
        changes.append("cook time")

    if total_time is not None:
        recipe["totalTime"] = total_time if total_time else None
        changes.append("total time")

    if servings is not None:
        recipe["recipeYield"] = servings if servings else None
        changes.append("servings")

    # Update ingredients (replace entire list)
    if ingredients is not None:
        recipe_ingredients = []
        for ing in ingredients:
            recipe_ingredients.append({
                "referenceId": str(uuid.uuid4()),
                "note": ing,
                "display": ing
            })
        recipe["recipeIngredient"] = recipe_ingredients
        changes.append(f"ingredients ({len(ingredients)} items)")

    # Update instructions (replace entire list)
    if instructions is not None:
        recipe_instructions = []
        for step in instructions:
            recipe_instructions.append({
                "id": str(uuid.uuid4()),
                "text": step
            })
        recipe["recipeInstructions"] = recipe_instructions
        changes.append(f"instructions ({len(instructions)} steps)")

    # Update notes (replace entire list)
    if notes is not None:
        recipe_notes = []
        for note in notes:
            recipe_notes.append({
                "id": str(uuid.uuid4()),
                "text": note
            })
        recipe["notes"] = recipe_notes
        changes.append(f"notes ({len(notes)} items)")

    if not changes:
        return "No changes provided. Specify at least one field to update."

    # PUT the updated recipe
    result = api_request(f"/recipes/{slug}", "PUT", recipe)
    if "error" in result:
        return f"Error updating recipe: {result['error']}"

    # Invalidate cache
    _cache.invalidate(slug)

    recipe_name = result.get("name", name or slug)
    output = [f"Recipe updated: **{recipe_name}**\n"]
    output.append(f"Changes: {', '.join(changes)}")
    output.append(f"\nURL: {build_recipe_url(slug)}")

    return "\n".join(output)


@mcp.tool()
def delete_recipe(slug: str, confirm: bool = False) -> str:
    """
    Delete a recipe by slug.

    Args:
        slug: Recipe slug to delete
        confirm: Must be True to actually delete
    """
    if not confirm:
        return f"To delete recipe '{slug}', call again with confirm=True"

    result = api_request(f"/recipes/{slug}", "DELETE")
    if "error" in result:
        return f"Error deleting recipe: {result['error']}"

    # Invalidate cache
    _cache.invalidate(slug)

    return f"Recipe '{slug}' deleted successfully."


@mcp.tool()
def clear_cache() -> str:
    """Clear all cached data to force fresh fetches."""
    global _group_slug, _group_slug_timestamp, _group_slug_auth_failed

    _cache.invalidate()
    _group_slug = None
    _group_slug_timestamp = 0
    _group_slug_auth_failed = False  # Allow retry after cache clear

    return "All caches cleared. Next requests will fetch fresh data."


@mcp.tool()
def get_random_recipe() -> str:
    """Get a random recipe suggestion."""
    import random

    recipes = get_all_recipes_summary()
    if not recipes:
        return "No recipes found."

    recipe = random.choice(recipes)
    slug = recipe.get("slug", "")

    # Get full details
    return get_recipe(slug)


@mcp.tool()
def get_recipes_by_tag(tag: str, limit: int = 20) -> str:
    """
    Get recipes with a specific tag.

    Args:
        tag: Tag name or slug
        limit: Maximum results (default 20)
    """
    # Search by tag
    result = api_request(f"/recipes?tags={tag}&perPage={limit}")
    if "error" in result:
        return f"Error searching by tag: {result['error']}"

    recipes = result.get("items", [])
    if not recipes:
        return f"No recipes found with tag '{tag}'"

    output = [f"# Recipes tagged '{tag}' ({len(recipes)} found)\n"]
    for recipe in recipes:
        name = recipe.get("name", "Untitled")
        slug = recipe.get("slug", "")
        output.append(f"- **{name}**")
        output.append(f"  {build_recipe_url(slug)}\n")

    return "\n".join(output)


# -----------------------------------------------------------------------------
# Meal Planning Tools
# -----------------------------------------------------------------------------
@mcp.tool()
def get_todays_meals() -> str:
    """
    Get today's meal plan.
    Shows what's planned for breakfast, lunch, dinner, and snacks today.
    """
    result = api_request("/households/mealplans/today")
    if "error" in result:
        return f"Error getting today's meals: {result['error']}"

    if not result:
        return "No meals planned for today. Use `plan_meal` to add one!"

    output = ["# Today's Meal Plan\n"]

    # Group by meal type
    meals_by_type = {}
    for meal in result if isinstance(result, list) else [result]:
        meal_type = meal.get("entryType", "dinner").capitalize()
        if meal_type not in meals_by_type:
            meals_by_type[meal_type] = []
        meals_by_type[meal_type].append(meal)

    for meal_type in ["Breakfast", "Lunch", "Dinner", "Snack"]:
        if meal_type.lower() in [m.lower() for m in meals_by_type.keys()]:
            output.append(f"\n## {meal_type}")
            for meal in meals_by_type.get(meal_type, []):
                recipe = meal.get("recipe", {})
                if recipe:
                    name = recipe.get("name", "Unknown")
                    slug = recipe.get("slug", "")
                    output.append(f"- **{name}**")
                    if slug:
                        output.append(f"  {build_recipe_url(slug)}")
                else:
                    # Might be a text-only entry
                    title = meal.get("title", "")
                    if title:
                        output.append(f"- {title}")

    if len(output) == 1:
        return "No meals planned for today."

    return "\n".join(output)


@mcp.tool()
def get_meal_plan(days: int = 7) -> str:
    """
    Get the meal plan for upcoming days.

    Args:
        days: Number of days to show (default 7)
    """
    from datetime import datetime, timedelta

    start_date = datetime.now().strftime("%Y-%m-%d")
    end_date = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")

    result = api_request(f"/households/mealplans?start_date={start_date}&end_date={end_date}")
    if "error" in result:
        return f"Error getting meal plan: {result['error']}"

    items = result.get("items", []) if isinstance(result, dict) else result
    if not items:
        return f"No meals planned for the next {days} days."

    # Group by date
    meals_by_date = {}
    for meal in items:
        date = meal.get("date", "Unknown")
        if date not in meals_by_date:
            meals_by_date[date] = []
        meals_by_date[date].append(meal)

    output = [f"# Meal Plan ({days} days)\n"]

    for date in sorted(meals_by_date.keys()):
        output.append(f"\n## {date}")
        for meal in meals_by_date[date]:
            meal_type = meal.get("entryType", "dinner").capitalize()
            recipe = meal.get("recipe", {})
            if recipe:
                name = recipe.get("name", "Unknown")
                output.append(f"- **{meal_type}**: {name}")
            else:
                title = meal.get("title", "")
                text = meal.get("text", "")
                if title or text:
                    output.append(f"- **{meal_type}**: {title or text}")

    return "\n".join(output)


@mcp.tool()
def plan_random_meal(
    date: str = "",
    meal_type: str = "dinner"
) -> str:
    """
    Add a random recipe to the meal plan.

    Args:
        date: Date in YYYY-MM-DD format (default: today)
        meal_type: breakfast, lunch, dinner, or snack (default: dinner)
    """
    from datetime import datetime

    if not date:
        date = datetime.now().strftime("%Y-%m-%d")

    result = api_request("/households/mealplans/random", "POST", {
        "date": date,
        "entryType": meal_type.lower()
    })

    if "error" in result:
        return f"Error creating random meal: {result['error']}"

    recipe = result.get("recipe", {})
    name = recipe.get("name", "Unknown recipe")
    slug = recipe.get("slug", "")

    output = f"Added random meal to plan!\n\n"
    output += f"**Date**: {date}\n"
    output += f"**Meal**: {meal_type.capitalize()}\n"
    output += f"**Recipe**: {name}\n"
    if slug:
        output += f"**Link**: {build_recipe_url(slug)}"

    return output


@mcp.tool()
def plan_meal(
    recipe_slug: str,
    date: str = "",
    meal_type: str = "dinner"
) -> str:
    """
    Add a specific recipe to the meal plan.

    Args:
        recipe_slug: Slug of the recipe to plan
        date: Date in YYYY-MM-DD format (default: today)
        meal_type: breakfast, lunch, dinner, or snack (default: dinner)
    """
    from datetime import datetime

    if not date:
        date = datetime.now().strftime("%Y-%m-%d")

    # Get recipe ID from slug
    recipe = api_request(f"/recipes/{recipe_slug}")
    if "error" in recipe:
        return f"Error finding recipe: {recipe['error']}"

    recipe_id = recipe.get("id")
    if not recipe_id:
        return f"Could not find recipe with slug '{recipe_slug}'"

    result = api_request("/households/mealplans", "POST", {
        "date": date,
        "entryType": meal_type.lower(),
        "recipeId": recipe_id
    })

    if "error" in result:
        return f"Error planning meal: {result['error']}"

    return f"Added to meal plan!\n\n**Date**: {date}\n**Meal**: {meal_type.capitalize()}\n**Recipe**: {recipe.get('name')}\n**Link**: {build_recipe_url(recipe_slug)}"


# -----------------------------------------------------------------------------
# Shopping List Tools
# -----------------------------------------------------------------------------
@mcp.tool()
def get_shopping_lists() -> str:
    """Get all shopping lists."""
    result = api_request("/households/shopping/lists")
    if "error" in result:
        return f"Error getting shopping lists: {result['error']}"

    items = result.get("items", []) if isinstance(result, dict) else result
    if not items:
        return "No shopping lists found. Create one in Mealie!"

    output = ["# Shopping Lists\n"]
    for lst in items:
        name = lst.get("name", "Untitled")
        list_id = lst.get("id", "")
        item_count = len(lst.get("listItems", []))
        output.append(f"- **{name}** (ID: `{list_id}`)")
        output.append(f"  {item_count} items")

    return "\n".join(output)


@mcp.tool()
def get_shopping_list(list_id: str = "") -> str:
    """
    Get items from a shopping list.

    Args:
        list_id: Shopping list ID (if empty, gets the first/default list)
    """
    # If no ID provided, get the first list
    if not list_id:
        lists = api_request("/households/shopping/lists")
        if "error" in lists:
            return f"Error getting shopping lists: {lists['error']}"
        items = lists.get("items", []) if isinstance(lists, dict) else lists
        if not items:
            return "No shopping lists found."
        list_id = items[0].get("id", "")

    result = api_request(f"/households/shopping/lists/{list_id}")
    if "error" in result:
        return f"Error getting shopping list: {result['error']}"

    name = result.get("name", "Shopping List")
    items = result.get("listItems", [])

    output = [f"# {name}\n"]

    if not items:
        output.append("*Empty list*")
        return "\n".join(output)

    # Group by checked status
    unchecked = [i for i in items if not i.get("checked", False)]
    checked = [i for i in items if i.get("checked", False)]

    if unchecked:
        output.append("## To Buy")
        for item in unchecked:
            note = item.get("note", "") or item.get("display", "")
            quantity = item.get("quantity", "")
            unit = item.get("unit", {})
            unit_name = unit.get("name", "") if unit else ""

            if quantity and unit_name:
                output.append(f"- [ ] {quantity} {unit_name} {note}")
            elif quantity:
                output.append(f"- [ ] {quantity} {note}")
            else:
                output.append(f"- [ ] {note}")

    if checked:
        output.append("\n## Already Got")
        for item in checked:
            note = item.get("note", "") or item.get("display", "")
            output.append(f"- [x] {note}")

    return "\n".join(output)


@mcp.tool()
def add_recipe_to_shopping_list(recipe_slug: str, list_id: str = "") -> str:
    """
    Add all ingredients from a recipe to a shopping list.

    Args:
        recipe_slug: Recipe slug to add ingredients from
        list_id: Shopping list ID (if empty, uses the first/default list)
    """
    # Get recipe ID
    recipe = api_request(f"/recipes/{recipe_slug}")
    if "error" in recipe:
        return f"Error finding recipe: {recipe['error']}"

    recipe_id = recipe.get("id")
    recipe_name = recipe.get("name", recipe_slug)

    # If no list ID, get the first list
    if not list_id:
        lists = api_request("/households/shopping/lists")
        if "error" in lists:
            return f"Error getting shopping lists: {lists['error']}"
        items = lists.get("items", []) if isinstance(lists, dict) else lists
        if not items:
            return "No shopping lists found. Create one in Mealie first!"
        list_id = items[0].get("id", "")
        list_name = items[0].get("name", "Shopping List")
    else:
        list_name = "Shopping List"

    # Add recipe to shopping list
    result = api_request(
        f"/households/shopping/lists/{list_id}/recipe/{recipe_id}",
        "POST"
    )

    if "error" in result:
        return f"Error adding to shopping list: {result['error']}"

    return f"Added ingredients from **{recipe_name}** to **{list_name}**!"


@mcp.tool()
def add_to_shopping_list(item: str, list_id: str = "") -> str:
    """
    Add a single item to a shopping list.

    Args:
        item: Item to add (e.g., "2 lbs chicken breast" or "milk")
        list_id: Shopping list ID (if empty, uses the first/default list)
    """
    # If no list ID, get the first list
    if not list_id:
        lists = api_request("/households/shopping/lists")
        if "error" in lists:
            return f"Error getting shopping lists: {lists['error']}"
        items = lists.get("items", []) if isinstance(lists, dict) else lists
        if not items:
            return "No shopping lists found. Create one in Mealie first!"
        list_id = items[0].get("id", "")

    result = api_request("/households/shopping/items", "POST", {
        "shoppingListId": list_id,
        "note": item,
        "checked": False
    })

    if "error" in result:
        return f"Error adding item: {result['error']}"

    return f"Added **{item}** to shopping list!"


# -----------------------------------------------------------------------------
# Favorites & Cookbooks
# -----------------------------------------------------------------------------
@mcp.tool()
def get_favorites() -> str:
    """Get the user's favorite recipes."""
    result = api_request("/users/self/favorites")
    if "error" in result:
        return f"Error getting favorites: {result['error']}"

    recipes = result.get("items", []) if isinstance(result, dict) else result
    if not recipes:
        return "No favorite recipes yet. Add some in Mealie!"

    output = ["# Favorite Recipes\n"]
    for recipe in recipes:
        name = recipe.get("name", "Untitled")
        slug = recipe.get("slug", "")
        output.append(f"- **{name}**")
        output.append(f"  {build_recipe_url(slug)}\n")

    return "\n".join(output)


@mcp.tool()
def get_cookbooks() -> str:
    """Get all cookbooks (recipe collections)."""
    result = api_request("/households/cookbooks")
    if "error" in result:
        return f"Error getting cookbooks: {result['error']}"

    cookbooks = result.get("items", []) if isinstance(result, dict) else result
    if not cookbooks:
        return "No cookbooks found."

    output = ["# Cookbooks\n"]
    for cb in cookbooks:
        name = cb.get("name", "Untitled")
        cookbook_id = cb.get("id", "")
        description = cb.get("description", "")
        output.append(f"- **{name}** (ID: `{cookbook_id}`)")
        if description:
            output.append(f"  {description}")

    return "\n".join(output)


@mcp.tool()
def get_cookbook_recipes(cookbook_id: str) -> str:
    """
    Get recipes in a specific cookbook.

    Args:
        cookbook_id: The cookbook ID (from get_cookbooks)
    """
    result = api_request(f"/households/cookbooks/{cookbook_id}")
    if "error" in result:
        return f"Error getting cookbook: {result['error']}"

    name = result.get("name", "Cookbook")
    recipes = result.get("recipes", [])

    output = [f"# {name}\n"]

    if not recipes:
        output.append("*No recipes in this cookbook*")
        return "\n".join(output)

    output.append(f"{len(recipes)} recipes:\n")
    for recipe in recipes:
        recipe_name = recipe.get("name", "Untitled")
        slug = recipe.get("slug", "")
        output.append(f"- **{recipe_name}**")
        output.append(f"  {build_recipe_url(slug)}\n")

    return "\n".join(output)


# -----------------------------------------------------------------------------
# Statistics & Info
# -----------------------------------------------------------------------------
@mcp.tool()
def get_statistics() -> str:
    """Get statistics about your Mealie instance."""
    result = api_request("/households/statistics")
    if "error" in result:
        return f"Error getting statistics: {result['error']}"

    output = ["# Mealie Statistics\n"]

    stats = [
        ("Total Recipes", result.get("totalRecipes", 0)),
        ("Total Users", result.get("totalUsers", 0)),
        ("Total Tags", result.get("totalTags", 0)),
        ("Total Categories", result.get("totalCategories", 0)),
        ("Total Households", result.get("totalHouseholds", 1)),
    ]

    for label, value in stats:
        output.append(f"- **{label}**: {value}")

    return "\n".join(output)


@mcp.tool()
def get_categories() -> str:
    """Get all recipe categories."""
    result = api_request("/organizers/categories")
    if "error" in result:
        return f"Error getting categories: {result['error']}"

    categories = result.get("items", [])
    if not categories:
        return "No categories found."

    output = ["# Recipe Categories\n"]
    for cat in sorted(categories, key=lambda c: c.get("name", "")):
        name = cat.get("name", "")
        slug = cat.get("slug", "")
        output.append(f"- {name} (slug: `{slug}`)")

    return "\n".join(output)


@mcp.tool()
def get_recipes_by_category(category: str, limit: int = 20) -> str:
    """
    Get recipes in a specific category.

    Args:
        category: Category name or slug
        limit: Maximum results (default 20)
    """
    result = api_request(f"/recipes?categories={category}&perPage={limit}")
    if "error" in result:
        return f"Error searching by category: {result['error']}"

    recipes = result.get("items", [])
    if not recipes:
        return f"No recipes found in category '{category}'"

    output = [f"# Recipes in '{category}' ({len(recipes)} found)\n"]
    for recipe in recipes:
        name = recipe.get("name", "Untitled")
        slug = recipe.get("slug", "")
        output.append(f"- **{name}**")
        output.append(f"  {build_recipe_url(slug)}\n")

    return "\n".join(output)


# -----------------------------------------------------------------------------
# Startup validation
# -----------------------------------------------------------------------------
def validate_config() -> bool:
    """Validate configuration on startup."""
    errors = []

    if not MEALIE_URL:
        errors.append("MEALIE_URL environment variable not set")
    if not MEALIE_API_KEY:
        errors.append("MEALIE_API_KEY environment variable not set")

    if errors:
        for err in errors:
            logger.error(err)
        return False

    # Test API connectivity
    logger.info(f"Testing connection to {MEALIE_URL}...")
    result = api_request("/recipes?perPage=1")
    if "error" in result:
        logger.error(f"Failed to connect to Mealie API: {result['error']}")
        return False

    logger.info("Mealie API connection successful")

    # Pre-cache group slug
    slug = get_group_slug()
    logger.info(f"Group slug: {slug}")

    return True


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    if not validate_config():
        logger.error("Configuration validation failed. Check environment variables.")
        exit(1)

    logger.info("Starting Mealie MCP server...")
    mcp.run()
