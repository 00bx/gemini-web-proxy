#!/bin/bash

echo "Setting up Gemini Web Proxy for OpenCode..."
echo "=========================================="

# Check Python version
python_version=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
if [[ $(echo "$python_version < 3.8" | bc -l) -eq 1 ]]; then
    echo "Error: Python 3.8 or higher is required"
    exit 1
fi

echo "✓ Python version check passed"

# Install Python dependencies
echo "Installing Python dependencies..."
pip3 install -r requirements.txt

# Install Playwright browsers
echo "Installing Playwright browsers..."
playwright install chromium

echo ""
echo "Setup complete!"
echo ""
echo "Next steps:"
echo "1. Run: python3 run.py"
echo "2. Log in to your Google account when browser opens"
echo "3. Configure OpenCode with the provider settings from README.md"
echo ""
echo "For detailed instructions, see README.md"
