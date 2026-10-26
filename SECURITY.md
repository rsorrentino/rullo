# Security policy

## Supported versions

Rullo is pre-1.0. Security fixes go into the latest release on `main`.

## Reporting a vulnerability

Please do not open a public issue. Use GitHub's private reporting instead:
**Security → Report a vulnerability** on the repository page. Include what you
found, how to reproduce it and what an attacker could do.

You should hear back within a week.

## What is in scope

Rullo runs a local web server bound to `127.0.0.1`. Of particular interest:

- ways for another website, or another user on the same machine, to drive the
  local API, read settings or tokens, or control a trainer
- handling of imported GPX and `.zwo` files
- storage of Strava and intervals.icu credentials
  (`~/.config/rullo/settings.json`, mode 600)
