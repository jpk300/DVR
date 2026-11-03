"""HDHomeRun DVR Manager.

Modules:
    config      environment settings, tunables and logging setup
    storage     JSON files in DATA_DIR (settings, keep limits, last automatic run)
    dvr_client  every request to the DVR and Silicondust's cloud, behind the SSRF guard
    pruning     keep-limit plans and deleting what they select
    scheduler   the daily automatic cleanup
    routes      the Flask app and its HTTP API
"""
