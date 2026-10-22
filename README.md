# signalbloom
SignalBloom project for HotTopic - MIT 2026

## SignalBloom agent

The agent is a small Python program that runs on a computer inside a customer's network. It measures network health and sends the results to the SignalBloom cloud app.

- Only makes outbound HTTPS calls to the cloud. It never opens a port on the customer network.
- Registers a site once, using a one-time setup code from Account & Settings.
- Sends latency, packet loss, uptime and device readings every 30 seconds, plus a bandwidth test every 15 minutes.
- Stores readings while the cloud can't be reached and sends them when the connection comes back.
- Collects fixes an Admin has approved (simulated in the prototype) and reports the results.

The contract between the agent and the cloud is [docs/API.md](docs/API.md). The product spec is [docs/SPEC.md](docs/SPEC.md).

> Status: project skeleton only. Measurements and network calls are not built yet.

### Install

Needs Python 3.12 or newer.

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1        # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
```

### Configure

```powershell
copy config.example.yaml config.yaml    # macOS/Linux: cp config.example.yaml config.yaml
```

Edit `config.yaml` and set `cloud_url` and `setup_code`. Leave `site_id` and `site_token` blank, because the agent fills them in when it registers.

**Never commit `config.yaml`.** It holds the site token. It is already listed in `.gitignore`.

### Run

```powershell
python -m signalbloom_agent              # start the agent
python -m signalbloom_agent --demo-fault # inject a fake fault for demos (not implemented yet)
python -m signalbloom_agent --help
```

On first run the agent registers with `setup_code`, saves `site_id` and `site_token` into `config.yaml`, clears `setup_code`, and limits the file to your user account. Later runs skip registration.

### Test

```powershell
python -m pytest
```

### Try it against the fake cloud (testing only)

`tools/fake_cloud.py` is a local stand-in for the cloud app. It implements the four endpoints in docs/API.md with in-memory data, and accepts one setup code, `TEST-CODE-0001`, once.

```powershell
python tools/fake_cloud.py               # add --with-command to queue a simulated fix
```

In `config.yaml`, set `cloud_url: http://localhost:8080` and `setup_code: TEST-CODE-0001`, then run the agent in another terminal.

Known limitations are listed in [docs/LIMITATIONS.md](docs/LIMITATIONS.md).
