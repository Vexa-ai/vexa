# Vexa MCP Server Setup Guide

This guide will help you set up and run the Vexa MCP (Model Context Protocol) Server to enable transcription capabilities in Claude Desktop (or any MCP client of your choice)!

## Prerequisites

- Claude Desktop installed on your system
- A Vexa API key

## 🚀 Quick Setup

### Step 1: Install the MCP Server File

Download or clone the `vexa-mcp.py` file to your desired directory on your machine.

### Step 2: Install UV (Recommended)

UV is a fast Python package installer and resolver. Choose your platform:

**Windows (PowerShell):**
```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

**macOS/Linux:**
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Learn more about [uv](https://docs.astral.sh/uv/)

### Step 3: Install Dependencies

Choose one of the following methods:

**Option A: Using UV (Recommended)**
```bash
uv add "mcp[cli]" requests
```

**Option B: Using pip**
```bash
pip install "mcp[cli]" requests
```

### Step 4: Configure Claude Desktop

1. **Open Claude Desktop Settings**
   - Launch Claude Desktop
   - Navigate to **Settings** → **Developer**
   - Click **Edit Config**

2. **Add MCP Server Configuration**
   
   Paste the following configuration into the config file, replacing the placeholder values:

   ```json
   {
     "mcpServers": {
       "Vexa-MCP": {
         "command": "uv",
         "args": [
           "--directory",
           "[full_path_to_directory_with_vexa-mcp.py_file]",
           "run",
           "vexa-mcp.py"
         ],
         "env": {
           "VEXA_API_KEY": "[your_api_key]"
         }
       }
     }
   }
   ```

3. **Update Configuration Values**
   - Replace `[full_path_to_directory_with_vexa-mcp.py_file]` with the actual path to your directory
   - Replace `[your_api_key]` with your Vexa API key

4. **Save and Restart**
   - Save the configuration file
   - Restart Claude Desktop

   Everything should work well now. You can use the same config in other MCP clients such as Cursor etc..

## ✅ Verification

After restarting Claude Desktop, the Vexa MCP server should be available. You can verify the setup by checking if transcription capabilities are working in your Claude conversations.

## 🔧 Troubleshooting

- **API Key Issues**: Verify your Vexa API key is valid and properly set in the environment variables
- **Path Issues**: Make sure to use the full absolute path to the directory containing `vexa-mcp.py`
- **UV Issues**: Make sure uv is properly installed by running `uv --version`. Restart if issue persists

## Additional Resources
- [MCP Protocol Documentation](https://modelcontextprotocol.io/)

---

**Need help?** Contact us directly