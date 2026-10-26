# Soniquemini Ledboard Assembly

## Configuration

In PowerShell, create `.env` in the project root by running
`Copy-Item .env.example .env`. Set a unique `SECRET_KEY`, a strong
`ADMIN_PASSWORD`, and the database connection values; do not leave the example
placeholders or required database values blank. The app loads this file
automatically when started with Python or Docker Compose and exits during
startup if any required value is missing.

When deployed behind one trusted reverse proxy, the app uses Werkzeug
`ProxyFix` to honor one value each for the forwarded client address, scheme,
host, port, and URL prefix. Configure the proxy to overwrite these forwarded
headers and restrict direct access to the Gunicorn port; otherwise clients
could supply spoofed forwarded values.

For local development, install dependencies with `pip install -r requirements.txt`
and start with `python run.py`. To run in Docker, use `docker compose up --build`.
The container uses Gunicorn and stores timestamps in the `Asia/Manila` timezone.
Gunicorn runs two workers, and MySQL connections use five-second connect, read,
and write timeouts by default. Scan write transactions allow up to 15 seconds
for the database response, and admin reads allow up to 20 seconds. If a database
operation times out, API requests return HTTP 503 with a retry message rather
than waiting for the Gunicorn worker timeout. Production-sized admin query
performance still needs validation against the deployment database. Sonique
Mini assembly scanning reads station and unit data from `soniquemini` and writes
inspection records to `soniquemini.ledboard_assembly`.

Scan the chargingboard serial first; it must pass `test` in
`soniquemini.chargingboard_main`. Next scan the controlboard serial; it must pass
both `progtest` and `vi` in `soniquemini.controlboard_main`. Then scan the
LED-board serial; it must exist in `soniquemini.ledboard_main` and pass `test`,
`lasermarking`, `assembly`, `finaltest`, `fvi`, and `packaging`. On PASS the app
records the LED-board serial, PO number, operator, shift, chargingboard serial,
controlboard serial, timestamp, test repetition, remarks, and status in
`soniquemini.ledboard_assembly`. FAIL records use serial suffixes `_1` through
`_3` and matching `test_rep` values; a fourth FAIL attempt is not accepted.