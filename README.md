# Gemini Web Proxy for OpenCode

A free Gemini web interface proxy that works as an OpenAI-compatible API for OpenCode. This project allows you to use your free Gemini web subscription as a local API provider with full tool calling support.

**Educational Purpose**: This project is created for learning purposes to demonstrate how to build custom providers for OpenCode using browser automation.

## What This Does

- Converts Gemini web interface into an OpenAI-compatible API
- Enables free Gemini usage in OpenCode without API keys
- Supports tool calling and function execution
- Maintains persistent login sessions
- Works completely locally on your machine

## How It Works

1. Uses Playwright to automate the Gemini web interface
2. Translates OpenCode requests to Gemini web format
3. Extracts responses and converts them back to OpenAI format
4. Handles tool calling through advanced prompt engineering
5. Maintains session state for continuous conversations

## Requirements

- Python 3.8 or higher
- Google account with Gemini access
- Chrome browser installed
- OpenCode installed

## Installation

### Quick Setup (Recommended)

```bash
git clone <repository-url>
cd gemini-web-proxy
chmod +x setup.sh
./setup.sh
```

### Manual Setup

### Step 1: Clone and Setup

```bash
git clone <repository-url>
cd gemini-web-proxy
```

### Step 2: Install Dependencies

```bash
# Install Python dependencies
pip install -r requirements.txt

# Install Playwright browsers
playwright install chromium
```

### Step 3: First Run and Login

```bash
python run.py
```

On first run:
1. Chrome browser will open automatically
2. Log in to your Google account
3. Navigate to Gemini if not redirected automatically
4. Wait for "Login saved" message
5. Browser will restart in headless mode

### Step 4: Configure OpenCode

Add this configuration to your OpenCode config file:

**Location**: `~/.config/opencode/config.json` or your project's `opencode.json`

```json
{
  "providers": {
    "00bx-gemini": {
      "npm": "@ai-sdk/openai-compatible",
      "options": {
        "baseURL": "http://localhost:8080/v1",
        "apiKey": "not-needed"
      },
      "models": {
        "00bx-gemini-web": {}
      }
    }
  }
}
```

### Step 5: Set as Default Model

```bash
opencode config set model 00bx-gemini/00bx-gemini-web
```

## Usage

### Starting the Proxy

```bash
python run.py
```

The proxy will start on `http://localhost:8080`

### Testing the Connection

```bash
curl http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "00bx-gemini-web",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

### Using with OpenCode

Once configured, simply use OpenCode normally. All requests will be routed through your free Gemini web interface.

## Features

- **Free Usage**: No API keys or paid subscriptions required
- **Tool Calling**: Full support for OpenCode's tool system
- **Persistent Sessions**: Login once, use indefinitely
- **Local Operation**: Everything runs on your machine
- **OpenAI Compatible**: Works with any OpenAI-compatible client

## Troubleshooting

### Login Issues

If login fails or expires:

```bash
# Reset the session
rm -rf ~/.gemini-service
python run.py
```

### Connection Problems

1. Ensure Chrome is installed and accessible
2. Check that port 8080 is not in use
3. Verify your Google account has Gemini access
4. Try restarting the proxy

### OpenCode Configuration

Make sure your OpenCode configuration matches exactly:
- Provider name: `00bx-gemini`
- Model name: `00bx-gemini-web`
- Base URL: `http://localhost:8080/v1`

## Project Structure

```
gemini-web-proxy/
├── server.py          # Main proxy server
├── run.py            # Startup script
├── requirements.txt  # Python dependencies
├── README.md        # This file
└── .gitignore       # Git ignore rules
```

## Technical Details

### Architecture

```
OpenCode → HTTP Request → Gemini Proxy → Browser Automation → Gemini Web
                                      ← Response Processing ←
```

### Session Management

- Sessions are stored in `~/.gemini-service/`
- Chrome profile data persists between runs
- Login state is maintained automatically

### Tool Calling

The proxy includes advanced prompt engineering to ensure Gemini properly formats tool calls for OpenCode compatibility.

## Limitations

- Requires active internet connection
- Dependent on Gemini web interface stability
- May need updates if Gemini changes their interface
- Rate limited by Gemini's web interface limits

## Contributing

This is an educational project. Feel free to fork and experiment, but please respect Google's terms of service.

## License

MIT License - See LICENSE file for details

## Disclaimer

This project is for educational purposes only. Users are responsible for complying with Google's terms of service. The authors are not responsible for any misuse or violations.
