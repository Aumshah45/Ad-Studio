"""Print the OpenAPI JSON (used by `make contract`)."""

import json

from backend.core.settings import Settings
from backend.http.app import create_app


def main() -> None:
    app = create_app(Settings(otel_enabled=False))
    print(json.dumps(app.openapi(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
