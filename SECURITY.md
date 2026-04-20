# Security Policy

## Supported Versions

| Version | Supported          |
|---------|--------------------|
| 0.1.x   | :white_check_mark: |

## Reporting a Vulnerability

If you discover a security vulnerability, please report it responsibly:

1. **Do NOT** open a public GitHub issue
2. Email: security@xjd.ai (or create a private security advisory on GitHub)
3. Include:
   - Description of the vulnerability
   - Steps to reproduce
   - Potential impact
   - Suggested fix (if any)

We will acknowledge receipt within 48 hours and provide a detailed response within 7 days.

## Security Best Practices

When deploying xjd-agent:

- Never commit API keys or secrets to version control
- Use environment variables or `config.yaml` with restricted permissions
- Enable authentication when exposing the Web or Gateway interface
- Run in Docker with resource limits in production
- Keep dependencies updated (`pip install --upgrade xjd-agent`)
