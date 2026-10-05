---
name: passkeep
description: Integrate with Passkeep ID, the company's identity provider (OAuth 2.1 / OpenID Connect): login flows, tokens, refresh, logout, verifying tokens in services.
---

# Passkeep ID

Passkeep is the identity provider for customer and staff logins. Discovery is at `<issuer>/.well-known/openid-configuration`; the issuer URL is in your service's configuration.

## Flows

- **Authorization code with PKCE (S256)** is the only interactive flow. Every client must send `code_challenge` and `code_challenge_method=S256`; `plain` and missing challenges are rejected. `response_type=token` (implicit) is not supported.
- `redirect_uri` must **exactly** equal a registered URI: no extra path, query or trailing slash.
- In this environment there is no browser. `POST /authorize` takes the authorization request **plus** `username` and `password` (form-encoded) and answers `303` with the `Location` a browser would have followed: `?code=…&state=…`, or `?error=…`.
- Exchange the code at `POST /token` with `grant_type=authorization_code`, `code`, `redirect_uri`, `client_id` and `code_verifier`. Codes are **single-use** and valid for 60 s. Presenting a code twice revokes every token it produced.
- **Confidential clients** authenticate with `client_secret_basic` (HTTP Basic) or `client_secret_post`, and may use `grant_type=client_credentials`. Public clients can't.
- `grant_type=password` is not supported.

## Tokens

- **Access tokens** are RS256 JWTs (`typ: at+jwt`) valid for 5 minutes, with `iss`, `aud` (the client, or its configured API audience), `sub`, `scope`, `client_id`, `jti` and any user claims (for example `tenant`). Verify them with the keys at `/jwks`.
- **ID tokens** (`openid` scope) carry `nonce` when you sent one.
- **Refresh tokens rotate:** each use returns a new refresh token, and the old one stops working. Presenting a used refresh token is treated as theft: the **whole token family is revoked**, including access tokens issued from it.
- `POST /revoke` (RFC 7009) revokes a refresh token (and its family) or an access token. It always answers 200.
- `POST /introspect` (RFC 7662) reports whether a token is active.
- `GET /userinfo` with a bearer access token returns the user's claims.

## Errors

The token endpoint uses OAuth error codes: `invalid_grant`, `invalid_client`, `unsupported_grant_type`, `unauthorized_client`, `invalid_request`.
