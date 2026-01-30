# Claude.ai Web Integration - Research Notes

> **Status**: Not implemented. Claude.ai web requires OAuth 2.1 which this server doesn't support.

## What We Tried (2026-01-30)

### Goal
Expose mealie-mcp to the internet so it could be used with Claude.ai web interface and Claude iOS app.

### Approach
1. Installed `cloudflared` on services LXC
2. Created Cloudflare Tunnel with cryptic subdomain (`mcp-3f9f97ca.1701media.com`)
3. Configured tunnel to proxy to `localhost:8096` (mealie-mcp service)
4. Set up systemd service for persistent tunnel
5. Tested endpoint - returned HTTP 200 with `text/event-stream`

### Result
Claude.ai rejected the connection with error:
> "There was an error connecting to the MCP server. Please check your server URL and make sure your server handles auth correctly."

Server logs showed Claude.ai probing for OAuth endpoints:
```
GET /.well-known/oauth-protected-resource/sse - 404
GET /.well-known/oauth-protected-resource - 404
GET /.well-known/oauth-authorization-server - 404
POST /register - 404
```

## What We Learned

### Claude.ai Web Requirements
Claude.ai web **requires OAuth 2.1 authentication** for MCP servers. Authless servers are not supported on the web interface.

From [Anthropic's documentation](https://support.claude.com/en/articles/11503834-building-custom-connectors-via-remote-mcp-servers):
- "Claude supports both authless and OAuth-based remote servers" - but this applies to **Claude Desktop**, not Claude.ai web
- Claude.ai web mandates OAuth with Dynamic Client Registration (RFC 7591)
- Required OAuth callback URL: `https://claude.ai/api/mcp/auth_callback`
- Must support token expiry and refresh

### OAuth Implementation Requirements
Per [MCP OAuth guides](https://www.buildwithmatija.com/blog/oauth-mcp-server-claude):
- OAuth 2.1 with PKCE
- `/.well-known/oauth-authorization-server` metadata endpoint
- Dynamic Client Registration (`/register` endpoint)
- Standard authorization and token endpoints
- Options: Auth0, Azure Entra ID, or custom implementation

### What Works Without OAuth

| Client | Authless Support | Notes |
|--------|------------------|-------|
| Claude Desktop | Yes | Via `mcp-remote` proxy or direct config |
| Claude Code | Yes | Direct connection, no auth needed |
| Claude.ai Web | **No** | Requires OAuth 2.1 |
| Claude iOS | **No** | Same as web, requires OAuth |
| Cloudflare AI Playground | Yes | Alternative web interface |

### Plan Requirements
Custom MCP connectors require: **Pro, Max, Team, or Enterprise** plan.

## Options for Claude.ai Web Support

### Option 1: Implement OAuth (Complex)
Add OAuth 2.1 support to mealie-mcp:
- Use Auth0, Azure Entra ID, or similar identity provider
- Implement required endpoints
- Handle token refresh
- Significant development effort

### Option 2: Use Claude Desktop (Current)
Continue using Claude Desktop for MCP access:
- Works on local network without tunnel
- Can use Cloudflare Tunnel for remote access if needed
- No code changes required

### Option 3: Use Claude Code (Current)
Already works - this is how we primarily interact with mealie-mcp.

## References

- [Building Custom Connectors via Remote MCP Servers](https://support.claude.com/en/articles/11503834-building-custom-connectors-via-remote-mcp-servers)
- [Remote MCP Server Submission Guide](https://support.claude.com/en/articles/12922490-remote-mcp-server-submission-guide)
- [OAuth for MCP Server Guide](https://www.buildwithmatija.com/blog/oauth-mcp-server-claude)
- [MCP Server Setup with OAuth using Auth0](https://medium.com/neural-engineer/mcp-server-setup-with-oauth-authentication-using-auth0-and-claude-ai-remote-mcp-integration-8329b65e6664)
- [Remote MCP Server (Authless) examples](https://glama.ai/mcp/servers/@TheseHandsAreSpiders/remote-mcp-server-authless) - Claude Desktop only

## Conclusion

Claude.ai web integration requires OAuth 2.1 implementation. Until that's added, use Claude Desktop or Claude Code for mealie-mcp access. The tunnel infrastructure (Cloudflare) could still be useful if OAuth is implemented later, or for Claude Desktop remote access.
