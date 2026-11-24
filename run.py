"""Local terminal runner for FlapGuard AI Website & Mobile Sync Server."""

import os
from web_app import create_app


app = create_app()


if __name__ == "__main__":
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "5000"))
    print(f"=== FlapGuard AI Server running at http://{host}:{port} ===")
    print("Demo Surgeon Login : surgeon@flapguard.local / Surgeon@123")
    print("Demo Nurse Login   : nurse@flapguard.local   / Nurse@123")
    app.run(host=host, port=port, debug=False)