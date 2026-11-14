from pathlib import Path


def test_public_ws_logs_are_disabled_and_uvicorn_access_log_is_off():
    deploy = Path(__file__).resolve().parents[1] / "deploy"
    nginx = (deploy / "nginx-agent.conf.example").read_text()
    service = (deploy / "esp32-agent.service.example").read_text()
    ws_block = nginx.split("location = /agent/ws/v1/device", 1)[1].split("location /agent/", 1)[0]
    assert "access_log off;" in ws_block
    assert "error_log /var/log/nginx/agent-ws-error.log crit;" in ws_block
    assert "proxy_set_header Upgrade $http_upgrade;" in ws_block
    assert "--no-access-log" in service
