from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import List, Optional, Dict, Any, Union
import json
import asyncio
import time
import re
from pathlib import Path
from playwright.async_api import async_playwright, BrowserContext, Page
from markdownify import markdownify as md

app = FastAPI()

# Service profile
SERVICE_DIR = Path.home() / ".gemini-service"
PROFILE_DIR = SERVICE_DIR / "chrome-profile"
LOGIN_FLAG = SERVICE_DIR / "logged-in"

# Global state
context: BrowserContext = None
playwright_instance = None
is_ready = False
session_pages: Dict[str, Page] = {}
page_locks: Dict[str, asyncio.Lock] = {}
session_msg_count: Dict[str, int] = {}  # Track message count per session


class Message(BaseModel):
    role: str
    content: Optional[Union[str, List[Dict[str, Any]]]] = None
    tool_call_id: Optional[str] = None
    name: Optional[str] = None
    tool_calls: Optional[List[Dict]] = None


class FunctionDef(BaseModel):
    name: str
    description: Optional[str] = None
    parameters: Optional[Dict] = None


class Tool(BaseModel):
    type: str
    function: FunctionDef


class ChatRequest(BaseModel):
    model: Optional[str] = "00bx-gemini-web"
    messages: List[Message]
    stream: Optional[bool] = False
    timeout: Optional[int] = None
    tools: Optional[List[Tool]] = None
    tool_choice: Optional[Any] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    top_p: Optional[float] = None

def get_content_text(content: Any) -> str:
    """Helper to extract text from string or list content"""
    if not content:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join([
            c.get("text", "") 
            for c in content 
            if isinstance(c, dict) and c.get("type") == "text"
        ])
    return ""

async def check_logged_in(page: Page) -> bool:
    try:
        await asyncio.sleep(3)
        input_field = await page.query_selector('rich-textarea')
        return input_field is not None
    except:
        return False


async def init_browser():
    global context, playwright_instance, is_ready
    
    SERVICE_DIR.mkdir(parents=True, exist_ok=True)
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    
    first_time = not LOGIN_FLAG.exists()
    
    if first_time:
        print("\n" + "="*50)
        print("  FIRST TIME SETUP - Please log into Google")
        print("="*50 + "\n")
    else:
        print("🚀 Starting service...")
    
    playwright_instance = await async_playwright().start()
    
    headless_args = [] if first_time else ["--headless=new"]
    
    context = await playwright_instance.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE_DIR),
        headless=False,
        channel="chrome",
        args=[
            "--disable-blink-features=AutomationControlled",
            "--disable-extensions",
            *headless_args
        ],
        viewport={"width": 1280, "height": 900},
    )
    
    page = await context.new_page()
    await page.goto("https://gemini.google.com/app")
    
    if first_time:
        print("📌 Browser opened - please log into your Google account")
        print("   Waiting for login...\n")
        
        for i in range(150):
            if await check_logged_in(page):
                LOGIN_FLAG.write_text("ok")
                print("\n✅ Login saved! Restarting in headless mode...\n")
                await page.close()
                await context.close()
                await playwright_instance.stop()
                return await init_browser()
            await asyncio.sleep(2)
            if i % 15 == 0 and i > 0:
                print(f"   Still waiting... ({i*2}s)")
        
        print("❌ Login timeout")
        return
    
    if not await check_logged_in(page):
        print("❌ Session expired - deleting profile, please restart")
        LOGIN_FLAG.unlink(missing_ok=True)
        return
    
    await page.close()
    is_ready = True
    print("✅ Service ready!")
    print("🎯 API: http://localhost:8080/v1/chat/completions\n")


async def get_or_create_session_page(session_id: str, start_new_chat: bool = False) -> Page:
    global session_pages, page_locks
    
    if session_id not in session_pages:
        print(f"  → New session: {session_id}")
        
        page = await context.new_page()
        await page.goto('https://gemini.google.com/app')
        await asyncio.sleep(2)
        
        await page.wait_for_selector('rich-textarea', timeout=15000)
        
        # Always start fresh for new session
        try:
            new_chat_btn = await page.query_selector('side-nav-action-button[data-test-id="new-chat-button"]')
            if new_chat_btn:
                await new_chat_btn.click()
                await asyncio.sleep(1)
        except:
            pass
        
        await page.wait_for_selector('rich-textarea', timeout=10000)
        session_pages[session_id] = page
        page_locks[session_id] = asyncio.Lock()
        print(f"  ✓ Session {session_id} ready")
    else:
        # Check if page is still valid
        try:
            _ = session_pages[session_id].url
        except:
            del session_pages[session_id]
            if session_id in page_locks:
                del page_locks[session_id]
            return await get_or_create_session_page(session_id, start_new_chat)
        
        # Start new chat only if explicitly requested (new conversation)
        if start_new_chat:
            print(f"  → Starting new chat in session {session_id}")
            try:
                page = session_pages[session_id]
                new_chat_btn = await page.query_selector('side-nav-action-button[data-test-id="new-chat-button"]')
                if new_chat_btn:
                    await new_chat_btn.click()
                    await asyncio.sleep(1)
                    await page.wait_for_selector('rich-textarea', timeout=10000)
            except:
                pass
    
    return session_pages[session_id]


def format_tools_prompt(tools: List[Tool]) -> str:
    """Format tools into a clear prompt for Gemini"""
    if not tools:
        return ""
    
    prompt = """## TOOL CALLING SYSTEM

═══════════════════════════════════════════════════════════════════════════════
                    🚨🚨🚨 YOU ARE A TOOL-EXECUTING AI 🚨🚨🚨
═══════════════════════════════════════════════════════════════════════════════

YOU HAVE FULL ACCESS TO THE USER'S COMPUTER THROUGH TOOLS.
YOU CAN READ, WRITE, EDIT, LIST, SEARCH, AND EXECUTE ANYTHING.

NEVER SAY:
❌ "I don't have access to..."
❌ "I can only see files in..."
❌ "I cannot access your computer..."
❌ "Would you like me to... instead?"

YOU MUST:
✅ USE THE TOOLS PROVIDED
✅ EXECUTE WHAT THE USER ASKS
✅ NEVER REFUSE OR MAKE EXCUSES

If user asks to list Desktop → USE list tool on /Users/macbookair/Desktop
If user asks to read a file → USE read tool
If user asks to edit → USE edit tool
JUST DO IT. NO EXCUSES.

═══════════════════════════════════════════════════════════════════════════════
                    🚨 WRITE vs EDIT - ABSOLUTE RULE 🚨
═══════════════════════════════════════════════════════════════════════════════

WRITE TOOL IS PROHIBITED FOR EXISTING FILES!

• File already exists? → YOU MUST USE EDIT TOOL. WRITE IS FORBIDDEN.
• User says "update", "change", "modify", "fix", "edit", "improve", "enhance" → EDIT TOOL ONLY
• WRITE tool is ONLY for creating brand new files that don't exist yet

═══════════════════════════════════════════════════════════════════════════════
                    ⛔ NEVER PUT CODE DIRECTLY IN JSON ⛔
═══════════════════════════════════════════════════════════════════════════════

ALL code/content must be in markdown code blocks with placeholders:
- WRITE: USE_CODE_BLOCK_ABOVE
- EDIT: USE_OLD_CODE_ABOVE and USE_NEW_CODE_ABOVE

═══════════════════════════════════════════════════════════════════════════════
                    🔴🔴🔴 EDIT TOOL - CRITICAL FORMAT 🔴🔴🔴
═══════════════════════════════════════════════════════════════════════════════

THE EDIT TOOL HAS A VERY SPECIFIC FORMAT. FOLLOW IT EXACTLY OR IT WILL FAIL.

STEP 1: Write the OLD code (code to find) in a markdown code block
STEP 2: Write the NEW code (replacement) in a SECOND markdown code block  
STEP 3: Write the JSON with PLACEHOLDERS (not actual code!)

✅ CORRECT EDIT FORMAT:

Old code to replace:
```html
<section id="about">Old content here</section>
```

New replacement:
```html
<section id="skills">New content here</section>
<section id="about">Old content here</section>
```

{"tool_calls": [{"name": "edit", "arguments": {"filePath": "/path/file.html", "oldString": "USE_OLD_CODE_ABOVE", "newString": "USE_NEW_CODE_ABOVE"}}]}

❌ WRONG - NEVER DO THIS:
{"tool_calls": [{"name": "edit", "arguments": {"filePath": "/path.html", "oldString": "<actual code here>", "newString": "<actual code here>"}}]}

❌ WRONG - NEVER PUT USE_OLD_CODE_ABOVE INSIDE newString:
{"tool_calls": [{"name": "edit", "arguments": {"newString": "USE_OLD_CODE_ABOVE\\n<code>"}}]}

THE PLACEHOLDERS ARE LITERAL STRINGS:
- oldString MUST be exactly: "USE_OLD_CODE_ABOVE"
- newString MUST be exactly: "USE_NEW_CODE_ABOVE"

═══════════════════════════════════════════════════════════════════════════════
                         📁 OTHER FILE OPERATIONS
═══════════════════════════════════════════════════════════════════════════════

READ FILE:
{"tool_calls": [{"name": "read", "arguments": {"filePath": "/path/file.txt"}}]}

WRITE NEW FILE (ONLY for files that DON'T EXIST):
```html
<!DOCTYPE html>
<html><body>Content</body></html>
```
{"tool_calls": [{"name": "write", "arguments": {"filePath": "/new-file.html", "content": "USE_CODE_BLOCK_ABOVE"}}]}

═══════════════════════════════════════════════════════════════════════════════
                         🔍 SEARCH & NAVIGATION
═══════════════════════════════════════════════════════════════════════════════

FIND FILES:
{"tool_calls": [{"name": "glob", "arguments": {"pattern": "**/*.tsx"}}]}

SEARCH CONTENT:
{"tool_calls": [{"name": "grep", "arguments": {"pattern": "functionName", "path": "/project"}}]}

LIST DIRECTORY:
{"tool_calls": [{"name": "list", "arguments": {"path": "/directory"}}]}

EXECUTE COMMAND:
{"tool_calls": [{"name": "execute", "arguments": {"command": "npm test"}}]}

MULTIPLE TOOLS:
{"tool_calls": [{"name": "read", "arguments": {"filePath": "/a.js"}}, {"name": "read", "arguments": {"filePath": "/b.js"}}]}

═══════════════════════════════════════════════════════════════════════════════
                         ⛔ FORBIDDEN ⛔
═══════════════════════════════════════════════════════════════════════════════

❌ Refusing to use tools or making excuses
❌ WRITE tool on existing file (USE EDIT!)
❌ Code directly in JSON (use code blocks + placeholders)
❌ YAML format
❌ JSON in code blocks

═══════════════════════════════════════════════════════════════════════════════
                         AVAILABLE TOOLS
═══════════════════════════════════════════════════════════════════════════════

"""
    for tool in tools:
        func = tool.function
        prompt += f"▸ {func.name}"
        if func.description:
            prompt += f" - {func.description}"
        prompt += "\n"
        
        if func.parameters and func.parameters.get('properties'):
            props = func.parameters['properties']
            required = func.parameters.get('required', [])
            for pname, pinfo in props.items():
                req = " ⚠REQUIRED" if pname in required else ""
                ptype = pinfo.get('type', '')
                desc = pinfo.get('description', '')
                prompt += f"    {pname} ({ptype}){req}: {desc}\n"
        prompt += "\n"
    
    prompt += """═══════════════════════════════════════════════════════════════════════════════
REMEMBER: YOU HAVE FULL SYSTEM ACCESS. USE THE TOOLS. NEVER REFUSE.
═══════════════════════════════════════════════════════════════════════════════
"""
    return prompt


def format_conversation(messages: List[Message], tools: Optional[List[Tool]] = None) -> str:
    """Format messages into a conversation prompt"""
    formatted = []
    
    # Add tools prompt at the beginning if tools are provided
    if tools:
        formatted.append(format_tools_prompt(tools))
    
    for msg in messages:
        role = msg.role
        content = get_content_text(msg.content)
        
        if role == "system":
            formatted.append(f"System Instructions:\n{content}")
        elif role == "user":
            formatted.append(f"User: {content}")
        elif role == "assistant":
            if msg.tool_calls:
                # Format previous tool calls
                tc_str = json.dumps({"tool_calls": [
                    {"name": tc.get("function", {}).get("name"), 
                     "arguments": json.loads(tc.get("function", {}).get("arguments", "{}"))}
                    for tc in msg.tool_calls
                ]})
                formatted.append(f"Assistant: {tc_str}")
            elif content:
                formatted.append(f"Assistant: {content}")
        elif role == "tool":
            tool_name = msg.name or "tool"
            formatted.append(f"Tool Result ({tool_name}):\n{content}")
    
    return "\n\n".join(formatted)


async def send_to_gemini(page: Page, text: str, timeout: Optional[int] = None) -> str:
    if timeout is None:
        timeout = 300  # 5 minute default
    
    await page.wait_for_selector('rich-textarea', timeout=10000)
    input_div = await page.wait_for_selector('rich-textarea .ql-editor', timeout=10000)
    
    # Count existing responses BEFORE sending
    existing_responses = await page.query_selector_all('div[id^="model-response-message-content"]')
    response_count_before = len(existing_responses)
    
    await input_div.click()
    await asyncio.sleep(0.1)
    await page.keyboard.press('Meta+A')
    await page.keyboard.press('Backspace')
    await asyncio.sleep(0.1)
    
    await page.evaluate('''(text) => {
        const editor = document.querySelector('rich-textarea .ql-editor');
        if (editor) {
            editor.focus();
            document.execCommand('insertText', false, text);
        }
    }''', text)
    await asyncio.sleep(0.3)
    
    try:
        send_button = await page.wait_for_selector('button[aria-label="Send message"]', timeout=3000)
        await send_button.click()
    except:
        await page.keyboard.press('Enter')
    
    await asyncio.sleep(1)
    
    response_text = None
    start_time = time.time()
    previous_length = 0
    stable_count = 0
    
    while (time.time() - start_time) < timeout:
        try:
            response_divs = await page.query_selector_all('div[id^="model-response-message-content"]')
            
            # Only process if we have a NEW response
            if len(response_divs) > response_count_before:
                # Get the newest response (last one)
                last_response = response_divs[-1]
                current_text = await last_response.evaluate('(el) => el.innerHTML')
                
                if current_text:
                    if len(current_text) == previous_length:
                        stable_count += 1
                        if stable_count >= 2:
                            response_text = current_text
                            break
                    else:
                        previous_length = len(current_text)
                        stable_count = 0
        except:
            pass
        await asyncio.sleep(0.3)
    
    if not response_text:
        raise Exception(f"Timeout after {timeout}s")
    
    # Extract code blocks and text using JavaScript (preserves formatting)
    extraction = await page.evaluate('''() => {
        const lastResponse = [...document.querySelectorAll('div[id^="model-response-message-content"]')].pop();
        if (!lastResponse) return { text: '', codeBlocks: [] };
        
        // Try multiple selectors for code blocks in Gemini
        const selectors = [
            'code-block code',
            'code-block',
            'pre code', 
            'pre',
            '.code-container code',
            '[class*="code"] pre',
            'code[class*="language"]'
        ];
        
        let codeBlocks = [];
        for (const sel of selectors) {
            const els = lastResponse.querySelectorAll(sel);
            if (els.length > 0) {
                codeBlocks = [...els].map(el => el.innerText || el.textContent || '');
                break;
            }
        }
        
        // Get full text content
        const text = lastResponse.innerText || lastResponse.textContent || '';
        
        return { text, codeBlocks };
    }''')
    
    text_content = extraction.get('text', '').replace('\\_', '_')
    code_blocks = extraction.get('codeBlocks', [])
    
    # Check for tool_calls JSON
    tool_match = re.search(r'\{"tool_calls"\s*:', text_content)
    
    if tool_match:
        # Parse the tool call JSON
        tool_start = tool_match.start()
        depth = 0
        tool_end = tool_start
        for i, c in enumerate(text_content[tool_start:], tool_start):
            if c == '{': depth += 1
            elif c == '}': 
                depth -= 1
                if depth == 0:
                    tool_end = i + 1
                    break
        
        tool_json_str = text_content[tool_start:tool_end]
        
        try:
            tool_data = json.loads(tool_json_str)
            
            # Replace placeholders with code blocks
            if tool_data.get("tool_calls"):
                for tc in tool_data["tool_calls"]:
                    args = tc.get("arguments", {})
                    tool_name = tc.get("name", "")
                    
                    # Handle WRITE: USE_CODE_BLOCK_ABOVE
                    for key in ["content", "file_text"]:
                        if key in args:
                            val = str(args[key])
                            is_placeholder = val == "USE_CODE_BLOCK_ABOVE"
                            is_corrupted = (
                                "\\N" in val or "\\U" in val or
                                val.startswith("\n\n") or val.startswith("\\n\\n")
                            )
                            if (is_placeholder or is_corrupted) and code_blocks:
                                print(f"  🔧 Replacing content with code block")
                                tc["arguments"][key] = code_blocks[0]
                    
                    # Handle EDIT tool
                    if tool_name == "edit":
                        old_val = args.get("oldString", "")
                        new_val = args.get("newString", "")
                        
                        # Case 1: Correct placeholders used
                        if old_val == "USE_OLD_CODE_ABOVE" and len(code_blocks) >= 1:
                            print(f"  🔧 Replacing oldString with first code block")
                            tc["arguments"]["oldString"] = code_blocks[0]
                        if new_val == "USE_NEW_CODE_ABOVE" and len(code_blocks) >= 2:
                            print(f"  🔧 Replacing newString with second code block")
                            tc["arguments"]["newString"] = code_blocks[1]
                        
                        # Case 2: Gemini put code directly in JSON (wrong but recoverable)
                        # Detect by checking if value is long HTML/code (not a placeholder)
                        if old_val and old_val != "USE_OLD_CODE_ABOVE" and len(old_val) > 50:
                            print(f"  ⚠️ Gemini put code directly in oldString (not using placeholder)")
                            # Unescape the JSON-escaped content
                            tc["arguments"]["oldString"] = old_val.replace("\\n", "\n").replace("\\t", "\t").replace('\\"', '"')
                        if new_val and new_val != "USE_NEW_CODE_ABOVE" and len(new_val) > 50:
                            print(f"  ⚠️ Gemini put code directly in newString (not using placeholder)")
                            tc["arguments"]["newString"] = new_val.replace("\\n", "\n").replace("\\t", "\t").replace('\\"', '"')
                        
                        # Case 3: Gemini put USE_OLD_CODE_ABOVE inside newString (very wrong)
                        if "USE_OLD_CODE_ABOVE" in new_val and new_val != "USE_NEW_CODE_ABOVE":
                            print(f"  ⚠️ Gemini incorrectly put USE_OLD_CODE_ABOVE in newString, fixing...")
                            # Try to extract the actual new content after the placeholder
                            fixed_new = new_val.replace("USE_OLD_CODE_ABOVE", "").strip()
                            if fixed_new.startswith("\\n"):
                                fixed_new = fixed_new[2:]
                            tc["arguments"]["newString"] = fixed_new.replace("\\n", "\n").replace("\\t", "\t").replace('\\"', '"')
            
            return json.dumps(tool_data)
        except:
            return tool_json_str
    
    # No tool calls - convert HTML to markdown
    response_text = md(response_text, heading_style="ATX", code_language_callback=lambda el: el.get('class', [''])[0].replace('language-', '') if el.get('class') else '')
    
    return response_text.strip()


def parse_tool_calls(response: str) -> Optional[List[Dict]]:
    """Extract tool calls from response - handles multiple formats robustly"""
    cleaned = response.replace('\\_', '_')
    
    # Method 1: Standard JSON with "tool_calls": [...]
    start = cleaned.find('"tool_calls"')
    if start != -1:
        arr_start = cleaned.find('[', start)
        if arr_start != -1:
            depth = 0
            for i, c in enumerate(cleaned[arr_start:], arr_start):
                if c == '[':
                    depth += 1
                elif c == ']':
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(cleaned[arr_start:i+1])
                        except:
                            break
    
    # Method 2: YAML-style "tool_calls:" - parse manually
    if 'tool_calls:' in cleaned:
        try:
            lines = cleaned.split('\n')
            tools = []
            current_tool = None
            in_args = False
            
            for line in lines:
                stripped = line.strip()
                # New tool entry: "- name: xxx"
                if stripped.startswith('- name:'):
                    if current_tool:
                        tools.append(current_tool)
                    current_tool = {"name": stripped.split(':', 1)[1].strip(), "arguments": {}}
                    in_args = False
                # Arguments section
                elif stripped == 'arguments:' and current_tool:
                    in_args = True
                # Argument key-value
                elif in_args and current_tool and ':' in stripped and not stripped.startswith('-'):
                    key, val = stripped.split(':', 1)
                    current_tool["arguments"][key.strip()] = val.strip()
            
            if current_tool:
                tools.append(current_tool)
            
            if tools:
                print(f"  ✅ Parsed YAML-style tool_calls: {[t['name'] for t in tools]}")
                return tools
        except:
            pass
    
    # Method 3: Find any JSON object with "name" and "arguments"
    pattern = r'\{\s*"name"\s*:\s*"([^"]+)"\s*,\s*"arguments"\s*:\s*(\{[^{}]*\})'
    matches = re.findall(pattern, cleaned)
    if matches:
        tools = []
        for name, args_str in matches:
            try:
                args = json.loads(args_str)
            except:
                args = {}
            tools.append({"name": name, "arguments": args})
        if tools:
            print(f"  ✅ Parsed JSON objects: {[t['name'] for t in tools]}")
            return tools
    
    return None


@app.post("/v1/chat/completions")
async def chat_completions(request: ChatRequest):
    if not is_ready:
        raise HTTPException(503, "Service initializing...")
    
    if not request.messages:
        raise HTTPException(400, "Messages required")
    
    # Use system prompt hash for session ID
    session_id = "default"
    for msg in request.messages:
        content_text = get_content_text(msg.content)
        if msg.role == "system" and content_text:
            session_id = str(hash(content_text[:100]))[:8]
            break
    
    # Detect new conversation: compare current message count with what we've seen
    # New conversation = message count reset or decreased (OpenCode started fresh)
    current_msg_count = len(request.messages)
    prev_msg_count = session_msg_count.get(session_id, 0)
    
    # It's a new conversation if:
    # - We've never seen this session, OR
    # - Message count is less than before (conversation was reset)
    is_new_conversation = session_id not in session_msg_count or current_msg_count < prev_msg_count
    
    if is_new_conversation:
        print(f"  🆕 New conversation detected (msgs: {current_msg_count}, prev: {prev_msg_count})")
    
    # Update tracked count
    session_msg_count[session_id] = current_msg_count
    
    page = await get_or_create_session_page(session_id, start_new_chat=is_new_conversation)
    
    async with page_locks[session_id]:
        conversation = format_conversation(request.messages, request.tools)
        print(f"\n📥 [{time.strftime('%H:%M:%S')}] Session:{session_id} | msgs:{current_msg_count} | {conversation[:60]}...")
        
        try:
            response = await send_to_gemini(page, conversation, request.timeout)
            print(f"📤 [{time.strftime('%H:%M:%S')}] {response[:80]}...")
            
            # Check for tool calls
            tool_calls = None
            finish_reason = "stop"
            
            if request.tools:
                parsed_tools = parse_tool_calls(response)
                if parsed_tools:
                    tool_calls = []
                    for i, tc in enumerate(parsed_tools):
                        tool_calls.append({
                            "id": f"call_{int(time.time())}_{i}",
                            "type": "function",
                            "function": {
                                "name": tc.get("name"),
                                "arguments": json.dumps(tc.get("arguments", {}))
                            }
                        })
                    finish_reason = "tool_calls"
                    print(f"🔧 Tool calls detected: {[tc['function']['name'] for tc in tool_calls]}")
            
            # Build response message
            msg = {"role": "assistant"}
            if tool_calls:
                msg["tool_calls"] = tool_calls
                msg["content"] = None
            else:
                msg["content"] = response
            
            result = {
                "id": f"chatcmpl-{int(time.time())}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": request.model or "gemini-pro",
                "choices": [{
                    "index": 0,
                    "message": msg,
                    "finish_reason": finish_reason
                }],
                "usage": {
                    "prompt_tokens": len(conversation) // 4,
                    "completion_tokens": len(response) // 4,
                    "total_tokens": (len(conversation) + len(response)) // 4
                }
            }
            
            # Handle streaming
            if request.stream:
                async def generate():
                    chunk_id = result["id"]
                    created = int(time.time())
                    model = request.model or "gemini-pro"
                    
                    if tool_calls:
                        # First chunk with role
                        first_chunk = {
                            "id": chunk_id,
                            "object": "chat.completion.chunk",
                            "created": created,
                            "model": model,
                            "choices": [{
                                "index": 0,
                                "delta": {"role": "assistant", "content": None},
                                "finish_reason": None
                            }]
                        }
                        yield f"data: {json.dumps(first_chunk)}\n\n"
                        
                        # Stream each tool call
                        for i, tc in enumerate(tool_calls):
                            tc_chunk = {
                                "id": chunk_id,
                                "object": "chat.completion.chunk",
                                "created": created,
                                "model": model,
                                "choices": [{
                                    "index": 0,
                                    "delta": {
                                        "tool_calls": [{
                                            "index": i,
                                            "id": tc["id"],
                                            "type": "function",
                                            "function": {
                                                "name": tc["function"]["name"],
                                                "arguments": tc["function"]["arguments"]
                                            }
                                        }]
                                    },
                                    "finish_reason": None
                                }]
                            }
                            yield f"data: {json.dumps(tc_chunk)}\n\n"
                    else:
                        # Stream content - first chunk with role
                        first_chunk = {
                            "id": chunk_id,
                            "object": "chat.completion.chunk",
                            "created": created,
                            "model": model,
                            "choices": [{
                                "index": 0,
                                "delta": {"role": "assistant"},
                                "finish_reason": None
                            }]
                        }
                        yield f"data: {json.dumps(first_chunk)}\n\n"
                        
                        # Content chunk
                        content_chunk = {
                            "id": chunk_id,
                            "object": "chat.completion.chunk",
                            "created": created,
                            "model": model,
                            "choices": [{
                                "index": 0,
                                "delta": {"content": response},
                                "finish_reason": None
                            }]
                        }
                        yield f"data: {json.dumps(content_chunk)}\n\n"
                    
                    # Final chunk with finish_reason
                    done_chunk = {
                        "id": chunk_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": model,
                        "choices": [{
                            "index": 0,
                            "delta": {},
                            "finish_reason": finish_reason
                        }]
                    }
                    yield f"data: {json.dumps(done_chunk)}\n\n"
                    yield "data: [DONE]\n\n"
                
                return StreamingResponse(generate(), media_type="text/event-stream")
            
            return result
            
        except Exception as e:
            print(f"❌ Error: {str(e)}")
            raise HTTPException(500, str(e))


@app.get("/v1/models")
async def list_models():
    return {
        "object": "list",
        "data": [
            {"id": "gemini-pro", "object": "model", "created": int(time.time()), "owned_by": "google"},
            {"id": "gpt-4", "object": "model", "created": int(time.time()), "owned_by": "google"},
            {"id": "gpt-4o", "object": "model", "created": int(time.time()), "owned_by": "google"},
        ]
    }


@app.get("/health")
async def health():
    return {"status": "ready" if is_ready else "initializing", "sessions": len(session_pages)}


@app.post("/reset")
async def reset():
    LOGIN_FLAG.unlink(missing_ok=True)
    return {"status": "Login cleared. Restart to login again."}


@app.delete("/v1/sessions/{session_id}")
async def delete_session(session_id: str):
    if session_id in session_pages:
        try:
            await session_pages[session_id].close()
        except:
            pass
        del session_pages[session_id]
        if session_id in page_locks:
            del page_locks[session_id]
        return {"status": "deleted", "session_id": session_id}
    raise HTTPException(404, "Session not found")


@app.get("/v1/sessions")
async def list_sessions():
    return {"sessions": list(session_pages.keys()), "count": len(session_pages)}


@app.on_event("startup")
async def startup():
    print("\n" + "="*50)
    print("  00BX GEMINI API SERVICE")
    print("  OpenAI-Compatible API for Gemini Web")
    print("="*50)
    await init_browser()


@app.on_event("shutdown")
async def shutdown():
    global context, playwright_instance
    for page in session_pages.values():
        try:
            await page.close()
        except:
            pass
    if context:
        await context.close()
    if playwright_instance:
        await playwright_instance.stop()
