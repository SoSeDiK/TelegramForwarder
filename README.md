# 📡 Telegram Forwarder

Forwards messages from Telegram channels to Discord (webhooks).

## ✨ Features

- Monitor multiple Telegram channels simultaneously
- Forward messages to one or more Discord webhooks
- Rich message formatting (text, images, media)
- Easy to use (configurable via YAML, supports config reload)

## 🚀 Setup

### Prerequisites

- Python 3.14 or higher.
- A Telegram **API ID** and **API Hash** (see the [guide](https://core.telegram.org/api/obtaining_api_id)).
- Discord webhook URLs for the channels you want to forward to.

### Installation

```bash
# Clone the repository
git clone https://github.com/SoSeDiK/TelegramForwarder.git
cd TelegramForwarder

# Create and activate a virtual environment
python -m venv venv
source venv/bin/activate   # On Windows: venv\Scripts\activate

# Install the package in editable mode
pip install -e .
```
### Configuration

1. **Environment variables** – Copy `.env_sample` to `.env` and fill in your Telegram credentials:

```ini
API_ID=1234567
API_HASH=your_api_hash_here
```

2. **Forwarding rules** – Copy `config_sample.yml` to `config.yml` and configure inputs & outputs.

3. **Run the bot** by running the `telegram-forwarder` installed earlier.

### Console Commands

While the bot is running, you can use the interactive console:
- help – show available commands
- reload – reload config.yml without restarting
- stop or `Ctrl + C` – stop the bot

## 📄 License

Distributed under the MIT License. See `LICENSE` for more information.
