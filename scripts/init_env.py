"""Generate local admin credentials once; never print or commit the password."""
import os
from pathlib import Path
import secrets

root = Path(__file__).resolve().parent.parent
path = root / ".env"
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w") as output:
    output.write("GRAFANA_ADMIN_USER=admin\nGRAFANA_ADMIN_PASSWORD=" + secrets.token_urlsafe(32) + "\n")
print("Created .env with mode 0600. Password is stored only in this file.")
