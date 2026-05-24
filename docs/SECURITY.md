# Security

## Supported version

| Version | Status |
|---|---|
| v1.0.0 | Initial supported release |

## Security model

RetroStation MC v1.0.0 is a local-control application. It has no built-in authentication or authorization. Treat it as a trusted-LAN service.

Do not expose the app directly to the public internet.

## Recommended deployment posture

Use one of these patterns:

- LAN-only access
- VPN-only access
- Authenticated reverse proxy in front of the app
- Firewall rules limiting access to trusted hosts

## Sensitive functions

The admin UI can:

- change playlist and XMLTV sources
- restart and stop FFmpeg/renderer processes
- upload audio files
- delete uploaded audio files
- read logs
- export logs

These functions should not be reachable by untrusted users.

## File upload handling

Music uploads are limited to known audio extensions and validated using magic bytes after saving. Filenames are sanitized with `secure_filename`.

The upload handler is still not a substitute for authentication. A trusted-only network boundary remains required.

## External source handling

M3U and XMLTV sources may be local file paths or HTTP/HTTPS URLs. Only configure sources you trust.

## Reporting vulnerabilities

For public repositories, prefer GitHub private vulnerability reporting or a private security advisory. Include:

- affected version
- deployment mode
- reproduction steps
- expected vs. actual behavior
- relevant logs without secrets

## Hardening roadmap

Recommended future hardening work:

1. Add authentication.
2. Add CSRF protection for write routes.
3. Add role separation for read-only status vs. administrative actions.
4. Add rate limits for uploads and write routes.
5. Add safer source validation for playlist/XMLTV URLs.
6. Add documented reverse-proxy examples.
7. Add container healthcheck endpoint.
