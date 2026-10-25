"""Bounded command replies without transport/device identifiers."""
import os
import re


def public_output(value, prefix=(), limit=16384):
    text = value.decode(errors='replace') if isinstance(value, bytes) else str(value or '')
    secrets = [os.environ.get(k, '') for k in ('A9_SERIAL', 'ANDROID_SERIAL')]
    secrets += [prefix[i + 1] for i, word in enumerate(prefix[:-1]) if word == '-s']
    for secret in sorted(set(secrets), key=len, reverse=True):
        if secret:
            text = text.replace(secret, '[redacted]')
    text = re.sub(r'(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}|\b(?:\d{1,3}\.){3}\d{1,3}\b', '[redacted]', text)
    return text[:limit]
